"""Provider-neutral bridge from durable inbound events to the agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from api.inbound_boundary import NormalizedInboundEvent
from api.tenant_boundary import TenantContext


@dataclass(frozen=True)
class PersistedInbound:
    event: NormalizedInboundEvent
    replayed: bool


AgentFlow = Callable[[TenantContext, NormalizedInboundEvent], Awaitable[Any]]


class KapsoAgentOrchestrator:
    """Run the existing agent flow only after a new inbound commit."""

    def __init__(self, agent_flow: AgentFlow):
        self._agent_flow = agent_flow

    async def process(self, context: TenantContext,
                      inbound: PersistedInbound) -> Any | None:
        if inbound.replayed:
            return None
        if inbound.event.client_account_id != context.client_account.id:
            raise ValueError("inbound event scope does not match tenant context")
        return await self._agent_flow(context, inbound.event)
