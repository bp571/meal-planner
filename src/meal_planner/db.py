"""Database engine and sessions. SQLite by default; set MEAL_PLANNER_DB_URL for another database."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from meal_planner import models  # noqa: F401  (registers the tables)

_engine: Engine | None = None


def default_url() -> str:
    data_dir = Path(os.getenv("MEAL_PLANNER_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'meal_planner.db').as_posix()}"


def init_db(url: str | None = None) -> Engine:
    """Create the engine and all tables. Call once at startup (tests pass "sqlite://")."""
    global _engine
    url = url or os.getenv("MEAL_PLANNER_DB_URL") or default_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    if url == "sqlite://":
        from sqlalchemy.pool import StaticPool

        _engine = create_engine(url, connect_args=connect_args, poolclass=StaticPool)
    else:
        _engine = create_engine(url, connect_args=connect_args)
    SQLModel.metadata.create_all(_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Call init_db() first.")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session that commits on success and rolls back on error."""
    with Session(get_engine(), expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
