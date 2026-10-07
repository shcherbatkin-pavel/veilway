#!/usr/bin/env python3
"""Scan changed, Git-visible regular files without printing sensitive values."""

import argparse
import io
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/]{32}"),
    re.compile(rb"-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/]{32}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    re.compile(rb"GOCSPX-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"ya29\.[A-Za-z0-9_-]{30,}"),
    re.compile(rb"-----BEGIN OpenVPN tls-crypt-v2 (?:client|server) key-----\r?\n[A-Za-z0-9+/]{32}"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-public", action="store_true",
                        help="scan all tracked and unignored new files, not only the diff")
    parser.add_argument("--history", action="store_true",
                        help="also scan objects reachable from local Git refs without printing matches")
    arguments = parser.parse_args()
    changed = subprocess.check_output(
        ["git", "ls-files", "-z"] if arguments.all_public else
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
            print(f"Credential-shaped content in public file: {path.relative_to(ROOT)}")
            failed = True
    if arguments.history:
        objects = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=ROOT)
        identifiers = [line.split(b" ", 1)[0] for line in objects.splitlines()]
        batch = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT,
                               input=b"\n".join(identifiers) + b"\n",
                               stdout=subprocess.PIPE, check=True)
        stream = io.BytesIO(batch.stdout)
        for identifier in identifiers:
            returned, kind, size = stream.readline().split()
            if returned != identifier:
                raise RuntimeError("Git object stream did not match requested object")
            contents = stream.read(int(size))
            if stream.read(1) != b"\n":
                raise RuntimeError("Git object stream was truncated")
            if kind in {b"blob", b"commit", b"tag"} and any(pattern.search(contents) for pattern in PATTERNS):
                print("Credential-shaped content in reachable Git object: " + identifier.decode("ascii"))
                failed = True
        if not failed:
            print(f"Reachable Git history scan passed ({len(identifiers)} objects)")
    if not failed:
        print("All public-file credential scan passed" if arguments.all_public else
              "Changed public-file credential scan passed")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
