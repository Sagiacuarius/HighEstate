"""Hexagonal audit/outbox ports; no FastAPI or psycopg dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class AuditRecord:
    id: str
    actor_id: str | None
    agency_id: str
    client_account_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    correlation_id: str
    idempotency_key: str | None
    metadata: dict[str, Any]
    created_at: datetime | None = None


@dataclass(frozen=True)
class OutboxEvent:
    id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    agency_id: str
    client_account_id: str
    correlation_id: str
    idempotency_key: str
    payload: dict[str, Any]
    status: str = "pending"
    attempts: int = 0
    available_at: datetime | None = None
    created_at: datetime | None = None
    processed_at: datetime | None = None


class AuditRepository(Protocol):
    async def append(self, record: AuditRecord, cursor: Any) -> None: ...


class AuditSink(AuditRepository, Protocol):
    pass


class OutboxRepository(Protocol):
    async def append_outbox(self, event: OutboxEvent, cursor: Any) -> None: ...
    async def claim(self, limit: int, lease_seconds: int, cursor: Any,
                    event_types: Sequence[str] | None = None) -> list[OutboxEvent]: ...
    async def mark_published(self, event_id: str, cursor: Any) -> None: ...
    async def mark_failed(self, event_id: str, available_at: datetime, cursor: Any) -> None: ...


class OutboxPublisher(Protocol):
    async def publish(self, event: OutboxEvent) -> None: ...


class OutboxConsumer(Protocol):
    async def consume(self, event: OutboxEvent) -> None: ...
