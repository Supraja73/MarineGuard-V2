"""
session.py
------------
Async SQLAlchemy engine + session factory, replacing storage.get_connection().
Uses asyncpg under the hood (via the postgresql+asyncpg:// URL scheme) so
the app's already-async route handlers actually benefit from async I/O
instead of secretly blocking the event loop on every query, which is what
the previous sqlite3-based implementation did.

Schema creation now happens through Alembic migrations, not through an
init_schema()/init_db() call at startup - see migrations/ and
app/main.py's startup handler, which now just verifies connectivity
instead of creating tables.
"""

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.sql_echo,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields an AsyncSession, closing it (and rolling
    back any uncommitted work) when the request finishes - including when
    the request raised, so a failed request never leaves a dangling
    transaction on the connection it borrowed from the pool.
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope():
    """Non-FastAPI-dependency context manager version of get_session(), for
    use in places that aren't directly wired into FastAPI's dependency
    injection (background tasks, startup checks, scripts).
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_connection() -> None:
    """Used at startup to fail fast with a clear error if the configured
    Postgres database isn't reachable, rather than the first request
    hitting an opaque connection error.
    """
    from sqlalchemy import text
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
