"""SQLAlchemy engine and session helpers."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def _make_engine(database_url: str | None = None):
    """Create a SQLAlchemy engine for the given (or configured) URL."""
    url = database_url or get_settings().database_url
    connect_args = {}
    if url.startswith("sqlite"):
        # Required for SQLite used across threads (BackgroundTasks / TestClient).
        connect_args["check_same_thread"] = False

    engine = create_engine(url, connect_args=connect_args)

    if url.startswith("sqlite"):
        # Enforce foreign keys on every SQLite connection.
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Prefer Alembic migrations in a real production app."""
    from app import models  # noqa: F401  – register models with Base.metadata

    Base.metadata.create_all(bind=engine)
