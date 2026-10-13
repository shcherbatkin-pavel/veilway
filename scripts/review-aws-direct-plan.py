#!/usr/bin/env python3
"""Review an aws-direct Terraform plan without printing sensitive values."""

from __future__ import annotations

import json
import sys
from lib.aws_plan_review import evaluate_plan


def main() -> int:
    if sys.argv[1:] not in (
        [],
        ["--recovery"],
        ["--port-rollback"],
        ["--enable-multihop"],
        ["--disable-multihop"],
    ):
        print(
            "usage: review-aws-direct-plan.py "
            "[--recovery|--port-rollback|--enable-multihop|--disable-multihop]",
            file=sys.stderr,
        )
        return 2

    recovery = sys.argv[1:] == ["--recovery"]
    port_rollback = sys.argv[1:] == ["--port-rollback"]
    enable_multihop = sys.argv[1:] == ["--enable-multihop"]
    disable_multihop = sys.argv[1:] == ["--disable-multihop"]

    try:
        plan = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as error:
        print(f"plan JSON input: FAILED ({error})", file=sys.stderr)
        return 2

    checks = evaluate_plan(plan, recovery=recovery, port_rollback=port_rollback, enable_multihop=enable_multihop, disable_multihop=disable_multihop)

    for name, passed in checks.items():
        status = "passed" if passed else "FAILED"
        print(f"{name}: {status}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
