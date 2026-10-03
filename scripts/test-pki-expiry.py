#!/usr/bin/env python3
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone
import unittest
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location("expiry", Path(__file__).with_name("pki-expiry.py"))
expiry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(expiry)


class ExpiryTests(unittest.TestCase):
    def test_openssl_expiry(self):
        with tempfile.TemporaryDirectory(prefix="veilway-expiry.") as directory:
            key, cert = str(Path(directory) / "key.pem"), str(Path(directory) / "cert.pem")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=expiry-test", "-days", "1", "-keyout", key, "-out", cert], check=True, capture_output=True)
            end = expiry.certificate_end(cert)
            for offset, accepted in ((-1, True), (1, False)):
                result = subprocess.run(["openssl", "verify", "-CAfile", cert, "-attime", str(int(end.timestamp()) + offset), cert], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, accepted)
                if not accepted:
                    self.assertIn("certificate has expired", result.stderr)
            result = subprocess.run(["python3", str(Path(__file__).with_name("pki-expiry.py")), "--ca", cert, "--valid-for", "1mo"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exceeds CA expiry", result.stderr)

    def test_calendar(self):
        now = datetime(2024, 1, 31, 5, 21, tzinfo=timezone.utc)
        for duration, expected in (("1mo", (2024, 2, 29)), ("3mo", (2024, 4, 30)), ("6mo", (2024, 7, 31)), ("12mo", (2025, 1, 31))):
            self.assertEqual(expiry.calculate(now, duration), datetime(*expected, 5, 21, tzinfo=timezone.utc))
        leap = datetime(2024, 2, 29, tzinfo=timezone.utc)
        self.assertEqual(expiry.calculate(leap, "12mo").day, 28)

    def test_minutes_default_and_timezone(self):
        now = datetime(2026, 10, 3, 5, 21, tzinfo=timezone.utc)
        for minutes in (5, 10, 17):
            self.assertEqual(expiry.calculate(now, f"{minutes}m"), now + timedelta(minutes=minutes))
        self.assertEqual(expiry.calculate(now), now + timedelta(days=365))
        self.assertEqual(expiry.calculate(now, expires_at="2026-10-03T08:30:00+03:00"), now + timedelta(minutes=9))

    def test_invalid(self):
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        for value in ("0m", "-1m", "1.5mo", "1y", "01m", "", "999999999999999999mo"):
            with self.assertRaises((ValueError, OverflowError)):
                expiry.calculate(now, value)
        for value in ("2026-10-03T00:00:00Z", "2026-10-02T00:00:00Z", "2026-10-04T00:00:00", "2026-02-30T00:00:00Z"):
            with self.assertRaises(ValueError):
                expiry.calculate(now, expires_at=value)
        with self.assertRaises(ValueError):
            expiry.calculate(now, "5m", "2026-10-04T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
