from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import uuid


MODES = {
    "yc-direct": ("yc-direct", 1194),
    "aws-direct": ("aws-direct", 1194),
    "yc-aws-multihop": ("yc-multihop-ingress", 1195),
}
MAX_FILE = 16 * 1024 * 1024
CONFIG = """[ca]
default_ca = CA_default
[CA_default]
dir = ./ca
database = $dir/index.txt
new_certs_dir = $dir/newcerts
certificate = $dir/ca.crt
private_key = $dir/private/ca.key
serial = $dir/serial
crlnumber = $dir/crlnumber
crl = ./crl.pem
default_md = sha256
default_days = 365
default_crl_days = 7
unique_subject = no
policy = policy
copy_extensions = none
[policy]
commonName = supplied
[req]
distinguished_name = dn
prompt = no
[dn]
commonName = Veilway
[client_cert]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyAgreement
extendedKeyUsage = clientAuth
"""


class PkiError(Exception):
    """Only fixed codes may cross the socket or reach an operator log."""

    CODES = {"invalid_request", "conflict", "not_found", "unavailable", "expired",
             "revoked", "invalid_import", "storage_error", "operation_failed"}

    def __init__(self, code: str):
        self.code = code if code in self.CODES else "operation_failed"
        super().__init__(self.code)


def canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise PkiError("invalid_request")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise PkiError("invalid_request") from None
    if str(parsed) != value:
        raise PkiError("invalid_request")
    return value


