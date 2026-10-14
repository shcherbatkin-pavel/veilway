#!/usr/bin/env python3
"""Test orchestration without Docker, network or deployed resources."""
import importlib.util
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from lib.test_resources import disposable_postgres, smoke_cleanup

spec = importlib.util.spec_from_file_location("acceptance_runner", Path(__file__).with_name("run-tests.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def test_build_is_shared_only_within_one_invocation(self):
        tests = runner.TestRun()
        with patch.object(tests, "run") as run:
            tests.backend(); tests.backend()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][:2], ["docker", "build"])
        with patch.object(runner.TestRun, "run") as run:
            runner.TestRun().backend()
            self.assertEqual(run.call_count, 1)

    def test_failed_build_cannot_mark_image_prepared(self):
        tests = runner.TestRun()
        with patch.object(tests, "run", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError): tests.backend()
        self.assertEqual(tests.built, set())

    def test_postgres_cleanup_on_success_failure_and_interrupt(self):
        for failure in (None, RuntimeError("test failed"), KeyboardInterrupt()):
            calls = []
            def run(args, **options):
                calls.append((args, options))
                return subprocess.CompletedProcess(args, 0)
            with patch("lib.test_resources.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
                try:
                    with disposable_postgres(run) as name:
                        self.assertTrue(name.startswith("veilway-security-postgres-"))
                        if failure: raise failure
                except (RuntimeError, KeyboardInterrupt) as error:
                    self.assertIs(error, failure)
            start, stop = calls[0][0], calls[-1][0]
            self.assertIn("none", start)
            self.assertIn("--read-only", start)
            self.assertNotIn("--publish", start)
            self.assertEqual(stop, ["docker", "stop", "--time", "5", name])

    def test_startup_interruption_still_attempts_owned_container_cleanup(self):
        calls = []
        def run(args, **options):
            calls.append(args)
            if args[1] == "run": raise KeyboardInterrupt()
            return subprocess.CompletedProcess(args, 0)
        with self.assertRaises(KeyboardInterrupt):
            with disposable_postgres(run): self.fail("not ready")
        self.assertEqual(calls[-1][:2], ["docker", "stop"])
        self.assertEqual(calls[0][calls[0].index("--name") + 1], calls[-1][-1])

    def test_postgres_readiness_timeout_cleans_up(self):
        calls = []
        def run(args, **options):
            calls.append(args); return subprocess.CompletedProcess(args, 0)
        with patch("lib.test_resources.subprocess.run", return_value=subprocess.CompletedProcess([], 1)), patch("lib.test_resources.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "not become ready"):
                with disposable_postgres(run): self.fail("not ready")
        self.assertEqual(calls[-1][:2], ["docker", "stop"])

    def test_smoke_cleanup_preserves_compose_lifecycle_scenario(self):
        with patch("lib.test_resources.subprocess.run") as run, patch.object(Path, "exists", return_value=False):
            with self.assertRaises(KeyboardInterrupt):
                with smoke_cleanup(["docker", "compose", "--project-name", "synthetic"], Path("/tmp/owned-fixture"),
                                   Path("/tmp/owned-fixture/data"), {}, runner.ROOT):
                    raise KeyboardInterrupt()
            self.assertEqual(run.call_args.args[0][-3:], ["down", "--volumes", "--remove-orphans"])

    def test_full_suite_keeps_all_required_levels_and_fails_on_first_error(self):
        tests = runner.TestRun(); order = []
        with patch.object(runner.shutil, "which", return_value="available"), patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=True), \
             patch.object(tests, "run", side_effect=lambda args: order.append(args)), \
             patch.object(runner.subprocess, "run", side_effect=lambda *args, **kw: order.append("android")), \
             patch.object(tests, "control_plane", side_effect=lambda: order.append("postgres-and-lifecycle")), \
             patch.object(tests, "pki", side_effect=lambda: order.append("pki")), \
             patch.object(tests, "browser", side_effect=lambda: order.append("browser")), \
             patch.object(tests, "accounting", side_effect=lambda: order.append("native-accounting")):
            with redirect_stdout(StringIO()):
                tests.all()
        self.assertEqual(order[-5:], ["android", "postgres-and-lifecycle", "pki", "browser", "native-accounting"])
        with patch.object(runner.shutil, "which", return_value=None), patch.object(tests, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "required checks unavailable"): tests.all()
            run.assert_not_called()

    def test_missing_providers_cannot_silently_skip_required_validation(self):
        tests = runner.TestRun()
        with patch.object(runner.shutil, "which", return_value="available"), \
             patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=False), \
             patch.object(tests, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Terraform providers missing"): tests.all()
            run.assert_not_called()

    def test_missing_docker_is_a_failure_without_installing_or_running(self):
        with patch.object(runner.sys, "argv", ["run-tests.py", "pki", "--build"]), \
             patch.object(runner.shutil, "which", return_value=None), redirect_stderr(StringIO()), \
             patch.object(runner.TestRun, "run") as run:
            self.assertEqual(runner.main(), 1)
            run.assert_not_called()

    def test_cli_requires_build_and_rejects_arbitrary_suite(self):
        for arguments in (["run-tests.py", "pki"], ["run-tests.py", "arbitrary", "--build"]):
            with patch.object(runner.sys, "argv", arguments), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error: runner.main()
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
