"""Typed, bounded client for the private PKI socket. No CA filesystem access."""
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
import re
import socket
import struct
import uuid


class PkiUnavailable(Exception):
    def __init__(self, code: str = "unavailable"):
        self.code = code
        super().__init__(code)


def receive(connection: socket.socket, size: int) -> bytes:
    value = bytearray()
    while len(value) < size:
        part = connection.recv(size - len(value))
        if not part:
            raise PkiUnavailable()
        value.extend(part)
    return bytes(value)


class PkiClient:
    def __init__(self, socket_path: Path, timeout: float = 120):
        self.socket_path = socket_path
        self.timeout = timeout

    def _call(self, request: dict) -> dict:
        encoded = json.dumps(request, separators=(",", ":")).encode()
        if len(encoded) > 4096:
            raise PkiUnavailable("invalid_request")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(str(self.socket_path))
                connection.sendall(struct.pack("!I", len(encoded)) + encoded)
                length = struct.unpack("!I", receive(connection, 4))[0]
                if not 1 <= length <= 131072:
                    raise PkiUnavailable()
                response = json.loads(receive(connection, length))
            if not isinstance(response, dict):
                raise PkiUnavailable()
            if response.get("ok") is not True:
                code = response.get("error")
                if code not in {"invalid_request", "conflict", "not_found", "unavailable", "expired", "revoked", "storage_error", "operation_failed"}:
                    code = "unavailable"
                raise PkiUnavailable(code)
            if set(response) != {"ok", "result"} or not isinstance(response["result"], dict):
                raise PkiUnavailable()
            return response["result"]
        except (OSError, ValueError, TypeError, RecursionError):
            raise PkiUnavailable() from None

    def issue(self, profile_id: uuid.UUID, job_id: uuid.UUID, mode: str, expires_at: str) -> dict:
        result = self._call(dict(operation="issue", profile_id=str(profile_id), job_id=str(job_id), mode=mode, expires_at=expires_at))
        if (set(result) != {"profile_id", "mode", "expires_at", "serial", "certificate_sha256"}
            or result["profile_id"] != str(profile_id) or result["mode"] != mode or result["expires_at"] != expires_at
            or not isinstance(result["serial"], str) or not re.fullmatch(r"[0-9A-F]{1,40}", result["serial"])
            or not isinstance(result["certificate_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", result["certificate_sha256"])):
            raise PkiUnavailable()
        return result

    def download(self, profile_id: uuid.UUID) -> bytes:
        result = self._call(dict(operation="download", profile_id=str(profile_id)))
        if set(result) != {"profile_id", "ovpn_base64"} or result["profile_id"] != str(profile_id):
            raise PkiUnavailable()
        try:
            value = base64.b64decode(result["ovpn_base64"], validate=True)
            if not value or len(value) > 65536:
                raise PkiUnavailable()
            return value
        except (binascii.Error, ValueError, TypeError):
            raise PkiUnavailable() from None

    def revoke(self, profile_id: uuid.UUID, job_id: uuid.UUID) -> dict:
        result = self._call(dict(operation="revoke", profile_id=str(profile_id), job_id=str(job_id)))
        if (set(result) != {"profile_id", "crl_number"} or result["profile_id"] != str(profile_id)
            or type(result["crl_number"]) is not int or not 1 <= result["crl_number"] <= 2**63 - 1):
            raise PkiUnavailable()
        return result

    def crl(self) -> tuple[bytes, bytes]:
        result = self._call({"operation": "crl"})
        if set(result) != {"crl_base64", "ca_base64"}:
            raise PkiUnavailable()
        try:
            crl = base64.b64decode(result["crl_base64"], validate=True)
            ca = base64.b64decode(result["ca_base64"], validate=True)
            if not 1 <= len(crl) <= 65536 or not 1 <= len(ca) <= 16384:
                raise PkiUnavailable()
            return crl, ca
        except (ValueError, TypeError):
            raise PkiUnavailable() from None

    def legacy_catalog(self, after: str = "", generation: str = "") -> dict:
        from .legacy_profiles import LegacyPage
        try:
            return LegacyPage.model_validate(self._call(dict(operation="legacy_catalog", after=after, generation=generation))).model_dump(mode="json")
        except ValueError:
            raise PkiUnavailable() from None
