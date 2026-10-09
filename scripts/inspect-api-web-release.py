#!/usr/bin/env python3
"""Operator-invoked, local read-only rollout checks; never start/stop containers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
FORMAT = '{"image":{{json .Image}},"running":{{json .State.Running}},"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}}}'
INPUTS = {
    "backend": (".dockerignore", "Dockerfile", "alembic.ini", "container-entrypoint.py", "migrations", "pyproject.toml", "src"),
    "frontend": (".dockerignore", "Dockerfile", "Caddyfile", "index.html", "package.json", "package-lock.json", "src", "tests", "tsconfig.app.json", "tsconfig.json", "vite.config.ts"),
}


def regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected regular file")


def digest(path: Path) -> str:
    regular(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def allowed(name: str) -> bool:
    if name == "compose.yaml":
        return True
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        return False
    if any(part.startswith(".env") for part in path.parts) or path.suffix in (".key", ".pem", ".crt", ".ovpn"):
        return False
    component, first = path.parts[:2]
    return component in INPUTS and first in INPUTS[component] and (
        len(path.parts) == 2 or first in ("src", "migrations", "tests")
    )


def output(*command: str) -> str:
    return subprocess.check_output([*DOCKER, *command], stderr=subprocess.DEVNULL).decode().strip()


def snapshot(app: Path) -> dict:
    services = {}
    for service in ("api", "web", "db", "pki"):
        container = output("compose", "--project-directory", str(app), "--file", str(app / "compose.yaml"), "ps", "--quiet", service)
        if not re.fullmatch(r"[a-f0-9]{12,64}", container):
            raise ValueError("expected one running container per service")
        state = json.loads(output("inspect", "--format", FORMAT, container))
        if state.get("running") is not True or not re.fullmatch(r"sha256:[a-f0-9]{64}", state.get("image", "")):
            raise ValueError("service is not running")
        services[service] = {"container": container, **state}
    return services


def inspect_release(release: Path, app: Path, *, require_images: bool, verify_preserved: Path | None = None,
                    verify_candidate: bool = False, verify_rollback: bool = False) -> dict:
    regular(release / "release.json")
    manifest = json.loads((release / "release.json").read_text())
    if manifest.get("version") != 1:
        raise ValueError("unknown manifest version")
    if digest(release / "images.tar") != manifest["archive_sha256"]:
        raise ValueError("archive checksum mismatch")
    if digest(release / "inspect-api-web-release.py") != manifest["checker_sha256"]:
        raise ValueError("checker checksum mismatch")
    baseline = manifest["baseline_files"]
    if not baseline or "compose.yaml" not in baseline:
        raise ValueError("missing baseline")
    for name, expected in baseline.items():
        if not allowed(name) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("invalid baseline path or digest")
        target = app / name
        # Do not follow any directory symlinks into unrelated/private storage.
        if any(parent.is_symlink() for parent in (target, *target.parents)):
            raise ValueError("symlink in baseline input")
        if digest(target) != expected:
            raise ValueError("installation differs from expected baseline")
    images = manifest["images"]
    if set(images) != {"api", "web"}:
        raise ValueError("only API/web images are supported")
    expected_override = {"services": {service: {"image": image["tag"]} for service, image in images.items()}}
    regular(release / "compose.override.json")
    if json.loads((release / "compose.override.json").read_text()) != expected_override:
        raise ValueError("override must contain only API/web image references")
    for service, image in images.items():
        if not re.fullmatch(f"veilway-control-{service}:[a-f0-9]{{12}}", image["tag"]):
            raise ValueError("invalid candidate image tag")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image["id"]):
            raise ValueError("invalid candidate image ID")
        if require_images and output("image", "inspect", "--format", "{{.Id}}", image["tag"]) != image["id"]:
            raise ValueError("candidate image mismatch")
    current = snapshot(app)
    if require_images:
        for service in ("api", "web"):
            running_platform = output("image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", current[service]["image"])
            candidate_platform = output("image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", images[service]["id"])
            if running_platform != candidate_platform or not re.fullmatch(r"linux/[a-z0-9_]+", candidate_platform):
                raise ValueError("candidate image platform mismatch")
    if verify_candidate and any(current[service]["image"] != images[service]["id"] for service in ("api", "web")):
        raise ValueError("running candidate image mismatch")
    if verify_preserved is not None:
        regular(verify_preserved)
        saved = json.loads(verify_preserved.read_text())
        if saved["x-veilway-preserved"] != {service: current[service] for service in ("db", "pki")}:
            raise ValueError("DB/PKI containers changed")
        if verify_rollback and any(current[service]["image"] != saved["services"][service]["image"] for service in ("api", "web")):
            raise ValueError("running rollback image mismatch")
    elif verify_rollback:
        raise ValueError("rollback verification requires saved metadata")
    return {
        "services": {service: {"image": current[service]["image"]} for service in ("api", "web")},
        "x-veilway-preserved": {service: current[service] for service in ("db", "pki")},
    }


def write_rollback(path: Path, value: dict) -> None:
    if path.parent.is_symlink() or stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise ValueError("rollback directory must be protected")
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, default=Path("/opt/veilway-control/app"))
    parser.add_argument("--require-images", action="store_true")
    parser.add_argument("--write-rollback", type=Path, help="explicitly save protected API/web image rollback references")
    parser.add_argument("--verify-preserved", type=Path, help="compare DB/PKI IDs/start times/restarts with saved rollback metadata")
    verification = parser.add_mutually_exclusive_group()
    verification.add_argument("--verify-candidate", action="store_true", help="verify running API/web image IDs")
    verification.add_argument("--verify-rollback", action="store_true", help="verify running API/web match saved rollback images")
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        rollback = inspect_release(arguments.release_dir.absolute(), arguments.app_root.absolute(),
                                   require_images=arguments.require_images, verify_preserved=arguments.verify_preserved,
                                   verify_candidate=arguments.verify_candidate, verify_rollback=arguments.verify_rollback)
        if arguments.write_rollback:
            write_rollback(arguments.write_rollback.absolute(), rollback)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("API/web preflight failed; no container was changed", file=sys.stderr)
        return 2
    print("API/web preflight passed; no container was changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
