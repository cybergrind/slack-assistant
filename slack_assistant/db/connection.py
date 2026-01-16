"""Async database connection pool using asyncpg."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from slack_assistant.config import get_config


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the database connection pool."""
    global _pool
    if _pool is None:
        config = get_config()
        _pool = await asyncpg.create_pool(
            config.database_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
    return _pool


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_connection() -> AsyncIterator[asyncpg.Connection]:
    """Get a connection from the pool."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
