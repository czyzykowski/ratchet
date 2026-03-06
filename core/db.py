"""Database connection and session management using psycopg3 async pool."""

from __future__ import annotations

import os

from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Example: postgresql+psycopg://user:password@localhost:5432/ratchet"
        )
    # psycopg_pool expects a libpq-style connstring or URI without the +psycopg scheme.
    return url.replace("postgresql+psycopg://", "postgresql://")


async def get_pool() -> AsyncConnectionPool:
    """Return the shared async connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        conninfo = _get_database_url()
        _pool = AsyncConnectionPool(conninfo, open=False)
        await _pool.open()
    return _pool


async def close_pool() -> None:
    """Close the connection pool. Call on application shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
