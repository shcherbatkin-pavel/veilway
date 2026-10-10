"""Synthetic CA, durable receipts and the outbound-only installer."""
import base64
from datetime import timedelta
import hashlib
import importlib.util
from pathlib import Path
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from test_api import build_client, login
from test_profile_integration import real_pki, db_factory
from test_migrations import postgres_connection
from test_profiles import payload, user_client
from veilway_control.config import Settings
from veilway_control.crl import CrlWorker
from veilway_control.models import CrlAgent, CrlPublication, CrlSyncState, User, VpnProfile, utcnow
from veilway_control.profile_worker import ProfileWorker
from veilway_control.security import hash_token


def agent_module():
    path = Path('/app/crl_agent.py')
    if not path.exists():
        path = Path(__file__).resolve().parents[3] / 'deploy/roles/veilway_crl_agent/files/veilway-crl-agent'
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader('test_crl_agent', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def synthetic():
    now = utcnow().replace(microsecond=0)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'Synthetic CRL test CA')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=10))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
          .sign(key, hashes.SHA256()))
    def crl(version, *, expired=False, serials=(), wrong_key=False):
        builder = (x509.CertificateRevocationListBuilder().issuer_name(name).last_update(now - timedelta(hours=1))
                   .next_update(now - timedelta(seconds=1) if expired else now + timedelta(days=7))
                   .add_extension(x509.CRLNumber(version), False))
        for serial in serials:
            builder = builder.add_revoked_certificate(x509.RevokedCertificateBuilder().serial_number(serial)
                      .revocation_date(now - timedelta(minutes=1)).build())
        return builder.sign(ec.generate_private_key(ec.SECP256R1()) if wrong_key else key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)
    return ca.public_bytes(serialization.Encoding.PEM), crl


def bundle(pem):
    version = x509.load_pem_x509_crl(pem).extensions.get_extension_for_class(x509.CRLNumber).value.crl_number
    return {'version': version, 'sha256': hashlib.sha256(pem).hexdigest(), 'crl_base64': base64.b64encode(pem).decode()}


def test_installer_atomic_replay_and_rejects_untrusted_expired_rollback_or_incomplete(tmp_path):
    agent = agent_module()
    ca, crl = synthetic()
    initial = crl(1, serials=(10,))
    path = tmp_path / 'crl.pem'
    path.write_bytes(initial)
    inode = path.stat().st_ino
    latest = crl(2, serials=(10, 11))
    installed = agent.install(tmp_path, ca, bundle(latest))
    assert installed == {'version': 2, 'sha256': hashlib.sha256(latest).hexdigest()}
    assert path.stat().st_ino != inode and path.stat().st_mode & 0o777 == 0o640
    inode = path.stat().st_ino
    assert agent.install(tmp_path, ca, bundle(latest)) == installed
    assert path.stat().st_ino == inode
    for invalid in (initial, crl(2, serials=(10, 11)), crl(3, expired=True, serials=(10, 11)),
                    crl(3, serials=(10, 11), wrong_key=True), crl(3, serials=(11,))):
        with pytest.raises(agent.Rejected):
            agent.install(tmp_path, ca, bundle(invalid))
        assert path.read_bytes() == latest
    assert list(tmp_path.iterdir()) == [path]


def test_missing_or_symlink_target_never_acknowledged(tmp_path):
    agent = agent_module()
    ca, crl = synthetic()
    with pytest.raises(agent.Rejected):
        agent.install(tmp_path, ca, bundle(crl(1)))
    outside = tmp_path / 'outside'
    outside.write_bytes(crl(1))
    (tmp_path / 'crl.pem').symlink_to(outside)
    with pytest.raises(agent.Rejected):
        agent.install(tmp_path, ca, bundle(crl(2)))
    assert (tmp_path / 'crl.pem').is_symlink()


def test_lost_ack_retries_same_installed_version_and_reports_safe_error(tmp_path):
    agent = agent_module()
    ca, crl = synthetic()
    (tmp_path / 'crl.pem').write_bytes(crl(1))
    latest = crl(2)
    class Transport:
        fail = True
        receipts = []
        def request(self, operation, payload=None):
            if operation == 'bundle':
                return bundle(latest)
            if operation == 'receipt':
                self.receipts.append(payload)
                if self.fail:
                    raise agent.Rejected('transport_unavailable')
    transport = Transport()
    assert agent.step(transport, tmp_path, ca) == 'transport_unavailable'
    inode = (tmp_path / 'crl.pem').stat().st_ino
    transport.fail = False
    assert agent.step(transport, tmp_path, ca) is None
    assert len(transport.receipts) == 2 and transport.receipts[0] == transport.receipts[1]
    assert (tmp_path / 'crl.pem').stat().st_ino == inode


