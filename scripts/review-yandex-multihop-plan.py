#!/usr/bin/env python3
"""Review the Yandex multi-hop ingress Terraform plan without leaking values."""

from __future__ import annotations

import json
import sys
from lib.yandex_plan_review import evaluate_plan


def main() -> int:
    if sys.argv[1:] not in ([], ["--disable-multihop"]):
        print(
            "usage: review-yandex-multihop-plan.py [--disable-multihop]",
            file=sys.stderr,
        )
        return 2
    disable_multihop = sys.argv[1:] == ["--disable-multihop"]

    try:
        plan = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as error:
        print(f"plan JSON input: FAILED ({error})", file=sys.stderr)
        return 2

    checks = evaluate_plan(plan, disable_multihop=disable_multihop)

    for name, passed in checks.items():
        print(f"{name}: {'passed' if passed else 'FAILED'}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
