#!/usr/bin/env python3
"""Calculate and verify client certificate expiry without accessing private keys."""

import argparse
import calendar
import os
from datetime import datetime, timedelta, timezone
import re
import subprocess

UTC = timezone.utc


def calculate(now, valid_for=None, expires_at=None):
    if valid_for is not None and expires_at is not None:
        raise ValueError("--valid-for and --expires-at are mutually exclusive")
    if expires_at is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", expires_at):
            raise ValueError("expiry must be ISO 8601 with seconds and timezone")
        end = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).astimezone(UTC)
    elif valid_for is not None:
        match = re.fullmatch(r"([1-9][0-9]*)(mo|m)", valid_for)
        if not match:
            raise ValueError("duration must be positive integer minutes (m) or months (mo)")
        count = int(match[1])
        if match[2] == "m":
            end = now + timedelta(minutes=count)
        else:
            year, month = divmod(now.year * 12 + now.month - 1 + count, 12)
            month += 1
            end = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
    else:
        end = now + timedelta(days=365)
    end = end.replace(microsecond=0)
    if end <= now:
        raise ValueError("expiry must be in the future")
    return end


def certificate_end(path):
    value = subprocess.check_output(
        ["openssl", "x509", "-in", path, "-noout", "-enddate"], text=True,
        env={**os.environ, "LC_ALL": "C"},
    ).strip()
    return datetime.strptime(value.removeprefix("notAfter="), "%b %d %H:%M:%S %Y GMT").replace(tzinfo=UTC)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--valid-for")
    group.add_argument("--expires-at")
    parser.add_argument("--ca")
    parser.add_argument("--check-certificate")
    parser.add_argument("--expected")
    args = parser.parse_args()
    try:
        if args.check_certificate:
            end = certificate_end(args.check_certificate)
            if end.strftime("%Y%m%d%H%M%SZ") != args.expected:
                raise ValueError("issued certificate expiry differs from requested expiry")
            print("Certificate expires UTC: " + end.isoformat())
            print("Certificate expires MSK: " + end.astimezone(timezone(timedelta(hours=3))).isoformat())
        else:
            if not args.ca:
                raise ValueError("--ca is required")
            end = calculate(datetime.now(UTC), args.valid_for, args.expires_at)
            if end > certificate_end(args.ca):
                raise ValueError("client expiry exceeds CA expiry")
            print(end.strftime("%Y%m%d%H%M%SZ"))
    except (ValueError, OverflowError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
