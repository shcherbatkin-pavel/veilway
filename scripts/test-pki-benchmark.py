#!/usr/bin/env python3
"""Test timing accounting without creating key material or running executables."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("pki_benchmark", Path(__file__).with_name("benchmark-pki.py"))
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class TimingTests(unittest.TestCase):
    def test_nested_phase_is_counted_once(self):
        timings = benchmark.Timings()
        inner = timings.wrap("sync", lambda: None)
        outer = timings.wrap("sync", inner)
        with patch.object(benchmark, "perf_counter", side_effect=[10.0, 12.0]) as clock:
            outer()
        self.assertEqual(timings.seconds["sync"], 2.0)
        self.assertEqual(timings.depth["sync"], 0)
        self.assertEqual(clock.call_count, 2)

    def test_failed_phase_releases_accounting_for_next_call(self):
        timings = benchmark.Timings()
        def fail():
            raise ValueError("synthetic failure")
        with patch.object(benchmark, "perf_counter", side_effect=[0.0, 1.0, 2.0, 4.0]):
            with self.assertRaises(ValueError):
                timings.wrap("check", fail)()
            timings.wrap("check", lambda: None)()
        self.assertEqual(timings.seconds["check"], 3.0)
        self.assertEqual(timings.depth["check"], 0)
        self.assertEqual(timings.seconds["copy"], 0.0)


if __name__ == "__main__":
    unittest.main()
