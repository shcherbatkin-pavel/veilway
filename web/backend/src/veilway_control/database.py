from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def build_database_url(settings: Settings) -> URL:
    return URL.create(
        drivername="postgresql+psycopg",
        username=settings.database_user,
        password=settings.database_password,
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
    )


def create_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    selected = settings or get_settings()
    engine = create_engine(
        build_database_url(selected),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return create_session_factory()


def get_db() -> Iterator[Session]:
    with get_session_factory()() as session:
        yield session
