#!/usr/bin/env python3
"""Explicit local acceptance suites with one image build per invocation."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from lib.test_resources import disposable_postgres

ROOT = Path(__file__).resolve().parent.parent


def local_module(name: str):
    path = ROOT / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRun:
    def __init__(self):
        # State belongs only to this invocation: an old tag never implies a build.
        self.built: set[str] = set()

    def run(self, args: list[str], *, check: bool = True, **kwargs):
        return subprocess.run(args, cwd=ROOT, check=check, **kwargs)

    def build(self, key: str, args: list[str]):
        if key not in self.built:
            self.run(["docker", "build", *args])
            self.built.add(key)

    def backend(self):
        self.build("backend", ["--file", "web/backend/Dockerfile.test", "--tag", "veilway-control-api-test:stage4", "web/backend"])

    def integration(self, *, postgres: bool = True):
        self.backend()
        tag = "veilway-profile-integration-test:stage7" if postgres else "veilway-profile-integration-test:stage5"
        self.build(tag, ["--file", "web/pki/Dockerfile.integration", "--build-context", "backend=web/backend",
                         "--build-context", "agent=deploy/roles/veilway_crl_agent/files", "--tag", tag, "web/pki"])
        temporary_storage = "/tmp:rw,noexec,nosuid,size=96m" if postgres else "/tmp:rw,noexec,nosuid,size=64m"
        args = ["docker", "run", "--rm", "--pull", "never", "--read-only", "--tmpfs", temporary_storage,
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true"]
        if postgres:
            with disposable_postgres(self.run) as name:
                self.run([*args, "--network", "container:" + name, "--env",
                          "VEILWAY_TEST_POSTGRES_URL=postgresql+psycopg://postgres:synthetic-stage7-test-only@127.0.0.1:5432/veilway_test", tag])
        else:
            self.run([*args, "--network", "none", tag])
            print("PostgreSQL acceptance is separate: scripts/test-profile-security.sh --build", flush=True)

    def pki(self):
        self.build("pki", ["--tag", "veilway-control-pki:0.1.0", "web/pki"])
        self.build("pki-tests", ["--file", "web/pki/Dockerfile.test", "--tag", "veilway-control-pki-test:0.1.0", "web/pki"])
        self.run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                  "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
                  "--tmpfs", "/var/lib/veilway-pki:rw,noexec,nosuid,size=64m,mode=0700",
                  "--tmpfs", "/run/veilway-pki:rw,noexec,nosuid,size=1m,mode=0700",
                  "--tmpfs", "/run/secrets:rw,noexec,nosuid,size=1m,mode=0700",
                  "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID", "--cap-add", "SETGID",
                  "--security-opt", "no-new-privileges:true", "veilway-control-pki-test:0.1.0"])

    def browser(self):
        self.build("browser", ["--file", "web/frontend/Dockerfile.browser", "--tag", "veilway-profile-panel-test:stage6", "web/frontend"])
        self.run(["docker", "run", "--rm", "--init", "--pull", "never", "--network", "none", "--read-only",
                  "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m", "--shm-size", "256m", "--cap-drop", "ALL",
                  "--security-opt", "no-new-privileges:true", "veilway-profile-panel-test:stage6"])

    def compose_contract(self):
        result = self.run(["docker", "compose", "--file", "web/compose.yaml", "config", "--format", "json"],
                          stdout=subprocess.PIPE)
        self.run([sys.executable, "scripts/validate-control-compose.py"], input=result.stdout)

    def smoke(self):
        self.build("api", ["--file", "web/backend/Dockerfile", "--tag", "veilway-control-api:0.1.0", "web/backend"])
        local_module("control-postgres-smoke").main(build=False)

    def control_plane(self):
        self.integration()
        self.build("frontend", ["--file", "web/frontend/Dockerfile", "--target", "build",
                                "--tag", "veilway-control-frontend-test:0.1.0", "web/frontend"])
        self.compose_contract()
        self.smoke()

    def accounting(self):
        self.build("accounting", ["--file", "deploy/image/Dockerfile", "--tag", "veilway-analytics-vpn-test:local", "deploy/image"])
        # This existing program uses a private namespace and synthetic PKI only.
        self.run([sys.executable, "scripts/test-business-analytics-vpn.py"])

    def all(self):
        required = ("docker", "npm", "node", "java", "ansible-playbook", "terraform")
        missing = [name for name in required if shutil.which(name) is None]
        if missing:
            raise RuntimeError("required checks unavailable: " + ", ".join(missing))
        if not (ROOT / "web/frontend/node_modules/.bin/tsc").is_file():
            raise RuntimeError("frontend node_modules missing; install pinned dependencies explicitly before testing")
        terraform_roots = ("infra/aws", "infra/yandex", "infra/aws-management", "infra/yandex-web")
        unprepared = [name for name in terraform_roots if not (ROOT / name / ".terraform").is_dir()]
        if unprepared:
            raise RuntimeError("Terraform providers missing for required validation: " + ", ".join(unprepared))
        if not (ROOT / "android/local.properties").is_file():
            raise RuntimeError("Android local.properties missing; configure the local SDK explicitly before testing")
        self.run(["./scripts/check.sh"])
        self.run(["npm", "--prefix", "web/frontend", "run", "build"])
        subprocess.run(["./gradlew", "--offline", "testDebugUnitTest", "lintDebug"],
                       cwd=ROOT / "android", check=True)
        self.control_plane()
        self.pki()
        self.browser()
        self.accounting()
        print("All required static/unit/integration/browser/native-accounting checks passed.", flush=True)
        print("Android JNI/emulator acceptance remains separate and is not implied by this result.", flush=True)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("all", "control-plane", "profile-security", "profile-api", "pki", "browser"))
    parser.add_argument("--build", action="store_true", required=True,
                        help="explicitly permit local image builds; no deployed resources are used")
    arguments = parser.parse_args()
    if shutil.which("docker") is None:
        print("Required command unavailable: docker", file=sys.stderr)
        return 1
    os.umask(0o077)
    signal.signal(signal.SIGTERM, interrupted)
    tests = TestRun()
    suites = {"all": tests.all, "control-plane": tests.control_plane, "profile-security": tests.integration,
              "profile-api": lambda: tests.integration(postgres=False), "pki": tests.pki, "browser": tests.browser}
    try:
        suites[arguments.suite]()
    except KeyboardInterrupt:
        print("Test run interrupted; disposable resources cleaned up.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Required test run failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
