#!/usr/bin/env python3
"""Export and optionally build a committed API/web-only release on local Docker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
INPUTS = {
    "backend": (".dockerignore", "Dockerfile", "alembic.ini", "container-entrypoint.py", "migrations", "pyproject.toml", "src"),
    "frontend": (".dockerignore", "Dockerfile", "Caddyfile", "index.html", "package.json", "package-lock.json", "src", "tests", "tsconfig.app.json", "tsconfig.json", "vite.config.ts"),
}


def git(*arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, stderr=subprocess.DEVNULL)


def revision(value: str) -> str:
    result = git("rev-parse", "--verify", "--end-of-options", value + "^{commit}").decode().strip()
    if not re.fullmatch(r"[a-f0-9]{40}", result):
        raise ValueError("invalid revision")
    return result


def source_files(commit: str) -> dict[str, bytes]:
    paths = [f"web/{component}/{name}" for component, names in INPUTS.items() for name in names]
    paths.append("web/compose.yaml")
    result = {}
    for record in git("ls-tree", "-rz", commit, "--", *paths).split(b"\0"):
        if not record:
            continue
        header, filename = record.split(b"\t", 1)
        mode, kind, object_id = header.split()
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ValueError("release inputs must be regular tracked files")
        name = filename.decode()
        relative = Path(name).relative_to("web")
        if ".." in relative.parts:
            raise ValueError("invalid release path")
        result[str(relative)] = git("cat-file", "blob", object_id.decode())
    for component, names in INPUTS.items():
        for name in names:
            prefix = f"{component}/{name}"
            if not any(path == prefix or path.startswith(prefix + "/") for path in result):
                raise ValueError("missing build input")
    if "compose.yaml" not in result:
        raise ValueError("missing Compose manifest")
    return result


def validate_compatibility(base: dict[str, bytes], candidate: dict[str, bytes]) -> None:
    # Only application sources/tests may change in this deployment path.
    for name in base.keys() | candidate.keys():
        if name.startswith(("backend/src/", "frontend/src/", "frontend/tests/")):
            continue
        if base.get(name) != candidate.get(name):
            raise ValueError("release requires broader deployment or migration review")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def export_contexts(contexts: Path, candidate: dict[str, bytes]) -> None:
    for name, contents in candidate.items():
        if name == "compose.yaml":
            continue
        target = contexts / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        parent = target.parent
        while parent != contexts.parent:
            parent.chmod(0o755)
            parent = parent.parent
        target.write_bytes(contents)
        # COPY preserves modes; runtime users must read public code/config.
        # The enclosing release directory remains protected with mode 0700.
        target.chmod(0o644)


def prepare(commit: str, base_commit: str, output: Path, *, build: bool) -> None:
    base, candidate = source_files(base_commit), source_files(commit)
    validate_compatibility(base, candidate)
    if not build:
        print("validated committed API/web inputs; no images built, no host contacted")
        return
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    contexts = output / "build"
    export_contexts(contexts, candidate)
    tags = {service: f"veilway-control-{service}:{commit[:12]}" for service in ("api", "web")}
    for service, component in (("api", "backend"), ("web", "frontend")):
        context = contexts / component
        command = [*DOCKER, "build", "--no-cache", "--file", str(context / "Dockerfile"), "--tag", tags[service], str(context)]
        print("local build:", " ".join(command), flush=True)
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Validate public runtime inputs as the actual unprivileged service user.
    checks = {
        "web": ["/bin/sh", "-c", "test -r /etc/caddy/Caddyfile && test -r /srv/veilway/index.html"],
        "api": ["python", "-c", "import os; from pathlib import Path; assert os.access('/app/alembic.ini',os.R_OK); assert os.access('/app/migrations/versions',os.R_OK|os.X_OK); files=list(Path('/app/migrations').rglob('*.py')); assert files and all(os.access(p,os.R_OK) for p in files)"],
    }
    for service, check in checks.items():
        subprocess.run([*DOCKER, "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--user", "10001:10001",
                        "--entrypoint", check[0], tags[service], *check[1:]], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    images = {}
    for service, tag in tags.items():
        image_id = subprocess.check_output([*DOCKER, "image", "inspect", "--format", "{{.Id}}", tag], stderr=subprocess.DEVNULL).decode().strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise ValueError("invalid local image ID")
        images[service] = {"tag": tag, "id": image_id}
    archive = output / "images.tar"
    command = [*DOCKER, "image", "save", "--output", str(archive), tags["api"], tags["web"]]
    print("local export:", " ".join(command), flush=True)
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    archive.chmod(0o600)
    checker = output / "inspect-api-web-release.py"
    checker.write_bytes((ROOT / "scripts/inspect-api-web-release.py").read_bytes())
    checker.chmod(0o600)
    write_json(output / "compose.override.json", {"services": {service: {"image": tag} for service, tag in tags.items()}})
    write_json(output / "release.json", {
        "version": 1, "revision": commit, "base_revision": base_commit,
        "images": images, "archive_sha256": digest(archive), "checker_sha256": digest(checker),
        "baseline_files": {name: hashlib.sha256(value).hexdigest() for name, value in base.items()},
    })
    print("release prepared; images and metadata are protected local artifacts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="origin/main")
    parser.add_argument("--base-revision", required=True, help="expected source revision of the existing installation")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build", action="store_true", help="build/export only API/web images using local Docker")
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        commit, base = revision(arguments.revision), revision(arguments.base_revision)
        output = (arguments.output or ROOT / "release-artifacts" / f"api-web-{commit[:12]}").absolute()
        prepare(commit, base, output, build=arguments.build)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("API/web release preparation failed; no host was contacted", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