def test_publisher_rejects_rollback_equivocation_and_incomplete_crl(db_factory):
    ca, crl = synthetic()
    class Client:
        def crl(self):
            return self.pem, ca
    client = Client()
    client.pem = crl(2, serials=(10,))
    worker = CrlWorker(Settings(), db_factory, client)
    worker.step()
    for invalid in (crl(1, serials=(10,)), crl(2, serials=(10,)), crl(3), crl(3, expired=True), crl(3, wrong_key=True)):
        client.pem = invalid
        worker.step()
        with db_factory() as db:
            assert db.scalar(select(CrlPublication.version)) == 2
            assert db.get(CrlSyncState, 1).error_code == 'publication_unavailable'
    client.pem = crl(3, serials=(10, 11))
    worker.step()
    with db_factory() as db:
        assert db.get(CrlPublication, 3) is not None
        assert db.get(CrlSyncState, 1).error_code is None


def test_both_nodes_receive_complete_crl_only_required_node_finalizes(db_factory, seed_control_data, real_pki, tmp_path):
    pki, store = real_pki
    seed_control_data()
    with db_factory() as db:
        db.add_all([CrlAgent(slug=slug, token_hash=hash_token(token)) for slug, token in
                    [('aws-direct', 'A'*40), ('yc-direct', 'Y'*40)]])
        user = User(role='USER', google_sub='crl-user', email='crl-user@example.test')
        db.add(user); db.commit(); user_id = user.id
    publisher = CrlWorker(Settings(), db_factory, pki)
    worker = ProfileWorker(Settings(), db_factory, pki)
    installer = agent_module()
    ca = (store.current() / 'ca/ca.crt').read_bytes()
    with build_client(db_factory) as admin:
        headers = {'X-CSRF-Token': login(admin)}
        ids = []
        for mode in ('aws-direct', 'yc-direct', 'yc-aws-multihop'):
            created = admin.post('/api/v1/profiles', json=payload(mode=mode, duration_days=3), headers=headers)
            assert created.status_code == 202
            pid = created.json()['profile']['id']; ids.append(pid)
            worker.step()
            assert admin.post(f'/api/v1/profiles/{pid}/revoke', json={'idempotency_key': str(uuid.uuid4())}, headers=headers).status_code == 202
            worker.step()
        # Expiry while waiting for delivery must not cancel an accepted revoke.
        with db_factory() as db:
            for pid in ids:
                row = db.get(VpnProfile, uuid.UUID(pid))
                row.created_at = utcnow() - timedelta(days=2)
                row.expires_at = utcnow() - timedelta(days=1)
            db.commit()
        publisher.step()
        with user_client(db_factory, user_id, pki)[0] as user_client_instance:
            assert user_client_instance.get('/api/v1/crl-delivery').status_code == 403
            assert user_client_instance.get('/api/v1/crl-agents/aws-direct/bundle').status_code == 401
        assert admin.get('/api/v1/crl-agents/aws-direct/bundle').status_code == 401
        assert admin.get('/api/v1/crl-agents/aws-direct/bundle', headers={'Authorization':'Bearer '+'a'*40}).status_code == 401
        aws_headers = {'Authorization':'Bearer '+'A'*40}
        yc_headers = {'Authorization':'Bearer '+'Y'*40}
        assert admin.get('/api/v1/crl-agents/yc-direct/bundle', headers=aws_headers).status_code == 401
        aws = admin.get('/api/v1/crl-agents/aws-direct/bundle', headers=aws_headers)
        yc = admin.get('/api/v1/crl-agents/yc-direct/bundle', headers=yc_headers)
        assert aws.json() == yc.json() and aws.headers['cache-control'] == 'no-store'
        # All historical and newly revoked serials travel to each node.
        crl = x509.load_pem_x509_crl(base64.b64decode(aws.json()['crl_base64']))
        assert len(crl) == 4
        (tmp_path/'crl.pem').write_bytes((store.current()/'crl.pem').read_bytes())
        receipt = installer.install(tmp_path, ca, aws.json())
        assert admin.post('/api/v1/crl-agents/aws-direct/receipt', json={**receipt, 'sha256':'0'*64}, headers=aws_headers).status_code == 409
        publisher.step()
        assert all(admin.get(f'/api/v1/profiles/{pid}').json()['status'] == 'revoking' for pid in ids)
        assert admin.post('/api/v1/crl-agents/aws-direct/receipt', json=receipt, headers=aws_headers).status_code == 204
        publisher.step()
        assert [admin.get(f'/api/v1/profiles/{pid}').json()['status'] for pid in ids] == ['revoked', 'revoking', 'revoking']
        assert admin.post('/api/v1/crl-agents/yc-direct/error', json={'code':'installation_failed'}, headers=yc_headers).status_code == 204
        assert admin.get('/api/v1/crl-delivery').json()['nodes'][1]['error_code'] == 'installation_failed'
        assert admin.post('/api/v1/crl-agents/yc-direct/receipt', json=receipt, headers=yc_headers).status_code == 204
        publisher = CrlWorker(Settings(), db_factory, pki)
        publisher.step()
        assert all(admin.get(f'/api/v1/profiles/{pid}').json()['status'] == 'revoked' for pid in ids)
        for pid in ids:
            repeated = admin.post(f'/api/v1/profiles/{pid}/revoke', json={'idempotency_key': str(uuid.uuid4())}, headers=headers)
            assert repeated.status_code == 202
            assert repeated.json()['status'] == 'succeeded'
        assert admin.get('/api/v1/crl-delivery').json()['nodes'][1]['status'] == 'current'


