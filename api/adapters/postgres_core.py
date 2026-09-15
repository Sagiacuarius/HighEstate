"""PostgreSQL adapters (psycopg3, no ORM) for Work Unit 4.

Ports are defined by the accepted tenant boundary; psycopg stays entirely
inside this adapter layer. Every tenant-scoped operation requires a
server-derived TenantContext and uses its clientAccountId as the mandatory
SQL predicate. No account/agency/user id is ever taken from client data.

Connection settings come from environment variables (fail-fast, no silent
defaults). The password is never read, printed or stored by this code:
scram/pgpass authentication is handled by libpq locally.

Required environment:
  RUNTIME_DB_HOST, RUNTIME_DB_PORT, RUNTIME_DB_NAME, RUNTIME_DB_USER
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Callable

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import psycopg
from psycopg.rows import dict_row


class DatabaseConfigurationError(Exception):
    """Raised when required database configuration is missing or invalid."""


class CrossTenantAccessDenied(Exception):
    """Raised before any SQL runs when a context/resource mismatch is detected."""


def load_database_configuration() -> dict[str, Any]:
    host = os.environ.get("RUNTIME_DB_HOST", "").strip()
    port_raw = os.environ.get("RUNTIME_DB_PORT", "").strip()
    dbname = os.environ.get("RUNTIME_DB_NAME", "").strip()
    user = os.environ.get("RUNTIME_DB_USER", "").strip()
    missing = [name for name, value in (
        ("RUNTIME_DB_HOST", host),
        ("RUNTIME_DB_PORT", port_raw),
        ("RUNTIME_DB_NAME", dbname),
        ("RUNTIME_DB_USER", user),
    ) if not value]
    if missing:
        raise DatabaseConfigurationError(
            f"missing required database configuration: {', '.join(missing)}"
        )
    try:
        port = int(port_raw)
    except ValueError as error:
        raise DatabaseConfigurationError("RUNTIME_DB_PORT must be an integer") from error
    if not (1 <= port <= 65535):
        raise DatabaseConfigurationError("RUNTIME_DB_PORT must be between 1 and 65535")
    return {"host": host, "port": port, "dbname": dbname, "user": user}


class ConnectionPool:
    """Minimal async-safe pool over libpq connections (one loop at a time)."""

    def __init__(self, kwargs: dict[str, Any], max_size: int = 4):
        self._kwargs = kwargs
        self._max_size = max_size
        self._idle: list[psycopg.AsyncConnection] = []
        self._open = 0
        self._lock = asyncio.Lock()

    async def _new_connection(self) -> psycopg.AsyncConnection:
        conn = await psycopg.AsyncConnection.connect(
            **self._kwargs, autocommit=True, row_factory=dict_row
        )
        return conn

    async def acquire(self) -> psycopg.AsyncConnection:
        return await self._new_connection()

    async def release(self, conn: psycopg.AsyncConnection) -> None:
        await conn.close()

    async def close_all(self) -> None:
        return None


class PostgresRunner:
    """Executes parameterized SQL through the pool."""

    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    async def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = await self._pool.acquire()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                return await cur.fetchall()
        finally:
            await self._pool.release(conn)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run a write statement; returns affected rowcount."""
        conn = await self._pool.acquire()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                return cur.rowcount
        finally:
            await self._pool.release(conn)

    async def close(self) -> None:
        await self._pool.close_all()
