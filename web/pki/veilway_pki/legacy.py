"""Explicit offline import of original profiles, never executable configuration."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import re
import shutil
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .core import MODES, PkiError, check_tree, load_json, mkdir, read_file, write_file, write_json


def pem_payload(value, label):
    text = value.decode("ascii").strip()
    match = re.fullmatch(r"-----BEGIN " + re.escape(label) + r"-----\s+([A-Za-z0-9+/=\s]+)-----END " + re.escape(label) + r"-----", text)
    if not match:
        raise PkiError("invalid_import")
    return base64.b64decode(re.sub(r"\s", "", match[1]), validate=True)


def validate_wrapping(client_pem, server_pem):
    """Authenticate/decrypt the OpenVPN 2.6 wrapped key, without network or argv secrets.

    Format reference: OpenVPN src/openvpn/tls_crypt.c, v2.6.12.
    """
    client = pem_payload(client_pem, "OpenVPN tls-crypt-v2 client key")
    server = pem_payload(server_pem, "OpenVPN tls-crypt-v2 server key")
    if len(server) != 128 or not 547 <= len(client) <= 1280:
        raise PkiError("invalid_import")
    wrapped = client[256:]
    if int.from_bytes(wrapped[-2:], "big") != len(wrapped):
        raise PkiError("invalid_import")
    tag = wrapped[:32]
    decryptor = Cipher(algorithms.AES(server[:32]), modes.CTR(tag[:16])).decryptor()
    plain = decryptor.update(wrapped[32:-2]) + decryptor.finalize()
    authenticated = hmac.new(server[64:96], wrapped[-2:] + plain, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, authenticated) or not hmac.compare_digest(client[:256], plain[:256]):
        raise PkiError("invalid_import")


def parse_profile(data, mode, endpoint):
    if not 1 <= len(data) <= 65536 or b"\x00" in data:
        raise PkiError("invalid_import")
    text = data.decode("ascii")
    blocks, directives, active, content = {}, [], None, []
    for line in text.splitlines():
        stripped = line.strip()
        if active:
            if stripped == f"</{active}>":
                blocks[active] = ("\n".join(content) + "\n").encode()
                active, content = None, []
            else:
                if stripped.startswith("<"):
                    raise PkiError("invalid_import")
                content.append(line)
        elif stripped.startswith("<"):
            if stripped not in {"<ca>", "<cert>", "<key>", "<tls-crypt-v2>"} or stripped[1:-1] in blocks:
                raise PkiError("invalid_import")
            active = stripped[1:-1]
        elif stripped and not stripped.startswith(("#", ";")):
            directives.append(stripped)
    if active or set(blocks) != {"ca", "cert", "key", "tls-crypt-v2"}:
        raise PkiError("invalid_import")
    server, port = MODES[mode]
    expected = ["client", "dev tun", "proto udp4", f"remote {endpoint} {port}", "nobind", "persist-key", "persist-tun",
                "remote-cert-tls server", f"verify-x509-name {server} name", "tls-version-min 1.3", "tls-cert-profile preferred",
                "data-ciphers CHACHA20-POLY1305:AES-256-GCM:AES-128-GCM", "auth SHA256", "allow-compression no", "verb 3"]
    if mode == "yc-direct":
        expected.append("block-ipv6")
    # Preserve the earlier explicitly supported AWS port trial without rewriting.
    if mode == "aws-direct" and f"remote {endpoint} 443" in directives:
        expected[3] = f"remote {endpoint} 443"
    if sorted(directives) != sorted(expected):
        raise PkiError("invalid_import")
    return blocks


def validate_profile(store, path, data, mode, name, folder):
    blocks = parse_profile(data, mode, load_json(path / "endpoints.json")[mode])
    for tag, label in (("ca", "CERTIFICATE"), ("cert", "CERTIFICATE"), ("key", "PRIVATE KEY")):
        pem_payload(blocks[tag], label)
    cert = x509.load_pem_x509_certificate(blocks["cert"])
    ca = x509.load_pem_x509_certificate(blocks["ca"])
    actual_ca = x509.load_pem_x509_certificate(read_file(path / "ca/ca.crt"))
    der = serialization.Encoding.DER
    if ca.public_bytes(der) != actual_ca.public_bytes(der):
        raise PkiError("invalid_import")
    if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise PkiError("invalid_import")
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    if x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH not in eku:
        raise PkiError("invalid_import")
    key = serialization.load_pem_private_key(blocks["key"], password=None)
    public_format = serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(der, public_format) != cert.public_key().public_bytes(der, public_format):
        raise PkiError("invalid_import")
    serial = format(cert.serial_number, "X")
    if not re.fullmatch(r"[0-9A-F]{1,40}", serial):
        raise PkiError("invalid_import")
    rows = [line.split("\t") for line in read_file(path / "ca/index.txt").decode().splitlines()]
    row = next((row for row in rows if int(row[3], 16) == cert.serial_number), None)
    if row is None:
        raise PkiError("invalid_import")
    historical = x509.load_pem_x509_certificate(read_file(path / "ca/newcerts" / (row[3] + ".pem")))
    date_format = "%y%m%d%H%M%SZ" if len(row[1]) == 13 else "%Y%m%d%H%M%SZ"
    if (cert.public_bytes(der) != historical.public_bytes(der)
        or cert.not_valid_after_utc != datetime.strptime(row[1], date_format).replace(tzinfo=timezone.utc)):
        raise PkiError("invalid_import")
    server, _ = MODES[mode]
    validate_wrapping(blocks["tls-crypt-v2"], read_file(path / "endpoints" / server / "tls-crypt-v2-server.key"))
    for tag, filename in (("cert", "client.crt"), ("key", "client.key"), ("tls-crypt-v2", "tls-crypt-v2-client.key")):
        write_file(folder / filename, blocks[tag])
    store.run(["/usr/bin/openssl", "verify", "-no_check_time", "-purpose", "sslclient", "-CAfile", "ca/ca.crt",
               str(folder / "client.crt")], path)
    identifier = str(uuid.uuid5(uuid.NAMESPACE_OID, hashlib.sha256(actual_ca.public_bytes(der) + cert.public_bytes(der)).hexdigest()))
    end = cert.not_valid_after_utc
    if cert.not_valid_before_utc > datetime.now(timezone.utc) or cert.not_valid_before_utc >= end or end > actual_ca.not_valid_after_utc:
        raise PkiError("invalid_import")
    result = dict(profile_id=identifier, mode=mode, expires_at=end.strftime("%Y-%m-%dT%H:%M:%SZ"), serial=serial,
                  certificate_sha256=hashlib.sha256(blocks["cert"]).hexdigest())
    status = "revoked" if row[0] == "R" else ("expired" if end <= datetime.now(timezone.utc) else "active")
    metadata = dict(**result, device_name=name, created_at=cert.not_valid_before_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    status=status, import_sha256=hashlib.sha256(data).hexdigest())
    write_file(folder / "client.ovpn", data)
    record = {"issue": result, "revoked": status == "revoked", "legacy": metadata}
    if status == "revoked":
        record["revoke"] = {"profile_id": identifier, "crl_number": store.crl_number(path)}
    write_json(folder / "record.json", record)
    return metadata


def import_profiles(store, source: Path):
    """Atomic bundle import. Separate from import-ca and unavailable through RPC."""
    with store.locked():
        try:
            check_tree(source)
            manifest = load_json(source / "manifest.json")
            if not isinstance(manifest, dict) or set(manifest) != {"version", "profiles"} or type(manifest["version"]) is not int or manifest["version"] != 1:
                raise PkiError("invalid_import")
            items = manifest["profiles"]
            if not isinstance(items, list) or not 1 <= len(items) <= 1000:
                raise PkiError("invalid_import")
            files = set()
            for item in items:
                if (not isinstance(item, dict) or set(item) != {"file", "mode", "device_name"}
                    or not isinstance(item["file"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.ovpn", item["file"])
                    or item["file"] in files or not isinstance(item["mode"], str) or item["mode"] not in MODES
                    or not isinstance(item["device_name"], str) or not 1 <= len(item["device_name"]) <= 128
                    or item["device_name"] != item["device_name"].strip()
                    or any(ord(c) < 32 or ord(c) == 127 for c in item["device_name"])):
                    raise PkiError("invalid_import")
                files.add(item["file"])
            if {child.name for child in source.iterdir()} != files | {"manifest.json"}:
                raise PkiError("invalid_import")
            previous = store.current()
            path = store.transaction(previous)
            results, seen, changed = [], set(), False
            for index, item in enumerate(items):
                folder = path / ("legacy-import-" + str(index))
                mkdir(folder)
                metadata = validate_profile(store, path, read_file(source / item["file"]), item["mode"], item["device_name"], folder)
                identifier = metadata["profile_id"]
                if identifier in seen:
                    raise PkiError("conflict")
                seen.add(identifier)
                destination = path / "profiles" / identifier
                # No second profile may represent the same serial, including issued profiles.
                for existing_folder in (path / "profiles").iterdir():
                    record = load_json(existing_folder / "record.json")
                    if int(record["issue"]["serial"], 16) == int(metadata["serial"], 16) and existing_folder.name != identifier:
                        raise PkiError("conflict")
                if destination.exists():
                    original = load_json(destination / "record.json").get("legacy")
                    # Current revocation/expiry may have changed since the first import.
                    fields = set(metadata) - {"status"}
                    if original is None or any(original[field] != metadata[field] for field in fields):
                        raise PkiError("conflict")
                    shutil.rmtree(folder)
                else:
                    folder.rename(destination)
                    changed = True
                results.append(identifier)
            store.fault("after_legacy_validation")
            if changed:
                store.protect(path)
                store.commit(path)
            else:
                store.recover()
            return results
        except PkiError:
            raise
        except Exception:
            raise PkiError("invalid_import") from None


def catalog(store, after, generation):
    from .core import canonical_uuid, expiry
    if after != "":
        canonical_uuid(after)
    if generation != "":
        canonical_uuid(generation)
    with store.locked():
        path = store.current()
        if generation and generation != path.name:
            raise PkiError("conflict")
        identifiers = sorted(folder.name for folder in (path / "profiles").iterdir() if folder.name > after)
        records, cursor = [], ""
        for identifier in identifiers:
            record = load_json(path / "profiles" / identifier / "record.json")
            cursor = identifier
            if "legacy" in record:
                metadata = dict(record["legacy"])
                metadata["status"] = "revoked" if record["revoked"] else ("expired" if expiry(metadata["expires_at"]) <= datetime.now(timezone.utc) else "active")
                records.append(metadata)
            if len(records) == 64:
                break
        more = bool(cursor and any(identifier > cursor for identifier in identifiers))
        return {"generation": path.name, "profiles": records, "next_after": cursor if more else ""}