def expiry(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise PkiError("invalid_request")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise PkiError("invalid_request") from None


def read_file(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
            raise PkiError("storage_error")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(MAX_FILE + 1)
    finally:
        os.close(fd)


def write_file(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path: Path, data: object) -> None:
    write_file(path, json.dumps(data, sort_keys=True, separators=(",", ":")).encode())


def load_json(path: Path):
    return json.loads(read_file(path))


def mkdir(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise PkiError("storage_error")
    os.chmod(path, 0o700)


def check_tree(path: Path, *, protected: bool = True) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (protected and stat.S_IMODE(info.st_mode) != 0o700):
        raise PkiError("storage_error")
    for child in path.iterdir():
        info = child.lstat()
        if stat.S_ISDIR(info.st_mode):
            check_tree(child, protected=protected)
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
            raise PkiError("storage_error")
        elif protected and stat.S_IMODE(info.st_mode) != 0o600:
            raise PkiError("storage_error")


def sync_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            sync_tree(child)
        else:
            with child.open("rb") as stream:
                os.fsync(stream.fileno())
    sync_directory(path)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Store:
    """Copy-on-write CA generations. CURRENT is the sole commit point.

    A process-wide flock serializes both readers and writers across containers.
    No material leaves a transaction before the durable pointer is committed.
    Business metadata stays in the application's PostgreSQL; receipt files here
    are the CA's technical operation journal, not a second user/profile database.
    """

    def __init__(self, root: Path, passphrase: bytes, fault=None):
        self.root = root
        self.passphrase = passphrase.rstrip(b"\r\n")
        # Import must preserve the existing CA password; decryption validates it.
        if not 1 <= len(self.passphrase) <= 4096 or b"\n" in self.passphrase or b"\x00" in self.passphrase:
            raise PkiError("unavailable")
        self.fault = fault or (lambda point: None)
        mkdir(root)
        mkdir(root / "generations")

    @contextmanager
    def locked(self):
        fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_nlink != 1:
                raise PkiError("storage_error")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def current(self) -> Path:
        try:
            name = read_file(self.root / "CURRENT").decode("ascii")
        except FileNotFoundError:
            raise PkiError("unavailable") from None
        canonical_uuid(name)
        path = self.root / "generations" / name
        check_tree(path)
        return path

    def recover(self) -> None:
        # Remove only service-owned, unpublished generations. No external paths.
        try:
            current = self.current().name
        except PkiError as error:
            if error.code != "unavailable":
                raise
            current = None
        for path in (self.root / "generations").iterdir():
            canonical_uuid(path.name)
            if path.name != current:
                check_tree(path)
                shutil.rmtree(path)
        (self.root / "CURRENT.next").unlink(missing_ok=True)
        sync_directory(self.root / "generations")

    def transaction(self, previous: Path | None) -> Path:
        self.recover()
        path = self.root / "generations" / str(uuid.uuid4())
        if previous is None:
            mkdir(path)
        else:
            shutil.copytree(previous, path)
        return path

    def commit(self, path: Path) -> None:
        check_tree(path)
        sync_tree(path)
        sync_directory(path.parent)
        write_file(self.root / "CURRENT.next", path.name.encode("ascii"))
        self.fault("before_commit")
        os.replace(self.root / "CURRENT.next", self.root / "CURRENT")
        sync_directory(self.root)
        self.fault("after_commit")
        self.recover()

    def run(self, args: list[str], cwd: Path, *, password: bool = False) -> bytes:
        # Call sites use fixed executable/subcommands and validated identifiers.
        # Never accept executable names, config text or shell fragments via RPC.
        if args[0] not in {"/usr/bin/openssl", "/usr/sbin/openvpn"}:
            raise PkiError("operation_failed")
        descriptor = None
        try:
            if password:
                descriptor = os.memfd_create("pki-passphrase", flags=os.MFD_ALLOW_SEALING)
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, self.passphrase + b"\n")
                os.lseek(descriptor, 0, os.SEEK_SET)
                fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS,
                            fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
                args = [*args, "-passin", f"fd:{descriptor}"]
            result = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=30, check=False, shell=False, umask=0o077,
                                    env={"PATH": "/usr/bin:/usr/sbin", "LANG": "C", "TZ": "UTC"},
                                    pass_fds=() if descriptor is None else (descriptor,))
            if result.returncode != 0:
                raise PkiError("operation_failed")
            return result.stdout
        except (OSError, subprocess.TimeoutExpired):
            raise PkiError("operation_failed") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def ca(self, path: Path, *args: str) -> bytes:
        return self.run(["/usr/bin/openssl", "ca", "-batch", "-config", "openssl.cnf", *args], path, password=True)

    def certificate_end(self, path: Path, certificate: str) -> datetime:
        value = self.run(["/usr/bin/openssl", "x509", "-in", certificate, "-noout", "-enddate"], path)
        try:
            return datetime.strptime(value.decode().strip().split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            raise PkiError("invalid_import") from None

    def import_ca(self, source: Path, endpoints: dict) -> None:
        """Offline operator-only import. No CLI/RPC for creating server identities."""
        with self.locked():
            if (self.root / "CURRENT").exists():
                raise PkiError("conflict")
            try:
                if set(endpoints) != set(MODES):
                    raise PkiError("invalid_import")
                for address in endpoints.values():
                    if not isinstance(address, str) or str(ipaddress.IPv4Address(address)) != address:
                        raise PkiError("invalid_import")
                # Inspect only explicit import inputs, never local VPN trees by discovery.
                for folder in (source, source / "ca", source / "ca/private", source / "ca/newcerts", source / "endpoints"):
                    if folder.is_symlink() or not folder.is_dir():
                        raise PkiError("invalid_import")
                path = self.transaction(None)
                for folder in ("ca", "ca/private", "ca/newcerts", "endpoints", "profiles", "receipts"):
                    mkdir(path / folder)
                for name in ("ca/ca.crt", "ca/private/ca.key", "ca/index.txt", "ca/serial", "ca/crlnumber", "crl.pem"):
                    write_file(path / name, read_file(source / name))
                # Allowlisted counters/registry and historical certificates are preserved.
                for cert in (source / "ca/newcerts").iterdir():
                    if not re.fullmatch(r"[0-9A-Fa-f]+\.pem", cert.name):
                        raise PkiError("invalid_import")
                    write_file(path / "ca/newcerts" / cert.name, read_file(cert))
                for server, _port in MODES.values():
                    folder = source / "endpoints" / server
                    if folder.is_symlink() or not folder.is_dir():
                        raise PkiError("invalid_import")
                    mkdir(path / "endpoints" / server)
                    write_file(path / "endpoints" / server / "tls-crypt-v2-server.key",
                               read_file(folder / "tls-crypt-v2-server.key"))
                write_file(path / "openssl.cnf", CONFIG.encode())
                write_json(path / "endpoints.json", endpoints)
                self.validate_import(path)
                self.commit(path)
            except PkiError as error:
                if error.code == "conflict":
                    raise
                raise PkiError("invalid_import") from None
            except (OSError, ValueError, TypeError):
                raise PkiError("invalid_import") from None

    def validate_import(self, path: Path) -> None:
        key = read_file(path / "ca/private/ca.key")
        if b"BEGIN ENCRYPTED PRIVATE KEY" not in key:
            raise PkiError("invalid_import")
        private_public = self.run(["/usr/bin/openssl", "pkey", "-in", "ca/private/ca.key", "-pubout"], path, password=True)
        cert_public = self.run(["/usr/bin/openssl", "x509", "-in", "ca/ca.crt", "-pubkey", "-noout"], path)
        if private_public != cert_public or self.certificate_end(path, "ca/ca.crt") <= datetime.now(timezone.utc):
            raise PkiError("invalid_import")
        ca_text = self.run(["/usr/bin/openssl", "x509", "-in", "ca/ca.crt", "-text", "-noout"], path)
        if b"CA:TRUE" not in ca_text or b"Certificate Sign" not in ca_text or b"CRL Sign" not in ca_text:
            raise PkiError("invalid_import")
        self.run(["/usr/bin/openssl", "verify", "-CAfile", "ca/ca.crt", "ca/ca.crt"], path)
        self.run(["/usr/bin/openssl", "crl", "-in", "crl.pem", "-verify", "-CAfile", "ca/ca.crt", "-noout"], path)
        crl_text = self.run(["/usr/bin/openssl", "crl", "-in", "crl.pem", "-text", "-noout"], path).decode()
        crl_number = self.crl_number(path)
        dates = self.run(["/usr/bin/openssl", "crl", "-in", "crl.pem", "-noout", "-lastupdate", "-nextupdate"], path).decode().splitlines()
        parsed_dates = [datetime.strptime(line.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc) for line in dates]
        if len(parsed_dates) != 2 or not parsed_dates[0] <= datetime.now(timezone.utc) < parsed_dates[1]:
            raise PkiError("invalid_import")
        counters = []
        for name in ("serial", "crlnumber"):
            value = read_file(path / "ca" / name).decode().strip()
            if not re.fullmatch(r"[0-9a-fA-F]{1,40}", value) or int(value, 16) < 1:
                raise PkiError("invalid_import")
            counters.append(int(value, 16))
        revoked = {int(value, 16) for value in re.findall(r"Serial Number:\s*([0-9A-Fa-f]+)", crl_text)}
        registry_revoked, seen = set(), set()
        for line in read_file(path / "ca/index.txt").decode().splitlines():
            fields = line.split("\t")
            if len(fields) != 6 or fields[0] not in {"V", "R", "E"} or not re.fullmatch(r"[0-9A-Fa-f]{1,40}", fields[3]):
                raise PkiError("invalid_import")
            serial = int(fields[3], 16)
            if serial in seen or serial >= counters[0] or serial < 1:
                raise PkiError("invalid_import")
            seen.add(serial)
            if fields[0] == "R":
                registry_revoked.add(serial)
            cert = path / "ca/newcerts" / (fields[3] + ".pem")
            if not cert.exists():
                raise PkiError("invalid_import")
            actual = self.run(["/usr/bin/openssl", "x509", "-in", str(cert), "-noout", "-serial"], path).decode().strip().split("=")[1]
            if int(actual, 16) != serial:
                raise PkiError("invalid_import")
            # Historical expired certificates remain valid import inputs.
            self.run(["/usr/bin/openssl", "verify", "-no_check_time", "-CAfile", "ca/ca.crt", str(cert)], path)
        if revoked != registry_revoked or counters[1] <= crl_number:
            raise PkiError("invalid_import")
        historical_serials = [int(cert.stem, 16) for cert in (path / "ca/newcerts").iterdir()]
        if set(historical_serials) != seen or len(historical_serials) != len(seen):
            raise PkiError("invalid_import")
        # Ask OpenVPN to parse each server key without networking; discard clients.
        for server, _port in MODES.values():
            self.run(["/usr/sbin/openvpn", "--tls-crypt-v2", f"endpoints/{server}/tls-crypt-v2-server.key",
                      "--genkey", "tls-crypt-v2-client", "import-test.key"], path)
            (path / "import-test.key").unlink()
        # Registry parsing by OpenSSL catches malformed dates/subjects as well.
        self.ca(path, "-updatedb")
        # Preserve the imported registry exactly; updatedb is validation only.
        if (path / "ca/index.txt.old").exists():
            os.replace(path / "ca/index.txt.old", path / "ca/index.txt")
        for extra in (path / "ca").glob("*.old"):
            extra.unlink()
        self.protect(path)

    @staticmethod
    def protect(path: Path) -> None:
        # Files created by OpenSSL/OpenVPN inherit umask, normalize defensively.
        for folder, dirs, files in os.walk(path):
            os.chmod(folder, 0o700)
            for filename in files:
                os.chmod(Path(folder) / filename, 0o600)

    def crl_number(self, path: Path) -> int:
        value = self.run(["/usr/bin/openssl", "crl", "-in", "crl.pem", "-noout", "-crlnumber"], path).decode().strip()
        try:
            return int(value.split("=", 1)[1], 16)
        except (ValueError, IndexError):
            raise PkiError("invalid_import") from None

    @staticmethod
    def receipt(path: Path, job_id: str, request: dict):
        receipt_path = path / "receipts" / (job_id + ".json")
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if receipt_path.exists():
            data = load_json(receipt_path)
            if data["request_hash"] != digest:
                raise PkiError("conflict")
            return digest, data["result"]
        return digest, None

    def issue(self, profile_id: str, job_id: str, mode: str, expires_at: str) -> dict:
        profile_id, job_id = canonical_uuid(profile_id), canonical_uuid(job_id)
        if not isinstance(mode, str) or mode not in MODES:
            raise PkiError("invalid_request")
        end = expiry(expires_at)
        request = dict(operation="issue", profile_id=profile_id, mode=mode, expires_at=expires_at)
        with self.locked():
            previous = self.current()
            digest, existing = self.receipt(previous, job_id, request)
            if existing is not None:
                return existing
            if (previous / "profiles" / profile_id).exists():
                raise PkiError("conflict")
            if end <= datetime.now(timezone.utc) or end > self.certificate_end(previous, "ca/ca.crt"):
                raise PkiError("expired")
            path = self.transaction(previous)
            folder = path / "profiles" / profile_id
            mkdir(folder)
            relative = f"profiles/{profile_id}"
            self.run(["/usr/bin/openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
                      "-out", f"{relative}/client.key"], path)
            self.run(["/usr/bin/openssl", "req", "-new", "-sha256", "-config", "openssl.cnf", "-key", f"{relative}/client.key",
                      "-subj", f"/CN={profile_id}", "-out", f"{relative}/client.csr"], path)
            self.ca(path, "-extensions", "client_cert", "-enddate", end.strftime("%Y%m%d%H%M%SZ"),
                    "-notext", "-in", f"{relative}/client.csr", "-out", f"{relative}/client.crt")
            self.fault("after_signature")
            self.run(["/usr/bin/openssl", "verify", "-purpose", "sslclient", "-CAfile", "ca/ca.crt", f"{relative}/client.crt"], path)
            if self.certificate_end(path, f"{relative}/client.crt") != end:
                raise PkiError("operation_failed")
            serial = self.run(["/usr/bin/openssl", "x509", "-in", f"{relative}/client.crt", "-noout", "-serial"], path).decode().strip().split("=")[1]
            server, port = MODES[mode]
            self.run(["/usr/sbin/openvpn", "--tls-crypt-v2", f"endpoints/{server}/tls-crypt-v2-server.key",
                      "--genkey", "tls-crypt-v2-client", f"{relative}/tls-crypt-v2-client.key"], path)
            endpoints = load_json(path / "endpoints.json")
            lines = ["client", "dev tun", "proto udp4", f"remote {endpoints[mode]} {port}", "nobind", "persist-key", "persist-tun",
                     "remote-cert-tls server", f"verify-x509-name {server} name", "tls-version-min 1.3", "tls-cert-profile preferred",
                     "data-ciphers CHACHA20-POLY1305:AES-256-GCM:AES-128-GCM", "auth SHA256", "allow-compression no"]
            if mode == "yc-direct":
                lines.append("block-ipv6")
            lines.append("verb 3")
            for tag, filename in (("ca", "ca/ca.crt"), ("cert", f"{relative}/client.crt"), ("key", f"{relative}/client.key"),
                                  ("tls-crypt-v2", f"{relative}/tls-crypt-v2-client.key")):
                lines.extend([f"<{tag}>", read_file(path / filename).decode().strip(), f"</{tag}>"])
            write_file(folder / "client.ovpn", ("\n".join(lines) + "\n").encode())
            result = dict(profile_id=profile_id, mode=mode, expires_at=expires_at, serial=serial,
                          certificate_sha256=hashlib.sha256(read_file(folder / "client.crt")).hexdigest())
            write_json(folder / "record.json", {"issue": result, "revoked": False})
            write_json(path / "receipts" / (job_id + ".json"), {"request_hash": digest, "result": result})
            self.protect(path)
            self.commit(path)
            return result

    def download(self, profile_id: str) -> dict:
        profile_id = canonical_uuid(profile_id)
        with self.locked():
            path = self.current()
            folder = path / "profiles" / profile_id
            if not folder.exists():
                raise PkiError("not_found")
            record = load_json(folder / "record.json")
            if record["revoked"]:
                raise PkiError("revoked")
            if expiry(record["issue"]["expires_at"]) <= datetime.now(timezone.utc):
                raise PkiError("expired")
            return {"profile_id": profile_id, "ovpn_base64": base64.b64encode(read_file(folder / "client.ovpn")).decode("ascii")}

    def revoke(self, profile_id: str, job_id: str) -> dict:
        profile_id, job_id = canonical_uuid(profile_id), canonical_uuid(job_id)
        request = dict(operation="revoke", profile_id=profile_id)
        with self.locked():
            previous = self.current()
            digest, existing = self.receipt(previous, job_id, request)
            if existing is not None:
                return existing
            if not (previous / "profiles" / profile_id).exists():
                raise PkiError("not_found")
            path = self.transaction(previous)
            folder = path / "profiles" / profile_id
            record = load_json(folder / "record.json")
            if not record["revoked"]:
                self.ca(path, "-revoke", f"profiles/{profile_id}/client.crt", "-crl_reason", "cessationOfOperation")
                self.ca(path, "-gencrl", "-out", "crl.pem")
                self.fault("after_crl")
                result = {"profile_id": profile_id, "crl_number": self.crl_number(path)}
                record.update(revoked=True, revoke=result)
                write_json(folder / "record.json", record)
            else:
                result = record["revoke"]
            write_json(path / "receipts" / (job_id + ".json"), {"request_hash": digest, "result": result})
            self.protect(path)
            self.commit(path)
            return result

    def crl(self) -> dict:
        """Stage-5 public CRL export, with daily serialized refresh, never keys."""
        with self.locked():
            path = self.current()
            dates = self.run(["/usr/bin/openssl", "crl", "-in", "crl.pem", "-noout", "-lastupdate", "-nextupdate"], path).decode().splitlines()
            updated, expires = [datetime.strptime(line.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc) for line in dates]
            now = datetime.now(timezone.utc)
            if updated > now:
                raise PkiError("storage_error")
            if (now - updated).total_seconds() >= 86400 or (expires - now).total_seconds() < 86400:
                candidate = self.transaction(path)
                self.ca(candidate, "-gencrl", "-out", "crl.pem")
                self.protect(candidate)
                self.fault("after_crl")
                self.commit(candidate)
                path = candidate
            pem = read_file(path / "crl.pem")
            if len(pem) > 65536:
                raise PkiError("operation_failed")
            return {"crl_base64": base64.b64encode(pem).decode(),
                    "ca_base64": base64.b64encode(read_file(path / "ca/ca.crt")).decode()}

    def dispatch(self, request: object) -> dict:
        if not isinstance(request, dict):
            raise PkiError("invalid_request")
        operation = request.get("operation")
        fields = {"issue": {"operation", "profile_id", "job_id", "mode", "expires_at"},
                  "download": {"operation", "profile_id"}, "revoke": {"operation", "profile_id", "job_id"},
                  "crl": {"operation"}, "legacy_catalog": {"operation", "after", "generation"}}
        if not isinstance(operation, str) or operation not in fields or set(request) != fields[operation]:
            raise PkiError("invalid_request")
        args = {key: value for key, value in request.items() if key != "operation"}
        if operation == "legacy_catalog":
            from .legacy import catalog
            return catalog(self, **args)
        return getattr(self, operation)(**args)
