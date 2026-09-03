from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from veilway_control.database import Base
from veilway_control.models import Admin, VmHeartbeat, VpnVm, utcnow
from veilway_control.security import PASSWORD_HASHER, hash_token


@pytest.fixture
def db_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def seed_control_data(
    db_factory: sessionmaker[Session],
) -> Callable[..., dict[str, object]]:
    def seed(*, with_heartbeats: bool = True) -> dict[str, object]:
        with db_factory() as db:
            admin = Admin(
                login="operator",
                password_hash=PASSWORD_HASHER.hash("correct horse battery staple"),
            )
            aws = VpnVm(
                slug="aws-direct",
                provider="aws",
                instance_id="i-0123456789abcdef0",
                region="eu-central-1",
                restart_order=1,
                heartbeat_token_hash=hash_token("a" * 40),
            )
            yandex = VpnVm(
                slug="yc-direct",
                provider="yandex",
                instance_id="fhm0123456789abcdef0",
                region="ru-central1-a",
                restart_order=2,
                heartbeat_token_hash=hash_token("y" * 40),
            )
            db.add_all([admin, aws, yandex])
            db.flush()
            if with_heartbeats:
                db.add_all(
                    [
                        VmHeartbeat(
                            vm_id=aws.id,
                            boot_id="00000000-0000-4000-8000-000000000001",
                            healthy=True,
                            uptime_seconds=100,
                            received_at=utcnow(),
                        ),
                        VmHeartbeat(
                            vm_id=yandex.id,
                            boot_id="00000000-0000-4000-8000-000000000002",
                            healthy=True,
                            uptime_seconds=100,
                            received_at=utcnow(),
                        ),
                    ]
                )
            db.commit()
            return {"admin_id": admin.id, "aws_id": aws.id, "yandex_id": yandex.id}

    return seed
