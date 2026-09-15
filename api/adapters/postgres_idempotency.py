"""Transactional PostgreSQL idempotency adapter for Work Unit 5."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import CrossTenantAccessDenied
from api.tenant_boundary import TenantContext


class IdempotencyConflict(Exception):
    """Same actor/operation/key was used for a different request hash."""


class CommandResourceNotFound(Exception):
    """The tenant-scoped resource is absent or outside the context."""


@dataclass(frozen=True)
class CommandResult:
    payload: dict[str, Any]
    replayed: bool
    rows_affected: int


class PostgresCommandConnection:
    """Opens a fresh transaction connection for one atomic command."""

    def __init__(self, conn_kwargs: dict[str, Any]):
        self._kwargs = conn_kwargs

    async def connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(
            **self._kwargs, autocommit=False, row_factory=dict_row
        )


class TransactionalUpdateTenantResource:
    """Authorize, reserve idempotency, mutate and commit atomically.

    No provider/external call is present in this command boundary. The only
    writes are the tenant_resource mutation and its idempotency result.
    """

    operation = "tenant_resource.update"

    def __init__(self, conn_factory: PostgresCommandConnection):
        self._factory = conn_factory

    async def execute(
        self,
        context: TenantContext | None,
        *,
        resource_id: str,
        request_hash: str,
        data: dict[str, Any],
        idempotency_key: str,
    ) -> CommandResult:
        self._validate_context(context)
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        if not request_hash or not request_hash.strip():
            raise ValueError("request_hash must be non-empty")
        assert context is not None
        actor_id = context.user.id
        account_id = context.client_account.id
        conn = await self._factory.connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO command_idempotency
                      (id, actor_id, operation, idempotency_key, request_hash, status)
                    VALUES (%s, %s, %s, %s, %s, 'processing')
                    ON CONFLICT (actor_id, operation, idempotency_key) DO NOTHING
                    RETURNING id
                    """,
                    (str(uuid4()), actor_id, self.operation, idempotency_key, request_hash),
                )
                inserted = await cur.fetchone()
                if inserted is None:
                    await cur.execute(
                        """
                        SELECT request_hash, result_payload, status
                        FROM command_idempotency
                        WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
                        FOR UPDATE
                        """,
                        (actor_id, self.operation, idempotency_key),
                    )
                    existing = await cur.fetchone()
                    if existing is None or existing["request_hash"] != request_hash:
                        await conn.rollback()
                        raise IdempotencyConflict()
                    if existing["status"] != "completed" or existing["result_payload"] is None:
                        await conn.rollback()
                        raise IdempotencyConflict()
                    await conn.commit()
                    return CommandResult(dict(existing["result_payload"]), True, 0)

                await cur.execute(
                    """
                    UPDATE tenant_resource
                    SET data = %s::jsonb, updated_at = now()
                    WHERE id = %s AND client_account_id = %s
                    """,
                    (_json(data), resource_id, account_id),
                )
                affected = cur.rowcount
                if affected != 1:
                    await conn.rollback()
                    raise CommandResourceNotFound()

                await cur.execute(
                    """
                    SELECT id, client_account_id, resource_key, data
                    FROM tenant_resource
                    WHERE id = %s AND client_account_id = %s
                    """,
                    (resource_id, account_id),
                )
                row = await cur.fetchone()
                if row is None:
                    await conn.rollback()
                    raise CommandResourceNotFound()
                payload = {
                    "id": str(row["id"]),
                    "client_account_id": str(row["client_account_id"]),
                    "resource_key": row["resource_key"],
                    "data": row["data"],
                }
                await cur.execute(
                    """
                    UPDATE command_idempotency
                    SET result_payload = %s::jsonb, status = 'completed', updated_at = now()
                    WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
                    """,
                    (_json(payload), actor_id, self.operation, idempotency_key),
                )
                await conn.commit()
                return CommandResult(payload, False, affected)
        except BaseException:
            if not conn.closed:
                await conn.rollback()
            raise
        finally:
            await conn.close()

    @staticmethod
    def _validate_context(context: TenantContext | None) -> None:
        if context is None:
            raise CrossTenantAccessDenied("tenant context is required")
        if context.membership.user_id != context.user.id:
            raise CrossTenantAccessDenied("context membership user mismatch")
        if context.membership.client_account_id != context.client_account.id:
            raise CrossTenantAccessDenied("context membership account mismatch")
        if context.membership.agency_id != context.agency.id:
            raise CrossTenantAccessDenied("context membership agency mismatch")
        if not context.membership.allows("client_account:write"):
            raise CrossTenantAccessDenied("membership lacks write permission")


def _json(value: Any) -> str:
    import json

    return json.dumps(value if value is not None else {}, separators=(",", ":"), sort_keys=True)
