"""Real OpenVPN TLS reconnect gate; loopback/dev null, no host routes or TUN."""
import base64
from datetime import timedelta
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid
import pytest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from test_crl import agent_module, bundle
from test_profile_integration import real_pki
from test_legacy_profiles import legacy_pki
from veilway_control.models import utcnow


def wait_for(log, process, needles, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = log.read_text(errors='replace')
        if any(needle in text for needle in needles):
            return text
        if process.poll() is not None:
            break
        time.sleep(0.05)
    # Only synthetic transport logs; useful diagnosis without keys/config contents.
    raise AssertionError('OpenVPN test did not reach expected TLS state: ' + log.read_text(errors='replace')[-3000:])


@pytest.mark.parametrize("mode", ["yc-direct", "aws-direct", "yc-aws-multihop"])
@pytest.mark.parametrize("legacy", [False, True])
def test_atomic_crl_rejects_revoked_reconnect_and_preserves_other_client(request, tmp_path, mode, legacy):
    pki, store = request.getfixturevalue("legacy_pki" if legacy else "real_pki")[:2]
    from veilway_pki.core import MODES
    server_identity = MODES[mode][0]
    from test_pki import PkiTests
    ca_pem = (store.current()/'ca/ca.crt').read_bytes()
    ca = x509.load_pem_x509_certificate(ca_pem)
    ca_key = serialization.load_pem_private_key((store.current()/'ca/private/ca.key').read_bytes(), PkiTests.password)
    server_key = ec.generate_private_key(ec.SECP256R1())
    now = utcnow()
    server_cert = (x509.CertificateBuilder().subject_name(x509.Name([
        x509.NameAttribute(x509.NameOID.COMMON_NAME, server_identity)])).issuer_name(ca.subject)
        .public_key(server_key.public_key()).serial_number(99999).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(days=3)).add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False).sign(ca_key, hashes.SHA256()))
    (tmp_path/'ca.crt').write_bytes(ca_pem)
    (tmp_path/'server.crt').write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path/'server.key').write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (tmp_path/'tls-server.key').write_bytes((store.current()/f'endpoints/{server_identity}/tls-crypt-v2-server.key').read_bytes())
    crl_dir = Path(os.environ['VEILWAY_TEST_CRL_WRITER']) if os.environ.get('VEILWAY_TEST_CRL_WRITER') else tmp_path/'crl'
    reader_dir = Path(os.environ['VEILWAY_TEST_CRL_READER']) if os.environ.get('VEILWAY_TEST_CRL_READER') else crl_dir
    crl_dir.mkdir(mode=0o750, exist_ok=True)
    (crl_dir/'crl.pem').write_bytes((store.current()/'crl.pem').read_bytes())
    ids = [uuid.uuid4(), uuid.uuid4()]
    if legacy:
        imported = next(row for row in pki.legacy_catalog()["profiles"] if row["mode"] == mode)
        ids[0] = uuid.UUID(imported["profile_id"])
    for pid in ids[1:] if legacy else ids:
        pki.issue(pid, uuid.uuid4(), mode, (now+timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    materials = {pid: pki.download(pid).decode() for pid in ids}
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1',0)); port=reserved.getsockname()[1]
    server_config = tmp_path/'server.conf'
    server_config.write_text(f'''dev null
proto tcp-server
local 127.0.0.1
port {port}
tls-server
dh none
ca {tmp_path}/ca.crt
cert {tmp_path}/server.crt
key {tmp_path}/server.key
tls-crypt-v2 {tmp_path}/tls-server.key
crl-verify {reader_dir}/crl.pem
remote-cert-tls client
data-ciphers AES-256-GCM
tls-version-min 1.3
allow-compression no
persist-key
persist-tun
verb 3
''')
    server_log = tmp_path/'server.log'
    with server_log.open('wb') as output:
        server = subprocess.Popen(['/usr/sbin/openvpn','--config',str(server_config)], stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            wait_for(server_log, server, ['Listening for incoming TCP connection'])
            def connect(pid, expected):
                original = materials[pid]
                # Same signed client certificate/key and wrapped tls-crypt-v2 key;
                # replace network/pull directives only for the loopback TLS test.
                blocks = ''.join(original[original.index('<'+tag+'>'):original.index('</'+tag+'>')+len(tag)+3]+'\n'
                                 for tag in ('ca','cert','key','tls-crypt-v2'))
                conf = tmp_path/f'{pid}.conf'
                conf.write_text(f'dev null\nproto tcp-client\nremote 127.0.0.1 {port}\nnobind\ntls-client\nremote-cert-tls server\nverify-x509-name {server_identity} name\ndata-ciphers AES-256-GCM\ntls-version-min 1.3\nallow-compression no\nconnect-retry 1 1\nconnect-retry-max 5\nverb 3\n'+blocks)
                log = tmp_path/f'{pid}-{expected}.log'
                with log.open('wb') as output:
                    client = subprocess.Popen(['/usr/sbin/openvpn','--config',str(conf)],stdout=output,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
                    try:
                        if expected == 'accept':
                            wait_for(log, client, ['Initialization Sequence Completed'])
                        else:
                            text = wait_for(server_log, server, ['certificate revoked'])
                            assert 'certificate revoked' in text
                            assert 'Initialization Sequence Completed' not in log.read_text()
                    finally:
                        client.terminate(); client.wait(timeout=5)
                time.sleep(0.15)
            connect(ids[0], 'accept')
            server_pid = server.pid
            pki.revoke(ids[0], uuid.uuid4())
            pem, ca_pem = pki.crl()
            agent_module().install(crl_dir, ca_pem, bundle(pem))
            connect(ids[0], 'reject')
            connect(ids[1], 'accept')
            assert server.pid == server_pid and server.poll() is None
        finally:
            server.terminate(); server.wait(timeout=5)
