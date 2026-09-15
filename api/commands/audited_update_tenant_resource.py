"""WU6 atomic command: idempotency + mutation + audit + outbox."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_audit_outbox import PostgresAuditOutboxRepository
from api.adapters.postgres_core import CrossTenantAccessDenied
from api.adapters.postgres_idempotency import CommandResourceNotFound, IdempotencyConflict
from api.application.audit_outbox_ports import AuditRecord, OutboxEvent
from api.tenant_boundary import TenantContext


@dataclass(frozen=True)
class AuditedCommandResult:
    payload: dict[str, Any]
    replayed: bool
    rows_affected: int


class AuditedUpdateTenantResource:
    operation = "tenant_resource.update"

    def __init__(self, conn_kwargs: dict[str, Any], audit_outbox: PostgresAuditOutboxRepository | None = None):
        self._kwargs = conn_kwargs
        self._audit_outbox = audit_outbox or PostgresAuditOutboxRepository()

    async def execute(
        self,
        context: TenantContext | None,
        *,
        resource_id: str,
        request_hash: str,
        data: dict[str, Any],
        idempotency_key: str,
        fail_before_commit: bool = False,
    ) -> AuditedCommandResult:
        try:
            self._validate(context, request_hash, idempotency_key)
        except CrossTenantAccessDenied:
            await self._record_denial(context, "invalid_or_unauthorized_context", idempotency_key)
            raise
        assert context is not None
        conn = await psycopg.AsyncConnection.connect(**self._kwargs, autocommit=False, row_factory=dict_row)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO command_idempotency
                      (id, actor_id, operation, idempotency_key, request_hash, status)
                    VALUES (%s,%s,%s,%s,%s,'processing')
                    ON CONFLICT (actor_id, operation, idempotency_key) DO NOTHING
                    RETURNING id
                    """,
                    (str(uuid4()), context.user.id, self.operation, idempotency_key, request_hash),
                )
                inserted = await cur.fetchone()
                if inserted is None:
                    await cur.execute(
                        """
                        SELECT request_hash, result_payload, status
                        FROM command_idempotency
                        WHERE actor_id=%s AND operation=%s AND idempotency_key=%s
                        FOR UPDATE
                        """,
                        (context.user.id, self.operation, idempotency_key),
                    )
                    existing = await cur.fetchone()
                    if existing is None or existing["request_hash"] != request_hash:
                        await conn.rollback()
                        raise IdempotencyConflict()
                    if existing["status"] != "completed" or existing["result_payload"] is None:
                        await conn.rollback()
                        raise IdempotencyConflict()
                    await conn.commit()
                    return AuditedCommandResult(dict(existing["result_payload"]), True, 0)

                await cur.execute(
                    """
                    UPDATE tenant_resource
                    SET data=%s::jsonb, updated_at=now()
                    WHERE id=%s AND client_account_id=%s
                    """,
                    (_json(data), resource_id, context.client_account.id),
                )
                if cur.rowcount != 1:
                    await conn.rollback()
                    raise CommandResourceNotFound()

                await cur.execute(
                    """
                    SELECT id, client_account_id, resource_key, data
                    FROM tenant_resource WHERE id=%s AND client_account_id=%s
                    """,
                    (resource_id, context.client_account.id),
                )
                row = await cur.fetchone()
                if row is None:
                    await conn.rollback()
                    raise CommandResourceNotFound()
                payload = {"id": str(row["id"]), "client_account_id": str(row["client_account_id"]), "resource_key": row["resource_key"], "data": row["data"]}
                await cur.execute(
                    """
                    UPDATE command_idempotency
                    SET result_payload=%s::jsonb, status='completed', updated_at=now()
                    WHERE actor_id=%s AND operation=%s AND idempotency_key=%s
                    """,
                    (_json(payload), context.user.id, self.operation, idempotency_key),
                )
                await self._audit_outbox.append(AuditRecord(
                    id=str(uuid4()), actor_id=context.user.id, agency_id=context.agency.id,
                    client_account_id=context.client_account.id, action=self.operation,
                    resource_type="tenant_resource", resource_id=resource_id, outcome="success",
                    correlation_id=context.correlation_id, idempotency_key=idempotency_key,
                    metadata={"rows_affected": 1},
                ), cur)
                await self._audit_outbox.append_outbox(OutboxEvent(
                    id=str(uuid4()), event_type="TenantResourceUpdated", aggregate_type="tenant_resource",
                    aggregate_id=resource_id, agency_id=context.agency.id,
                    client_account_id=context.client_account.id, correlation_id=context.correlation_id,
                    idempotency_key=idempotency_key, payload={"resource_id": resource_id, "operation": self.operation},
                ), cur)
                if fail_before_commit:
                    raise RuntimeError("forced failure before commit")
                await conn.commit()
                return AuditedCommandResult(payload, False, 1)
        except (CrossTenantAccessDenied, CommandResourceNotFound) as error:
            if not conn.closed:
                await conn.rollback()
            await self._record_denial(context, "tenant_scope_denied", idempotency_key)
            raise error
        except BaseException:
            if not conn.closed:
                await conn.rollback()
            raise
        finally:
            await conn.close()

    @staticmethod
    def _validate(context: TenantContext | None, request_hash: str, idempotency_key: str) -> None:
        if context is None:
            raise CrossTenantAccessDenied("tenant context is required")
        if context.membership.user_id != context.user.id or context.membership.agency_id != context.agency.id or context.membership.client_account_id != context.client_account.id:
            raise CrossTenantAccessDenied("tenant context is inconsistent")
        if not context.membership.allows("client_account:write"):
            raise CrossTenantAccessDenied("membership lacks write permission")
        if not request_hash.strip() or not idempotency_key.strip():
            raise ValueError("request hash and idempotency key are required")

    async def _record_denial(self, context: TenantContext | None, reason: str, idempotency_key: str) -> None:
        """Persist a redacted denial after the rejected command has rolled back."""
        if context is None or not context.agency.id:
            return
        account_id = None
        if (
            context.membership.user_id == context.user.id
            and context.membership.agency_id == context.agency.id
            and context.membership.client_account_id == context.client_account.id
        ):
            account_id = context.client_account.id
        conn = await psycopg.AsyncConnection.connect(**self._kwargs, autocommit=False, row_factory=dict_row)
        try:
            async with conn.cursor() as cur:
                await self._audit_outbox.append(AuditRecord(
                    id=str(uuid4()), actor_id=context.user.id, agency_id=context.agency.id,
                    client_account_id=account_id, action="authorization_denied",
                    resource_type="tenant_resource", resource_id=None, outcome="denied",
                    correlation_id=context.correlation_id, idempotency_key=idempotency_key,
                    metadata={"reason": reason},
                ), cur)
            await conn.commit()
        finally:
            await conn.close()


def _json(value: Any) -> str:
    import json
    return json.dumps(value if value is not None else {}, separators=(",", ":"), sort_keys=True)
