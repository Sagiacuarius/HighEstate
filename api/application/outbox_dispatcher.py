"""Outbox dispatch seam with explicit lease and idempotent fake delivery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row

from api.application.audit_outbox_ports import OutboxConsumer, OutboxEvent
from api.adapters.postgres_audit_outbox import PostgresAuditOutboxRepository


class OutboxDispatcher:
    def __init__(self, conn_kwargs: dict[str, Any], repository: PostgresAuditOutboxRepository, consumer: OutboxConsumer):
        self._kwargs = conn_kwargs
        self._repository = repository
        self._consumer = consumer

    async def dispatch_once(self, limit: int = 20, lease_seconds: int = 60,
                            event_types: Sequence[str] | None = None) -> int:
        conn = await psycopg.AsyncConnection.connect(**self._kwargs, autocommit=False, row_factory=dict_row)
        events: list[OutboxEvent] = []
        try:
            async with conn.cursor() as cur:
                if event_types is None:
                    events = await self._repository.claim(limit, lease_seconds, cur)
                else:
                    events = await self._repository.claim(limit, lease_seconds, cur, event_types)
                await conn.commit()
            for event in events:
                try:
                    await self._consumer.consume(event)
                    async with conn.cursor() as cur:
                        await self._repository.mark_published(event.id, cur)
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    async with conn.cursor() as cur:
                        await self._repository.mark_failed(event.id, datetime.now(timezone.utc) + timedelta(seconds=lease_seconds), cur)
                    await conn.commit()
            return len(events)
        finally:
            await conn.close()
