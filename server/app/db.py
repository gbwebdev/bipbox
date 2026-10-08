"""Database access.

Async throughout, deliberately. The server holds long-lived WebSockets to every
box, so a synchronous database call on the event loop stalls *all* of them, not
just one request — unlike telex, which was pure request/response and got away
with sync sessions (architecture.md §4.2).

SQLite is the only supported engine, but nothing here is SQLite-specific beyond
the connection pragmas, so a move to Postgres would be a URL change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _configure_sqlite(engine: AsyncEngine) -> None:
    """WAL so readers never block the single writer, and enforce foreign keys.

    SQLite leaves foreign keys *off* by default, which quietly permits orphaned
    rows — a delivery pointing at a device that no longer exists.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def init_engine(database_url: str, echo: bool = False) -> AsyncEngine:
    global _engine, _sessionmaker

    _engine = create_async_engine(database_url, echo=echo, future=True)
    if database_url.startswith("sqlite"):
        _configure_sqlite(_engine)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("init_engine() has not been called")
    return _engine


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Commits on success, rolls back on any exception."""
    if _sessionmaker is None:
        raise RuntimeError("init_engine() has not been called")
    async with _sessionmaker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def check_connection() -> bool:
    """Used by /health/ready. Cheap, and never raises."""
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
