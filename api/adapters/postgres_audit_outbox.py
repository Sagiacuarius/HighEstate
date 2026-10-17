"""PostgreSQL durable audit/outbox adapter."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence
from uuid import uuid4

from api.application.audit_outbox_ports import AuditRecord, OutboxEvent


class PostgresAuditOutboxRepository:
    """All methods accept a caller-owned transaction cursor."""

    async def append(self, record: AuditRecord, cursor: Any) -> None:
        metadata = _sanitize(record.metadata)
        await cursor.execute(
            """
            INSERT INTO audit_records
              (id, actor_id, agency_id, client_account_id, action, resource_type,
               resource_id, outcome, correlation_id, idempotency_key, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (record.id, record.actor_id, record.agency_id, record.client_account_id,
             record.action, record.resource_type, record.resource_id, record.outcome,
             record.correlation_id, record.idempotency_key, _json(metadata)),
        )

    async def append_outbox(self, event: OutboxEvent, cursor: Any) -> None:
        await cursor.execute(
            """
            INSERT INTO outbox_events
              (id, event_type, aggregate_type, aggregate_id, agency_id,
               client_account_id, correlation_id, idempotency_key, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (event_type, aggregate_type, aggregate_id, idempotency_key)
            DO NOTHING
            """,
            (event.id, event.event_type, event.aggregate_type, event.aggregate_id,
             event.agency_id, event.client_account_id, event.correlation_id,
             event.idempotency_key, _json(_sanitize(event.payload))),
        )

    async def claim(self, limit: int, lease_seconds: int, cursor: Any,
                    event_types: Sequence[str] | None = None) -> list[OutboxEvent]:
        event_filter = ""
        params: list[Any] = [limit, lease_seconds]
        if event_types is not None:
            event_filter = " AND event_type = ANY(%s)"
            params.insert(0, list(event_types))
        await cursor.execute(
            f"""
            WITH claimed AS (
              SELECT id FROM outbox_events
              WHERE ((status = 'pending' AND available_at <= now())
                 OR (status = 'failed' AND available_at <= now()))
                 {event_filter}
              ORDER BY available_at, created_at, id
              FOR UPDATE SKIP LOCKED
              LIMIT %s
            )
            UPDATE outbox_events e
            SET status='processing', attempts=e.attempts + 1,
                available_at=now() + make_interval(secs => %s)
            FROM claimed c WHERE e.id=c.id
            RETURNING e.*
            """,
            tuple(params),
        )
        return [_event(row) for row in await cursor.fetchall()]

    async def mark_published(self, event_id: str, cursor: Any) -> None:
        await cursor.execute(
            "UPDATE outbox_events SET status='published', processed_at=now(), available_at=now() WHERE id=%s AND status='processing'",
            (event_id,),
        )

    async def mark_failed(self, event_id: str, available_at: datetime, cursor: Any) -> None:
        await cursor.execute(
            "UPDATE outbox_events SET status='failed', available_at=%s WHERE id=%s AND status='processing'",
            (available_at, event_id),
        )


def _event(row: dict[str, Any]) -> OutboxEvent:
    return OutboxEvent(
        id=str(row["id"]), event_type=row["event_type"], aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"], agency_id=str(row["agency_id"]),
        client_account_id=str(row["client_account_id"]), correlation_id=row["correlation_id"],
        idempotency_key=row["idempotency_key"], payload=row["payload"], status=row["status"],
        attempts=row["attempts"], available_at=row["available_at"], created_at=row["created_at"],
        processed_at=row["processed_at"],
    )


def _json(value: Any) -> str:
    import json
    return json.dumps(value if value is not None else {}, separators=(",", ":"), sort_keys=True)


_SENSITIVE_KEYS = {"password", "token", "secret", "authorization", "cookie", "raw_body", "request_body"}


def _sanitize(value: Any) -> Any:
    """Deeply remove sensitive keys from dictionaries nested in arrays/objects."""
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value
