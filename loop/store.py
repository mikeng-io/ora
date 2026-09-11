"""Postgres store — one pool, one idempotent schema.sql, no ORM, no migrations.

ORA-16: Postgres, not sqlite. `Store.apply_schema` is safe to call every
start (every DDL statement is `IF NOT EXISTS`); every write helper here is
one statement, so a wrong row is one line of SQL away from the bug.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


class Store:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> Store:
        pool = await asyncpg.create_pool(dsn=database_url)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def apply_schema(self, schema_path: Path = SCHEMA_PATH) -> None:
        sql = schema_path.read_text()
        async with self._pool.acquire() as conn:
            await conn.execute(sql)

    async def execute(self, query: str, *args: Any) -> str:
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)
