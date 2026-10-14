"""Security boundaries independent of CA generation transactions."""
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from veilway_pki.commands import run_command
from veilway_pki.errors import PkiError
from veilway_pki.filesystem import read_file, write_file


class BoundaryTests(unittest.TestCase):
    def test_password_fd_is_sealed_and_closed_without_entering_argv(self):
        descriptors = []
        def child(args, **options):
            fd, = options["pass_fds"]
            descriptors.append(fd)
            self.assertEqual(os.read(fd, 100), b"synthetic-passphrase\n")
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GET_SEALS),
                             fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            self.assertNotIn("synthetic-passphrase", " ".join(args))
            self.assertFalse(options["shell"])
            self.assertEqual(options["timeout"], 30)
            self.assertEqual(options["umask"], 0o077)
            with self.assertRaises(OSError): os.write(fd, b"changed")
            return subprocess.CompletedProcess(args, 0, b"public result")
        with patch("veilway_pki.commands.subprocess.run", child):
            self.assertEqual(run_command(["/usr/bin/openssl", "version"], Path("/tmp"),
                                         passphrase=b"synthetic-passphrase"), b"public result")
        with self.assertRaises(OSError): os.fstat(descriptors[0])

    def test_failures_and_timeout_expose_only_fixed_codes(self):
        for failure in (OSError("private child detail"), subprocess.TimeoutExpired("private detail", 30)):
            with patch("veilway_pki.commands.subprocess.run", side_effect=failure):
                with self.assertRaises(PkiError) as error:
                    run_command(["/usr/bin/openssl", "version"], Path("/tmp"))
                self.assertEqual(str(error.exception), "operation_failed")
        with patch("veilway_pki.commands.subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"private")):
            with self.assertRaises(PkiError): run_command(["/usr/bin/openssl", "version"], Path("/tmp"))

    def test_unknown_executable_is_never_started(self):
        with patch("veilway_pki.commands.subprocess.run") as child:
            with self.assertRaises(PkiError): run_command(["/bin/sh", "-c", "ignored"], Path("/tmp"))
            child.assert_not_called()

    def test_file_boundary_rejects_links_and_normalizes_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / "target"
            write_file(target, b"synthetic data")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            link = root / "link"; link.symlink_to(target)
            with self.assertRaises(OSError): read_file(link)
            with self.assertRaises(OSError): write_file(link, b"replacement")
            hard = root / "hard"; os.link(target, hard)
            with self.assertRaises(PkiError): read_file(hard)
            self.assertEqual(target.read_bytes(), b"synthetic data")
