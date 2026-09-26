import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./starrocks_br_api.db"


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """Enable FK enforcement and WAL mode on every new SQLite connection.

    SQLite ignores foreign keys unless told otherwise per-connection, so
    `ondelete="CASCADE"` on the ops tables' `cluster_id` FK would silently do
    nothing without this. WAL mode lets readers (e.g. API routes) proceed
    while a job holds a brief write transaction; it does not add concurrent
    writers, so job handlers still use short-lived per-touchpoint sessions
    rather than one held open for a whole job (see design.md Decision 2).
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_database_url() -> str:
    """Resolve the metadata store's DATABASE_URL from the environment.

    Defaults to a local SQLite file so the API server has zero required
    external infrastructure out of the box. Pointing DATABASE_URL at
    MySQL/Postgres requires no code changes - only the SQLAlchemy models
    and Alembic migrations, which are engine-portable by construction.
    """
    return os.getenv("STARROCKS_BR_DATABASE_URL", DEFAULT_DATABASE_URL)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = get_database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session; commits on success, rolls back on error."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Clear the cached engine - used by tests that change DATABASE_URL."""
    get_engine.cache_clear()
