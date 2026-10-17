"""Durable Kapso inbound outbox consumer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.adapters.postgres_tenancy import PostgresTenantRepository
from api.application.audit_outbox_ports import OutboxConsumer, OutboxEvent
from api.application.kapso_agent_orchestrator import KapsoAgentOrchestrator, PersistedInbound
from api.inbound_boundary import NormalizedInboundEvent
from api.tenant_boundary import (
    InMemoryIdentityAdapter,
    InMemoryMembershipRepository,
    TenantContextResolver,
)


class KapsoAgentConsumer(OutboxConsumer):
    """Rebuild inbound input from trusted persistence, never event claims."""

    def __init__(self, runner: Any, resolver: TenantContextResolver,
                 orchestrator: KapsoAgentOrchestrator):
        self._runner = runner
        self._resolver = resolver
        self._orchestrator = orchestrator

    async def consume(self, event: OutboxEvent) -> None:
        if event.event_type != "InboundMessageProcessed":
            return
        message_id = event.payload.get("message_id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("inbound outbox event has no message_id")
        account_id = event.client_account_id
        rows = await self._runner.query(
            "SELECT id, conversation_id, direction, body, external_event_id, created_at "
            "FROM messages WHERE id = %s AND client_account_id = %s AND direction = 'inbound'",
            (message_id, account_id),
        )
        if not rows:
            raise ValueError("trusted inbound message is missing")
        row = rows[0]
        normalized = NormalizedInboundEvent(
            event_id=str(row["external_event_id"]),
            client_account_id=account_id,
            conversation_id=str(row["conversation_id"]),
            message_id=str(row["id"]),
            direction="inbound",
            body=str(row["body"]),
            received_at=_utc_iso(row["created_at"]),
        )
        context = await self._resolver.resolve_service(account_id, event.correlation_id)
        await self._orchestrator.process(context, PersistedInbound(normalized, replayed=False))


def build_kapso_agent_consumer(conn_kwargs: dict[str, Any], runner: Any,
                               orchestrator: KapsoAgentOrchestrator | None = None) -> KapsoAgentConsumer:
    if orchestrator is None:
        from api.routes.agent_wu14 import build_agent_router
        orchestrator = getattr(build_agent_router(conn_kwargs), "kapso_orchestrator")
    tenants = PostgresTenantRepository(runner)
    resolver = TenantContextResolver(
        InMemoryIdentityAdapter(None), tenants, InMemoryMembershipRepository()
    )
    return KapsoAgentConsumer(runner, resolver, orchestrator)


def _utc_iso(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
