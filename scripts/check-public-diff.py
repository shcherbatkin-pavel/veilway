#!/usr/bin/env python3
"""Scan changed, Git-visible regular files without printing sensitive values."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/]{32}"),
    re.compile(rb"-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/]{32}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
)


def main() -> int:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", "-z", "HEAD", "--"], cwd=ROOT,
    )
    new = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    )
    failed = False
    for name in sorted(set((changed + new).split(b"\0")) - {b""}):
        path = ROOT / name.decode("utf-8", errors="surrogateescape")
        if path.is_symlink() or not path.is_file():
            continue
        if any(pattern.search(path.read_bytes()) for pattern in PATTERNS):
            # Never emit matching contents, including keys or infrastructure values.
            print(f"Credential-shaped content in changed public file: {path.relative_to(ROOT)}")
            failed = True
    if not failed:
        print("Changed public-file credential scan passed")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
