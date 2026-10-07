"""Synthetic CA only; all private material lives in automatically cleaned /tmp."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import base64
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import unittest
import uuid

from veilway_pki.core import CONFIG, MODES, PkiError, Store, check_tree, read_file, write_file
from veilway_pki.server import MAX_REQUEST, Server, receive


def ident():
    return str(uuid.uuid4())


def end(days=3):
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def command(args, cwd):
    result = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30, umask=0o077)
    if result.returncode:
        raise AssertionError("synthetic fixture command failed")
    return result.stdout


def crashing_worker(root, password, request, point, operation):
    store = Store(Path(root), password, fault=lambda reached: os._exit(73) if reached == point else None)
    getattr(store, operation)(**request)


class PkiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.umask(0o077)
        cls.fixture = tempfile.TemporaryDirectory(prefix="veilway-pki-fixture-")
        cls.source = Path(cls.fixture.name) / "source"
        cls.source.mkdir(mode=0o700)
        for folder in ("ca", "ca/private", "ca/newcerts", "endpoints"):
            (cls.source / folder).mkdir(mode=0o700)
        # An inherited encrypted CA may have a password shorter than 20 bytes.
        cls.password = secrets.token_hex(9).encode()
        write_file(cls.source / "test-passphrase", cls.password)
        write_file(cls.source / "openssl.cnf", CONFIG.encode())
        write_file(cls.source / "ca/index.txt", b"")
        write_file(cls.source / "ca/serial", b"1000\n")
        write_file(cls.source / "ca/crlnumber", b"1000\n")
        command(["/usr/bin/openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-aes-256-cbc",
                 "-pass", "file:test-passphrase", "-out", "ca/private/ca.key"], cls.source)
        command(["/usr/bin/openssl", "req", "-new", "-x509", "-key", "ca/private/ca.key", "-passin", "file:test-passphrase",
                 "-subj", "/CN=Synthetic Veilway CA", "-days", "30", "-addext", "basicConstraints=critical,CA:true,pathlen:0",
                 "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-out", "ca/ca.crt"], cls.source)
        for server, _port in MODES.values():
            (cls.source / "endpoints" / server).mkdir(mode=0o700)
            command(["/usr/sbin/openvpn", "--genkey", "tls-crypt-v2-server", f"endpoints/{server}/tls-crypt-v2-server.key"], cls.source)
        # Preserve a revoked historical certificate and an active historical one.
        signer = Store(Path(cls.fixture.name) / "fixture-signer", cls.password)
        for old in ("old-revoked", "old-active"):
            command(["/usr/bin/openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", f"{old}.key"], cls.source)
            command(["/usr/bin/openssl", "req", "-new", "-config", "openssl.cnf", "-key", f"{old}.key", "-subj", f"/CN={old}", "-out", f"{old}.csr"], cls.source)
            signer.ca(cls.source, "-extensions", "client_cert", "-days", "10", "-in", f"{old}.csr", "-out", f"{old}.crt", "-notext")
        signer.ca(cls.source, "-revoke", "old-revoked.crt")
        signer.ca(cls.source, "-gencrl", "-out", "crl.pem")
        (cls.source / "clients").mkdir(mode=0o700)
        write_file(cls.source / "clients/ignored.ovpn", b"synthetic old profile")
        cls.endpoints = {"yc-direct": "192.0.2.10", "aws-direct": "192.0.2.20", "yc-aws-multihop": "192.0.2.10"}

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="veilway-pki-test-")
        self.root = Path(self.temporary.name) / "storage"
        self.store = Store(self.root, self.password)
        self.store.import_ca(self.source, self.endpoints)

    def tearDown(self):
        self.temporary.cleanup()

    def request(self, mode="yc-direct"):
        return dict(profile_id=ident(), job_id=ident(), mode=mode, expires_at=end())

    def test_empty_ca_passphrase_is_rejected(self):
        for password in (b"", b"\r\n"):
            with self.assertRaises(PkiError):
                Store(Path(self.temporary.name) / "empty-password", password)

    def serial(self):
        return int(read_file(self.store.current() / "ca/serial"), 16)

    def assert_code(self, code, callable_, *args, **kwargs):
        with self.assertRaises(PkiError) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_all_modes_generate_client_certificates_and_distinct_wrapped_keys(self):
        keys = set()
        for mode, (server, port) in MODES.items():
            request = self.request(mode)
            result = self.store.issue(**request)
            self.assertEqual(result["profile_id"], request["profile_id"])
            material = base64.b64decode(self.store.download(request["profile_id"])["ovpn_base64"]).decode()
            self.assertIn(f"remote {self.endpoints[mode]} {port}\n", material)
            self.assertIn(f"verify-x509-name {server} name\n", material)
            self.assertEqual("block-ipv6\n" in material, mode == "yc-direct")
            for tag in ("ca", "cert", "key", "tls-crypt-v2"):
                self.assertEqual(material.count(f"<{tag}>"), 1)
                self.assertEqual(material.count(f"</{tag}>"), 1)
            self.assertIn("tls-version-min 1.3", material)
            self.assertIn("allow-compression no", material)
            path = self.store.current()
            relative = f"profiles/{request['profile_id']}"
            # OpenVPN parses the generated inline configuration without opening
            # a tunnel, reaching an endpoint or requiring network capabilities.
            self.store.run(["/usr/sbin/openvpn", "--config", f"{relative}/client.ovpn", "--show-ciphers"], path)
            self.store.run(["/usr/bin/openssl", "verify", "-purpose", "sslclient", "-CAfile", "ca/ca.crt", f"{relative}/client.crt"], path)
            private_public = self.store.run(["/usr/bin/openssl", "pkey", "-in", f"{relative}/client.key", "-pubout"], path)
            cert_public = self.store.run(["/usr/bin/openssl", "x509", "-in", f"{relative}/client.crt", "-pubkey", "-noout"], path)
            self.assertEqual(private_public, cert_public)
            keys.add(read_file(path / relative / "tls-crypt-v2-client.key"))
            self.assertEqual(self.store.certificate_end(path, f"{relative}/client.crt").strftime("%Y-%m-%dT%H:%M:%SZ"), request["expires_at"])
        self.assertEqual(len(keys), 3)
        check_tree(self.root / "generations")
        self.assertEqual(len(list((self.root / "generations").iterdir())), 1)

    def test_import_preserves_registry_counters_crl_and_encrypted_ca_without_old_profiles(self):
        path = self.store.current()
        for name in ("ca/ca.crt", "ca/private/ca.key", "ca/index.txt", "ca/serial", "ca/crlnumber", "crl.pem"):
            self.assertEqual(read_file(path / name), read_file(self.source / name))
        self.assertEqual(list((path / "profiles").iterdir()), [])
        self.assertIn(b"BEGIN ENCRYPTED PRIVATE KEY", read_file(path / "ca/private/ca.key"))
        self.assert_code("not_found", self.store.download, ident())
        self.assert_code("conflict", self.store.import_ca, self.source, self.endpoints)

    def test_repeat_issue_returns_same_certificate_profile_and_serial(self):
        request = self.request()
        before = self.serial()
        first = self.store.issue(**request)
        profile = self.store.download(request["profile_id"])
        for _ in range(3):
            self.assertEqual(self.store.issue(**request), first)
            self.assertEqual(self.store.download(request["profile_id"]), profile)
        self.assertEqual(self.serial(), before + 1)
        changed = {**request, "mode": "aws-direct"}
        self.assert_code("conflict", self.store.issue, **changed)
        self.assert_code("conflict", self.store.issue, **{**request, "job_id": ident()})

    def test_concurrent_repeated_issue_has_one_certificate(self):
        request = self.request()
        before = self.serial()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: Store(self.root, self.password).issue(**request), range(8)))
        self.assertEqual(len({json.dumps(value, sort_keys=True) for value in results}), 1)
        self.assertEqual(self.serial(), before + 1)

    def test_concurrent_distinct_issues_have_unique_serials(self):
        requests = [self.request(mode) for mode in MODES for _ in range(2)]
        before = self.serial()
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda request: Store(self.root, self.password).issue(**request), requests))
        self.assertEqual(len({result["serial"] for result in results}), len(requests))
        self.assertEqual(self.serial(), before + len(requests))

    def test_real_process_crash_recovery_before_and_after_commit(self):
        for point in ("after_signature", "before_commit", "after_commit"):
            request = self.request()
            before = self.serial()
            process = multiprocessing.get_context("fork").Process(target=crashing_worker,
                args=(str(self.root), self.password, request, point, "issue"))
            process.start()
            process.join(timeout=30)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 73)
            self.store = Store(self.root, self.password)
            result = self.store.issue(**request)
            self.assertEqual(self.serial(), before + 1)
            self.assertEqual(self.store.issue(**request), result)
            with self.store.locked():
                self.store.recover()
            self.assertEqual(len(list((self.root / "generations").iterdir())), 1)
            registry = read_file(self.store.current() / "ca/index.txt")
            self.assertEqual(registry.count(request["profile_id"].encode()), 1)

    def test_revoke_is_idempotent_preserves_old_revocations_and_denies_download(self):
        request = self.request()
        self.store.issue(**request)
        job = ident()
        before = self.store.crl_number(self.store.current())
        result = self.store.revoke(request["profile_id"], job)
        self.assertEqual(result["crl_number"], before + 1)
        self.assertEqual(self.store.revoke(request["profile_id"], job), result)
        self.assertEqual(self.store.revoke(request["profile_id"], ident()), result)
        self.assertEqual(self.store.crl_number(self.store.current()), before + 1)
        self.assert_code("revoked", self.store.download, request["profile_id"])
        self.assert_code("conflict", self.store.revoke, request["profile_id"], request["job_id"])
        path = self.store.current()
        for cert in (f"profiles/{request['profile_id']}/client.crt", "ca/newcerts/1000.pem"):
            self.assert_code("operation_failed", self.store.run,
                             ["/usr/bin/openssl", "verify", "-crl_check", "-CAfile", "ca/ca.crt", "-CRLfile", "crl.pem", cert], path)
        self.store.run(["/usr/bin/openssl", "verify", "-crl_check", "-CAfile", "ca/ca.crt", "-CRLfile", "crl.pem", "ca/newcerts/1001.pem"], path)

    def test_revoke_real_crash_recovery_does_not_duplicate_crl_update(self):
        for point in ("after_crl", "before_commit", "after_commit"):
            request = self.request()
            self.store.issue(**request)
            before = self.store.crl_number(self.store.current())
            revoke = dict(profile_id=request["profile_id"], job_id=ident())
            process = multiprocessing.get_context("fork").Process(target=crashing_worker,
                args=(str(self.root), self.password, revoke, point, "revoke"))
            process.start()
            process.join(timeout=30)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 73)
            result = Store(self.root, self.password).revoke(**revoke)
            self.assertEqual(result["crl_number"], before + 1)
            self.assertEqual(self.store.revoke(**revoke), result)

    def test_expiry_ca_limit_and_expired_download(self):
        request = self.request()
        for value in (end(-1), end(40)):
            self.assert_code("expired", self.store.issue, **{**request, "expires_at": value})
        self.store.issue(**request)
        path = self.store.current() / "profiles" / request["profile_id"] / "record.json"
        record = json.loads(read_file(path))
        record["issue"]["expires_at"] = end(-1)
        write_file(path, json.dumps(record).encode())
        self.assert_code("expired", self.store.download, request["profile_id"])

    def test_dispatch_rejects_extra_fields_paths_server_operations_and_shell_payloads(self):
        before = self.serial()
        for request in (None, [], {}, {"operation": "exec", "command": "id"}, {"operation": "import-ca", "source": "/private"},
                        {"operation": "download", "profile_id": "../../private/ca.key"},
                        {"operation": "download", "profile_id": ident(), "path": "ca/ca.key"},
                        {"operation": "issue", **self.request(), "mode": "$(id)"},
                        {"operation": "issue", **self.request(), "expires_at": "2027-01-01; id"}):
            self.assert_code("invalid_request", self.store.dispatch, request)
        self.assertEqual(self.serial(), before)

    def test_import_rejects_wrong_passphrase_unencrypted_ca_bad_counters_missing_history_symlinks(self):
        for mutation in ("wrong_password", "plaintext", "serial", "crlnumber", "missing_cert", "symlink", "crl_mismatch", "endpoint_injection"):
            with self.subTest(mutation=mutation):
                folder = Path(self.temporary.name) / mutation
                source = folder / "source"
                shutil.copytree(self.source, source)
                password = self.password
                endpoints = self.endpoints.copy()
                if mutation == "wrong_password":
                    password = secrets.token_hex(24).encode()
                elif mutation == "plaintext":
                    value = command(["/usr/bin/openssl", "pkey", "-in", "ca/private/ca.key", "-passin", "file:test-passphrase"], source)
                    write_file(source / "ca/private/ca.key", value)
                elif mutation == "serial":
                    write_file(source / "ca/serial", b"1001\n")
                elif mutation == "crlnumber":
                    write_file(source / "ca/crlnumber", b"1000\n")
                elif mutation == "missing_cert":
                    (source / "ca/newcerts/1000.pem").unlink()
                elif mutation == "symlink":
                    (source / "ca/private/ca.key").unlink()
                    (source / "ca/private/ca.key").symlink_to(self.source / "ca/private/ca.key")
                elif mutation == "crl_mismatch":
                    write_file(source / "ca/index.txt", read_file(source / "ca/index.txt").replace(b"R\t", b"V\t", 1))
                else:
                    endpoints["yc-direct"] = "192.0.2.10\nscript-security 2"
                candidate = Store(folder / "target", password)
                self.assert_code("invalid_import", candidate.import_ca, source, endpoints)
                self.assertFalse((candidate.root / "CURRENT").exists())

    def test_store_refuses_symlink_and_loose_permissions(self):
        path = self.store.current()
        (path / "ca/serial").chmod(0o644)
        self.assert_code("storage_error", self.store.current)
        (path / "ca/serial").chmod(0o600)
        (path / "profiles/escape").symlink_to(self.source)
        self.assert_code("storage_error", self.store.current)

    def test_socket_framing_allowed_uid_methods_and_duplicate_fields(self):
        socket_path = Path(self.temporary.name) / "pki.sock"
        server = Server(socket_path, self.store, allowed_uid=os.geteuid(), socket_gid=os.getegid())
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            self.assertEqual(stat.S_IMODE(socket_path.stat().st_mode), 0o660)
            request = self.request()
            def rpc(payload):
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.settimeout(10)
                    connection.connect(str(socket_path))
                    connection.sendall(struct.pack("!I", len(payload)) + payload)
                    size = struct.unpack("!I", receive(connection, 4))[0]
                    return json.loads(receive(connection, size))
            first = rpc(json.dumps({"operation": "issue", **request}).encode())
            self.assertTrue(first["ok"])
            self.assertEqual(rpc(json.dumps({"operation": "issue", **request}).encode()), first)
            download = rpc(json.dumps({"operation": "download", "profile_id": request["profile_id"]}).encode())
            self.assertTrue(download["ok"])
            for payload in (b'{"operation":"download","operation":"issue"}', b"null", b"{", b"[]",
                            json.dumps({"operation": "download", "profile_id": request["profile_id"], "extra": True}).encode()):
                self.assertEqual(rpc(payload), {"ok": False, "error": "invalid_request"})
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(10)
                connection.connect(str(socket_path))
                connection.sendall(struct.pack("!I", MAX_REQUEST + 1))
                size = struct.unpack("!I", receive(connection, 4))[0]
                self.assertEqual(json.loads(receive(connection, size))["error"], "invalid_request")
            server.allowed_uid = os.geteuid() + 100000
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(10)
                connection.connect(str(socket_path))
                self.assertEqual(connection.recv(1), b"")
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
        self.assertFalse(socket_path.exists())

    def test_uninitialized_storage_fails_closed(self):
        store = Store(Path(self.temporary.name) / "empty", self.password)
        self.assert_code("unavailable", store.issue, **self.request())
        self.assert_code("unavailable", store.download, ident())
        self.assert_code("unavailable", store.revoke, ident(), ident())

    def test_quiesced_backup_restore_preserves_profiles_receipts_and_revocations(self):
        requests = [self.request(mode) for mode in MODES]
        issued = [self.store.issue(**request) for request in requests]
        material = self.store.download(requests[1]["profile_id"])
        revoke_key = ident()
        revoked = self.store.revoke(requests[0]["profile_id"], revoke_key)
        backup = Path(self.temporary.name) / "backup"
        # A quiesced coherent copy includes CURRENT, counters, registry,
        # profiles and operation receipts. No export goes to the repository.
        with self.store.locked():
            shutil.copytree(self.root, backup)
            before_serial = read_file(self.store.current() / "ca/serial")
            before_crl = read_file(self.store.current() / "crl.pem")
        restored = Store(backup, self.password)
        restored.recover()
        for request, result in zip(requests, issued):
            self.assertEqual(restored.issue(**request), result)
        self.assertEqual(restored.revoke(requests[0]["profile_id"], revoke_key), revoked)
        self.assert_code("revoked", restored.download, requests[0]["profile_id"])
        self.assertEqual(restored.download(requests[1]["profile_id"]), material)
        self.assertEqual(read_file(restored.current() / "ca/serial"), before_serial)
        self.assertEqual(read_file(restored.current() / "crl.pem"), before_crl)
        # New operations continue strictly above restored counters and retain
        # both historical and acceptance-test revoked serials.
        later = restored.issue(**self.request())
        self.assertGreater(int(later["serial"], 16), int(issued[-1]["serial"], 16))
        next_revoke = restored.revoke(requests[1]["profile_id"], ident())
        self.assertGreater(next_revoke["crl_number"], revoked["crl_number"])
        self.assert_code("revoked", restored.download, requests[0]["profile_id"])


if __name__ == "__main__":
    unittest.main()
