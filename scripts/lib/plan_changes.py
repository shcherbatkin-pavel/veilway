"""Explicit read-only view of the managed resources in a supplied Terraform plan."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanChanges:
    resources: dict[str, dict[str, Any]]

    @classmethod
    def from_plan(cls, plan: dict[str, Any]) -> "PlanChanges":
        return cls({item["address"]: item for item in plan.get("resource_changes", [])
                    if item.get("mode") == "managed"})

    def before(self, address: str) -> dict[str, Any]:
        return self.resources.get(address, {}).get("change", {}).get("before", {}) or {}

    def after(self, address: str) -> dict[str, Any]:
        return self.resources.get(address, {}).get("change", {}).get("after", {}) or {}

    def actions(self, address: str) -> list[str]:
        return self.resources.get(address, {}).get("change", {}).get("actions", [])
