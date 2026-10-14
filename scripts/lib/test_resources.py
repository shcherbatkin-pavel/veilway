"""Disposable local resources shared by acceptance scenarios, never deployed hosts."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import time
import uuid

POSTGRES_IMAGE = "postgres:17.4-alpine"


def test_name(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:8]


@contextmanager
def disposable_postgres(run):
    name = test_name("veilway-security-postgres")
    attempted = False
    started = False
    try:
        attempted = True
        run(["docker", "run", "--detach", "--rm", "--pull", "never", "--name", name,
             "--network", "none", "--read-only", "--security-opt", "no-new-privileges:true",
             "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=256m",
             "--tmpfs", "/var/run/postgresql:rw,noexec,nosuid,size=8m",
             "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
             "--env", "POSTGRES_PASSWORD=synthetic-stage7-test-only", "--env", "POSTGRES_DB=veilway_test",
             POSTGRES_IMAGE], stdout=subprocess.DEVNULL)
        started = True
        for _ in range(30):
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "--username", "postgres", "--dbname", "veilway_test"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                yield name
                break
            time.sleep(1)
        else:
            raise RuntimeError("disposable PostgreSQL did not become ready")
    finally:
        if attempted:
            stopped = run(["docker", "stop", "--time", "5", name], stdout=subprocess.DEVNULL, check=False)
            if started and stopped.returncode != 0:
                raise RuntimeError("failed to clean up disposable PostgreSQL")


def smoke_directories(root: Path) -> tuple[Path, Path, Path, Path]:
    directories = (root / "secrets", root / "data", root / "run", root / "pki-run")
    for path, mode in zip(directories, (0o700, 0o777, 0o777, 0o755)):
        path.mkdir(mode=mode)
        # Explicit fixture permissions must not depend on the runner umask.
        path.chmod(mode)
    return directories


@contextmanager
def smoke_cleanup(compose: list[str], root: Path, data_root: Path, environment: dict, cwd: Path):
    try:
        yield
    finally:
        subprocess.run(compose + ["down", "--volumes", "--remove-orphans"], cwd=cwd, env=environment, check=False)
        if (data_root / "postgres").exists():
            subprocess.run(["docker", "run", "--rm", "--volume", f"{root}:/cleanup", POSTGRES_IMAGE,
                            "rm", "-rf", "--", "/cleanup/data/postgres"], cwd=cwd, check=True)
