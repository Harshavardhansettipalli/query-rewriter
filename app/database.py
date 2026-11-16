"""Database engine and session handling (SQLAlchemy 2.x).

SQLite is used locally with zero setup. Switching to PostgreSQL (for example
Amazon RDS) only requires changing DATABASE_URL - no code changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp.

    SQLite drops timezone info, and comparing timezone-aware with naive datetimes
    raises TypeError. Storing naive UTC everywhere avoids that class of bug; the
    API layer adds the 'Z' suffix when serialising so browsers read it correctly.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_db_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    kwargs: dict = {"pool_pre_ping": True}

    if is_sqlite:
        # FastAPI runs sync endpoints in a thread pool, so allow cross-thread use.
        kwargs["connect_args"] = {"check_same_thread": False}
        db_path = database_url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, **kwargs)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")    # enforce ON DELETE CASCADE
            cursor.execute("PRAGMA journal_mode=WAL")   # readers don't block writers
            cursor.execute("PRAGMA busy_timeout=5000")  # wait instead of 'database is locked'
            cursor.close()

    return engine
