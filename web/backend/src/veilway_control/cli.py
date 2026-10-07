from __future__ import annotations

import argparse
import json
import re
import sys
import uuid

from sqlalchemy import delete, select

from .database import get_session_factory
from .models import (
    EXPECTED_VM_CONFIGURATION,
    CrlAgent,
    User,
    UserSession,
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
        # Never adopt a Google identity or remove historical job authors.
        admins = db.scalars(select(User).where(User.google_sub.is_(None))).all()
        admin = next((item for item in admins if item.login == login), None)
        credentials_changed = False
        if admin is None:
            admin = User(login=login, password_hash=PASSWORD_HASHER.hash(password), role="ADMIN")
            db.add(admin)
            credentials_changed = True
        elif not verify_password(admin.password_hash or "", password):
            admin.password_hash = PASSWORD_HASHER.hash(password)
            admin.updated_at = utcnow()
            credentials_changed = True
        if admin.role != "ADMIN" or not admin.is_active:
            credentials_changed = True
        admin.role = "ADMIN"
        admin.is_active = True
        for other in admins:
            if other is not admin and other.is_active:
                other.is_active = False
                credentials_changed = True
        db.flush()
        if credentials_changed:
            db.execute(delete(UserSession).where(UserSession.user_id.in_(
                select(User.id).where(User.google_sub.is_(None))
            )))
        db.commit()
    print("Legacy administrator synchronized; changed credentials revoke legacy sessions.")


def sync_vms() -> None:
    payload = read_json_stdin()
    expected = EXPECTED_VM_CONFIGURATION
    if set(payload) != set(expected):
        raise SystemExit("VM input must contain exactly aws-direct and yc-direct")
    with get_session_factory()() as db:
        crl_hashes = set(db.scalars(select(CrlAgent.token_hash)).all())
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
            if hash_token(heartbeat_token) in crl_hashes:
                raise SystemExit("CRL and heartbeat credentials must differ")
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


def sync_crl_agents() -> None:
    payload = read_json_stdin()
    if set(payload) != {"aws-direct", "yc-direct"}:
        raise SystemExit("expected exactly two CRL agents")
    tokens = list(payload.values())
    if any(not isinstance(token, str) or not 32 <= len(token) <= 256
           or any(ord(c) < 33 or ord(c) > 126 for c in token) for token in tokens) or tokens[0] == tokens[1]:
        raise SystemExit("invalid or duplicate CRL agent tokens")
    with get_session_factory()() as db:
        heartbeat_hashes = set(db.scalars(select(VpnVm.heartbeat_token_hash)).all())
        if any(hash_token(token) in heartbeat_hashes for token in tokens):
            raise SystemExit("CRL and heartbeat credentials must differ")
        existing = {agent.slug: agent.token_hash for agent in db.scalars(select(CrlAgent)).all()}
        if any(hash_token(token) == digest for slug, token in payload.items()
               for other_slug, digest in existing.items() if other_slug != slug):
            raise SystemExit("never reuse another node's CRL credential")
        for slug, token in payload.items():
            agent = db.get(CrlAgent, slug)
            if agent is None:
                db.add(CrlAgent(slug=slug, token_hash=hash_token(token)))
            else:
                agent.token_hash = hash_token(token)
        db.commit()
    print("Two CRL agents synchronized; only token hashes stored.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="veilway-control")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap-admin")
    subparsers.add_parser("sync-vms")
    subparsers.add_parser("sync-crl-agents")
    legacy = subparsers.add_parser("import-legacy-profiles")
    legacy.add_argument("--admin-id", type=uuid.UUID, required=True)
    arguments = parser.parse_args()
    if arguments.command == "bootstrap-admin":
        bootstrap_admin()
    elif arguments.command == "sync-vms":
        sync_vms()
    elif arguments.command == "sync-crl-agents":
        sync_crl_agents()
    elif arguments.command == "import-legacy-profiles":
        from .config import get_settings
        from .legacy_profiles import synchronize_legacy_profiles
        from .pki import PkiClient
        try:
            count = synchronize_legacy_profiles(get_session_factory(), PkiClient(get_settings().pki_socket_path), arguments.admin_id)
        except Exception:
            # A lost commit acknowledgement can mean success; replay is safe.
            raise SystemExit("Legacy profile synchronization failed. Repeat the same synchronization after operator review.") from None
        print(f"Legacy profile metadata synchronized: {count}. Assign owners through the ADMIN panel.")


if __name__ == "__main__":
    main()
