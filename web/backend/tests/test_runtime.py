from __future__ import annotations

import fcntl
import importlib.util
import os
from pathlib import Path

import pytest

from veilway_control.config import Settings


def entrypoint():
    path = Path(__file__).resolve().parents[1] / "container-entrypoint.py"
    spec = importlib.util.spec_from_file_location("test_container_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oauth_inputs_use_file_paths_and_sealed_inherited_descriptors(tmp_path):
    runtime = entrypoint()
    expected = {
        "VEILWAY_GOOGLE_CLIENT_ID_FILE": "google_client_id",
        "VEILWAY_GOOGLE_CLIENT_SECRET_FILE": "google_client_secret",
        "VEILWAY_ADMIN_GOOGLE_EMAIL_FILE": "admin_google_email",
    }
    for variable, filename in expected.items():
        assert runtime.SECRET_PATHS[variable] == Path("/run/secrets") / filename
    assert len(runtime.SECRET_PATHS) == 6
    source = tmp_path / "test-only-google-client-secret"
    source.write_text("test-only-value\n")
    source.chmod(0o600)
    fd = runtime.protected_secret_fd("test-only", source)
    try:
        assert os.get_inheritable(fd)
        assert Settings.read_secret(Path(f"/proc/self/fd/{fd}")) == "test-only-value"
        assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE
        with pytest.raises(OSError):
            os.write(fd, b"changed")
    finally:
        os.close(fd)


def test_production_server_disables_raw_access_logs():
    command, socket = entrypoint().command_for(["serve"])
    assert command[0] == "uvicorn" and "--no-access-log" in command
    assert socket == Path("/run/veilway/api.sock")
