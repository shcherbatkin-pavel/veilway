from __future__ import annotations

import argparse
import json
import re
import sys

from sqlalchemy import delete, select

from .database import get_session_factory
from .models import (
    EXPECTED_VM_CONFIGURATION,
    Admin,
    AdminSession,
    VpnVm,
    utcnow,
)
from .security import PASSWORD_HASHER, hash_token, verify_password


LOGIN_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$")
INSTANCE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{5,127}$")
REGION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def read_json_stdin() -> dict[str, object]:
    try:
        value = json.load(sys.stdin)
    except (OSError, ValueError) as error:
        raise SystemExit("expected one JSON object on stdin") from error
    if not isinstance(value, dict):
        raise SystemExit("expected one JSON object on stdin")
    return value


def bootstrap_admin() -> None:
    payload = read_json_stdin()
    login = payload.get("login")
    password = payload.get("password")
    if not isinstance(login, str) or not LOGIN_PATTERN.fullmatch(login):
        raise SystemExit("invalid admin login")
    if not isinstance(password, str) or not 14 <= len(password) <= 1024:
        raise SystemExit("admin password must contain between 14 and 1024 characters")
    with get_session_factory()() as db:
        admins = db.scalars(select(Admin)).all()
        admin = next((item for item in admins if item.login == login), None)
        credentials_changed = False
        if admin is None:
            admin = Admin(login=login, password_hash=PASSWORD_HASHER.hash(password))
            db.add(admin)
            credentials_changed = True
        elif not verify_password(admin.password_hash, password):
            admin.password_hash = PASSWORD_HASHER.hash(password)
            admin.updated_at = utcnow()
            credentials_changed = True
        for other in admins:
            if other.id != admin.id and other.login != login:
                db.delete(other)
                credentials_changed = True
        db.flush()
        if credentials_changed:
            db.execute(delete(AdminSession))
        db.commit()
    print("Administrator synchronized; all existing sessions were revoked.")


def sync_vms() -> None:
    payload = read_json_stdin()
    expected = EXPECTED_VM_CONFIGURATION
    if set(payload) != set(expected):
        raise SystemExit("VM input must contain exactly aws-direct and yc-direct")
    with get_session_factory()() as db:
        existing = {vm.slug: vm for vm in db.scalars(select(VpnVm)).all()}
        if set(existing) - set(expected):
            raise SystemExit("database contains an unexpected managed VM")
        for slug, (provider, restart_order) in expected.items():
            item = payload[slug]
            if not isinstance(item, dict):
                raise SystemExit(f"invalid VM input for {slug}")
            instance_id = item.get("instance_id")
            region = item.get("region")
            heartbeat_token = item.get("heartbeat_token")
            if (
                not isinstance(instance_id, str)
                or not INSTANCE_ID_PATTERN.fullmatch(instance_id)
                or not isinstance(region, str)
                or not REGION_PATTERN.fullmatch(region)
                or not isinstance(heartbeat_token, str)
                or not 32 <= len(heartbeat_token) <= 256
            ):
                raise SystemExit(f"invalid VM input for {slug}")
            vm = existing.get(slug)
            if vm is None:
                vm = VpnVm(slug=slug)
                db.add(vm)
            vm.provider = provider
            vm.instance_id = instance_id
            vm.region = region
            vm.restart_order = restart_order
            vm.heartbeat_token_hash = hash_token(heartbeat_token)
        db.commit()
    print("The two managed VPN VMs were synchronized without storing raw heartbeat tokens.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="veilway-control")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap-admin")
    subparsers.add_parser("sync-vms")
    arguments = parser.parse_args()
    if arguments.command == "bootstrap-admin":
        bootstrap_admin()
    elif arguments.command == "sync-vms":
        sync_vms()


if __name__ == "__main__":
    main()
