#!/usr/bin/env python3
"""Opt-in PKI timings using only a disposable synthetic CA and profile set."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
from statistics import median
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch
import uuid


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "web/pki"))
sys.path.insert(0, str(REPOSITORY_ROOT / "web/pki/tests"))


class Timings:
    """Inclusive phase totals, excluding recursive/nested double counting."""

    def __init__(self):
        self.seconds = {name: 0.0 for name in ("copy", "check", "sync")}
        self.depth = dict.fromkeys(self.seconds, 0)

    def wrap(self, name, function):
        def measured(*args, **kwargs):
            outermost = self.depth[name] == 0
            start = perf_counter() if outermost else 0.0
            self.depth[name] += 1
            try:
                return function(*args, **kwargs)
            finally:
                self.depth[name] -= 1
                if outermost:
                    self.seconds[name] += perf_counter() - start
        return measured

    def measure(self, operation):
        from veilway_pki import core

        with ExitStack() as stack:
            for owner, attribute, phase in (
                (core.shutil, "copytree", "copy"),
                (core, "check_tree", "check"),
                (core, "sync_tree", "sync"),
                (core, "sync_directory", "sync"),
            ):
                stack.enter_context(patch.object(owner, attribute, self.wrap(phase, getattr(owner, attribute))))
            start = perf_counter()
            operation()
            total = perf_counter() - start
        return {"total_seconds": total, **{f"{name}_seconds": value for name, value in self.seconds.items()}}


def prepare_seed(root, password, source, endpoints, count, expires):
    from veilway_pki.core import Store, check_tree, sync_tree

    store = Store(root, password)
    store.import_ca(source, endpoints)

    class SeedStore(Store):
        """Build a private fixture population without quadratic generation copies.

        Signing, profile bytes and receipts use Store.issue unchanged. Only fixture
        preparation mutates in place; no server can access this temporary tree.
        All measured operations below use an ordinary Store with durable commits.
        """

        def __init__(self):
            super().__init__(root, password)
            self.generation = super().current()

        def current(self):
            return self.generation

        def transaction(self, previous):
            assert previous == self.generation
            return previous

        def commit(self, path):
            assert path == self.generation

        @staticmethod
        def protect(path):
            # Fixture subprocesses and writes already use 0077/0600/0700.
            pass

    seed = SeedStore()
    for _ in range(count):
        seed.issue(str(uuid.uuid4()), str(uuid.uuid4()), "yc-direct", expires)
    Store.protect(seed.generation)
    check_tree(seed.generation)
    sync_tree(seed.generation)
    assert len(list((seed.generation / "profiles").iterdir())) == count
    return store


def benchmark(sizes, repetitions):
    from test_pki import PkiTests, end
    from veilway_pki.core import Store

    PkiTests.setUpClass()
    try:
        with tempfile.TemporaryDirectory(prefix="veilway-pki-benchmark-") as temporary:
            workspace = Path(temporary)
            expires = end()
            for count in sizes:
                seed_root = workspace / "seed"
                prepare_seed(seed_root, PkiTests.password, PkiTests.source, PkiTests.endpoints, count, expires)
                samples = {name: [] for name in ("issue", "revoke")}
                for _ in range(repetitions):
                    trial = workspace / "trial"
                    shutil.copytree(seed_root, trial)
                    try:
                        store = Store(trial, PkiTests.password)
                        profile_id = str(uuid.uuid4())
                        issue_key, revoke_key = str(uuid.uuid4()), str(uuid.uuid4())
                        samples["issue"].append(Timings().measure(
                            lambda: store.issue(profile_id, issue_key, "yc-direct", expires)))
                        samples["revoke"].append(Timings().measure(lambda: store.revoke(profile_id, revoke_key)))
                    finally:
                        shutil.rmtree(trial)
                yield {"profiles": count, "repetitions": repetitions, **{
                    operation: {metric: round(median(sample[metric] for sample in values), 6)
                                for metric in values[0]}
                    for operation, values in samples.items()
                }}
                shutil.rmtree(seed_root)
    finally:
        PkiTests.tearDownClass()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, choices=(10, 100, 1000), default=[10, 100, 1000])
    parser.add_argument("--repetitions", type=int, choices=(1, 2, 3), default=3)
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        for result in benchmark(arguments.sizes, arguments.repetitions):
            print(json.dumps(result, sort_keys=True), flush=True)
    except Exception:
        # Never print a traceback or fixture command output containing material.
        print("synthetic PKI benchmark failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
