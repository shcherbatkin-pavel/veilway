"""Actual production entrypoint and Unix permissions, using disposable tmpfs."""
import errno
import json
import os
from pathlib import Path
import socket
import shutil
import signal
import struct
import subprocess
import time
import unittest

import test_pki as fixtures
from test_pki import ident, end
from veilway_pki.core import Store, read_file, write_file, write_json, mkdir
from veilway_pki.server import receive


@unittest.skipUnless(os.geteuid() == 0 and Path('/usr/local/bin/veilway-pki-entrypoint').exists(),
                     'production runtime test requires the isolated PKI Docker test image')
class ContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PkiTests.setUpClass()
        cls.source = fixtures.PkiTests.source
        cls.password = fixtures.PkiTests.password
        cls.endpoints = fixtures.PkiTests.endpoints
        cls.storage = Path('/var/lib/veilway-pki')
        cls.runtime = Path('/run/veilway-pki')
        cls.socket = cls.runtime / 'pki.sock'
        for path in (cls.storage, cls.runtime):
            path.chmod(0o710 if path == cls.runtime else 0o700)
            os.chown(path, 10002, 10003 if path == cls.runtime else 10002)
        os.setgroups([10003])
        write_file(Path('/run/secrets/pki_ca_passphrase'), cls.password)
        write_file(Path('/run/secrets/pki_endpoints'), json.dumps(cls.endpoints).encode())
        for root, dirs, files in os.walk(Path(fixtures.PkiTests.fixture.name), topdown=False):
            for filename in files:
                path = Path(root) / filename
                os.chmod(path, 0o600)
                os.chown(path, 10002, 10002)
            os.chmod(root, 0o700)
            os.chown(root, 10002, 10002)
        imported = subprocess.run(['/usr/local/bin/veilway-pki-entrypoint', 'import-ca', '--source', str(cls.source)],
                                  capture_output=True, timeout=30)
        if imported.returncode != 0:
            raise AssertionError('production entrypoint synthetic import failed')
        # Exercise the real offline import-profiles entrypoint after privilege drop.
        cls.bundle = cls.source / 'legacy-bundle'
        pid = os.fork()
        if pid == 0:
            try:
                os.setgroups([10003]); os.setgid(10002); os.setuid(10002)
                mkdir(cls.bundle)
                store = Store(cls.storage, cls.password)
                path = store.current()
                store.run(['/usr/sbin/openvpn', '--tls-crypt-v2', str(path / 'endpoints/yc-direct/tls-crypt-v2-server.key'),
                           '--genkey', 'tls-crypt-v2-client', str(cls.bundle / 'wrapped.key')], path)
                lines = ['client', 'dev tun', 'proto udp4', 'remote 192.0.2.10 1194', 'nobind', 'persist-key', 'persist-tun',
                         'remote-cert-tls server', 'verify-x509-name yc-direct name', 'tls-version-min 1.3',
                         'tls-cert-profile preferred', 'data-ciphers CHACHA20-POLY1305:AES-256-GCM:AES-128-GCM',
                         'auth SHA256', 'allow-compression no', 'block-ipv6', 'verb 3']
                for tag, material in [('ca', path / 'ca/ca.crt'), ('cert', cls.source / 'old-active.crt'),
                                      ('key', cls.source / 'old-active.key'), ('tls-crypt-v2', cls.bundle / 'wrapped.key')]:
                    lines.extend([f'<{tag}>', read_file(material).decode().strip(), f'</{tag}>'])
                write_file(cls.bundle / 'original.ovpn', ('\n'.join(lines) + '\n').encode())
                (cls.bundle / 'wrapped.key').unlink()
                write_json(cls.bundle / 'manifest.json', {'version': 1, 'profiles': [
                    {'file': 'original.ovpn', 'mode': 'yc-direct', 'device_name': 'Original'}]})
                os._exit(0)
            except Exception:
                os._exit(1)
        _, status = os.waitpid(pid, 0)
        if status != 0:
            raise AssertionError('synthetic legacy fixture setup failed')
        imported = subprocess.run(['/usr/local/bin/veilway-pki-entrypoint', 'import-profiles', '--source', str(cls.bundle)],
                                  capture_output=True, timeout=30)
        if imported.returncode != 0 or b'committed: 1 profiles' not in imported.stdout:
            raise AssertionError('production entrypoint legacy import failed')
        if cls.password in imported.stdout + imported.stderr or b'BEGIN' in imported.stdout + imported.stderr:
            raise AssertionError('legacy import log disclosed material')
        cls.process = subprocess.Popen(['/usr/local/bin/veilway-pki-entrypoint', 'serve'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 10
        while not cls.socket.exists():
            if cls.process.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError('production PKI did not create its socket')
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        pid = os.fork()
        if pid == 0:
            os.setgroups([])
            os.setgid(10002)
            os.setuid(10002)
            os.kill(cls.process.pid, signal.SIGTERM)
            shutil.rmtree(fixtures.PkiTests.fixture.name)
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        if status != 0:
            raise AssertionError('synthetic fixture cleanup failed')
        stdout, stderr = cls.process.communicate(timeout=10)
        if cls.process.returncode != 0:
            raise AssertionError('production PKI did not shut down cleanly')
        for output in (stdout, stderr):
            if cls.password in output or b'BEGIN' in output or b'192.0.2.' in output:
                raise AssertionError('runtime logs disclosed private inputs')
        if cls.socket.exists():
            raise AssertionError('production PKI left its socket after shutdown')
        fixtures.PkiTests.tearDownClass()

    def as_user(self, uid, operation):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                os.setgroups([10003] if uid == 10001 else [])
                os.setgid(uid)
                os.setuid(uid)
                result = operation()
                os.write(write_fd, json.dumps(result).encode())
                os._exit(0)
            except Exception:
                os.write(write_fd, b'{"failed":true}')
                os._exit(1)
        os.close(write_fd)
        payload = os.read(read_fd, 65536)
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(status, 0, 'isolated runtime child failed')
        return json.loads(payload)

    def rpc(self, request):
        payload = json.dumps(request).encode()
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(10)
            connection.connect(str(self.socket))
            connection.sendall(struct.pack('!I', len(payload)) + payload)
            size = struct.unpack('!I', receive(connection, 4))[0]
            return json.loads(receive(connection, size))

    def test_backend_can_issue_download_revoke_via_socket_without_reading_ca_or_secret(self):
        profile_id, job_id = ident(), ident()
        def operation():
            denied = 0
            for path in (self.storage / 'CURRENT', Path('/run/secrets/pki_ca_passphrase')):
                try:
                    read_file(path)
                except PermissionError:
                    denied += 1
            issued = self.rpc(dict(operation='issue', profile_id=profile_id, job_id=job_id, mode='aws-direct', expires_at=end()))
            repeated = self.rpc(dict(operation='issue', profile_id=profile_id, job_id=job_id, mode='aws-direct', expires_at=issued['result']['expires_at']))
            downloaded = self.rpc(dict(operation='download', profile_id=profile_id))
            revoked = self.rpc(dict(operation='revoke', profile_id=profile_id, job_id=ident()))
            rejected = self.rpc(dict(operation='download', profile_id=profile_id))
            return dict(denied=denied, issued=issued['ok'], repeated=(issued == repeated),
                        downloaded=downloaded['ok'], revoked=revoked['ok'], rejected=rejected['error'])
        result = self.as_user(10001, operation)
        self.assertEqual(result, dict(denied=2, issued=True, repeated=True, downloaded=True, revoked=True, rejected='revoked'))
        self.assertEqual(self.socket.stat().st_uid, 10002)
        self.assertEqual(self.socket.stat().st_gid, 10003)
        self.assertEqual(self.socket.stat().st_mode & 0o777, 0o660)
        self.assertEqual(self.runtime.stat().st_mode & 0o777, 0o710)

    def test_peer_uid_and_socket_directory_reject_other_users(self):
        # Root can traverse DAC, but SO_PEERCRED still rejects it.
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(10)
            connection.connect(str(self.socket))
            self.assertEqual(connection.recv(1), b'')
        def operation():
            with socket.socket(socket.AF_UNIX) as connection:
                try:
                    connection.connect(str(self.socket))
                except PermissionError:
                    return {'denied': True}
            return {'denied': False}
        self.assertEqual(self.as_user(10004, operation), {'denied': True})

    def test_container_has_no_network_docker_socket_or_writable_root(self):
        self.assertEqual({name for _, name in socket.if_nameindex()}, {'lo'})
        self.assertFalse(Path('/var/run/docker.sock').exists())
        with self.assertRaises(OSError) as caught:
            Path('/app/should-never-write').write_bytes(b'')
        self.assertEqual(caught.exception.errno, errno.EROFS)
        invalid = subprocess.run(['/usr/local/bin/veilway-pki-entrypoint', '/bin/sh', '-c', 'id'], capture_output=True, timeout=10)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(invalid.stderr, b'PKI startup failed.\n')
