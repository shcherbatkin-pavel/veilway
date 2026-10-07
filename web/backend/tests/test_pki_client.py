from __future__ import annotations

import base64
import json
import socket
import struct
import threading
import uuid

import pytest

from veilway_control.pki import PkiClient, PkiUnavailable, receive


@pytest.fixture
def responder(tmp_path):
    servers = []
    threads = []

    def make(response=None, *, raw=None, length=None):
        path = tmp_path / f"pki-{len(servers)}.sock"
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(path))
        server.listen(1)
        seen = []

        def answer():
            connection, _ = server.accept()
            with connection:
                size = struct.unpack("!I", receive(connection, 4))[0]
                seen.append(json.loads(receive(connection, size)))
                payload = raw if raw is not None else json.dumps(response).encode()
                connection.sendall(struct.pack("!I", len(payload) if length is None else length) + payload)

        thread = threading.Thread(target=answer)
        thread.start()
        servers.append(server)
        threads.append(thread)
        return PkiClient(path, timeout=2), seen

    yield make
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    for server in servers:
        server.close()


def test_issue_revoke_download_are_bounded_typed_socket_calls(responder):
    profile_id, job_id = uuid.uuid4(), uuid.uuid4()
    issued = dict(profile_id=str(profile_id), mode="aws-direct", expires_at="2027-01-01T00:00:00Z", serial="1002", certificate_sha256="a" * 64)
    client, seen = responder({"ok": True, "result": issued})
    assert client.issue(profile_id, job_id, "aws-direct", issued["expires_at"]) == issued
    assert seen == [dict(operation="issue", profile_id=str(profile_id), job_id=str(job_id), mode="aws-direct", expires_at=issued["expires_at"])]
    revoked = dict(profile_id=str(profile_id), crl_number=4098)
    client, seen = responder({"ok": True, "result": revoked})
    assert client.revoke(profile_id, job_id) == revoked
    assert seen[0]["operation"] == "revoke"
    material = b"synthetic test profile"
    client, seen = responder({"ok": True, "result": {"profile_id": str(profile_id), "ovpn_base64": base64.b64encode(material).decode()}})
    assert client.download(profile_id) == material
    assert seen[0] == dict(operation="download", profile_id=str(profile_id))


@pytest.mark.parametrize("response", [{"ok": False, "error": "revoked"}, {"ok": False, "error": "private-ca-output"}, [], {"ok": True, "result": "secret"}, {"ok": 1, "result": {}}])
def test_errors_do_not_include_remote_contents(responder, response):
    client, _ = responder(response)
    with pytest.raises(PkiUnavailable) as caught:
        client.download(uuid.uuid4())
    assert str(caught.value) in {"revoked", "unavailable"}


@pytest.mark.parametrize("length,raw", [(0, b""), (131073, b""), (4, b"x"), (1, b"{")])
def test_invalid_or_truncated_framing_fails_closed(responder, length, raw):
    client, _ = responder(length=length, raw=raw)
    with pytest.raises(PkiUnavailable, match="unavailable"):
        client.download(uuid.uuid4())


@pytest.mark.parametrize("result", [{"profile_id": "wrong", "ovpn_base64": ""}, {"ovpn_base64": "private"}, {"profile_id": "same", "ovpn_base64": "!invalid!"}])
def test_download_validates_identity_and_encoding(responder, result):
    profile_id = uuid.uuid4()
    if result.get("profile_id") == "same":
        result = {**result, "profile_id": str(profile_id)}
    client, _ = responder({"ok": True, "result": result})
    with pytest.raises(PkiUnavailable):
        client.download(profile_id)


def test_socket_unavailable_is_sanitized(tmp_path):
    with pytest.raises(PkiUnavailable) as caught:
        PkiClient(tmp_path / "missing-sensitive-path.sock", timeout=0.1).download(uuid.uuid4())
    assert str(caught.value) == "unavailable"


@pytest.mark.parametrize("operation", ["issue", "revoke"])
def test_metadata_response_rejects_extra_secret_fields(responder, operation):
    client, _ = responder({"ok": True, "result": {"private_key": "synthetic-value"}})
    with pytest.raises(PkiUnavailable):
        if operation == "issue":
            client.issue(uuid.uuid4(), uuid.uuid4(), "yc-direct", "2027-01-01T00:00:00Z")
        else:
            client.revoke(uuid.uuid4(), uuid.uuid4())