def test_daily_refresh_retains_revocations_and_is_idempotent(real_pki):
    pki, store = real_pki
    from test_pki import PkiTests
    now = utcnow().replace(microsecond=0)
    current = store.current()
    original = x509.load_pem_x509_crl((current/'crl.pem').read_bytes())
    ca = x509.load_pem_x509_certificate((current/'ca/ca.crt').read_bytes())
    key = serialization.load_pem_private_key((current/'ca/private/ca.key').read_bytes(), PkiTests.password)
    old_number = original.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number
    old = (x509.CertificateRevocationListBuilder().issuer_name(ca.subject)
           .last_update(now-timedelta(days=2)).next_update(now+timedelta(days=5))
           .add_extension(x509.CRLNumber(old_number), False))
    for revoked in original:
        old = old.add_revoked_certificate(revoked)
    (current/'crl.pem').write_bytes(old.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
    pem, exported_ca = pki.crl()
    fresh = x509.load_pem_x509_crl(pem)
    assert exported_ca == ca.public_bytes(serialization.Encoding.PEM)
    assert fresh.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number > old_number
    assert {entry.serial_number for entry in fresh} == {entry.serial_number for entry in original}
    assert fresh.last_update_utc >= now
    assert fresh.next_update_utc <= now+timedelta(days=7, seconds=5)
    assert pki.crl() == (pem, exported_ca)


def test_crl_migration_refuses_destroying_receipts(postgres_connection):
    import sqlalchemy as sa
    from alembic import command
    from test_migrations import config
    cfg = config(postgres_connection)
    command.upgrade(cfg, 'head')
    postgres_connection.execute(sa.text("INSERT INTO crl_agents (slug, token_hash) VALUES ('aws-direct', :digest)"), {'digest': hash_token('synthetic-token'*3)})
    postgres_connection.commit()
    with pytest.raises(RuntimeError, match='operator-preserving migration'):
        command.downgrade(cfg, '0004_profile_api')
    postgres_connection.rollback()
    assert postgres_connection.scalar(sa.text('SELECT count(*) FROM crl_agents')) == 1
    assert postgres_connection.scalar(sa.text('SELECT version_num FROM alembic_version')) == '0006_legacy_profiles'


def test_crl_token_sync_rotation_and_heartbeat_separation(db_factory, seed_control_data, monkeypatch):
    import io
    import json
    from veilway_control import cli
    seed_control_data()
    monkeypatch.setattr(cli, 'get_session_factory', lambda: db_factory)
    def sync(aws, yc):
        monkeypatch.setattr(cli.sys, 'stdin', io.StringIO(json.dumps({'aws-direct': aws, 'yc-direct': yc})))
        cli.sync_crl_agents()
    for aws, yc in [('A'*40,'A'*40), ('a'*40,'Y'*40), ('short','Y'*40), (' '*40,'Y'*40)]:
        with pytest.raises(SystemExit):
            sync(aws, yc)
    sync('A'*40, 'Y'*40)
    with pytest.raises(SystemExit):
        sync('Y'*40, 'A'*40)
    sync('B'*40, 'Z'*40)
    with db_factory() as db:
        assert db.get(CrlAgent, 'aws-direct').token_hash == hash_token('B'*40)
        assert db.get(CrlAgent, 'yc-direct').token_hash == hash_token('Z'*40)
