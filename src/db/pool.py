"""Async PostgreSQL connection pool management."""

from __future__ import annotations

import asyncpg
import structlog

logger = structlog.get_logger()

_pool: asyncpg.Pool | None = None


async def get_pool(dsn: str) -> asyncpg.Pool:
    """Get or create a connection pool (singleton)."""
    global _pool
    if _pool is None:
        logger.info("pg.pool.creating", dsn=dsn.split("@")[-1])  # log host only, not creds
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        logger.info("pg.pool.created")
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("pg.pool.closed")
