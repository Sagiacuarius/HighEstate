from __future__ import annotations

import asyncio

from api.application.kapso_agent_orchestrator import (
    KapsoAgentOrchestrator,
    PersistedInbound,
)
from api.inbound_boundary import NormalizedInboundEvent
from api.tenant_boundary import Agency, ClientAccount, Membership, TenantContext, User


ACCOUNT = "mapped-account"
CONVERSATION = "conversation-a"


def context() -> TenantContext:
    return TenantContext(
        User("service:tenant-webhook", "Tenant Webhook Service"),
        Agency("agency-a", "Agency"),
        ClientAccount(ACCOUNT, "agency-a", "Account"),
        Membership("service:tenant-webhook", "agency-a", ACCOUNT,
                   frozenset({"client_account:write"})),
        "kapso-correlation",
    )


def inbound(body: str = "hello") -> PersistedInbound:
    event = NormalizedInboundEvent(
        "message-1", ACCOUNT, CONVERSATION, "message-1", "inbound", body,
        "2026-10-17T12:00:00.000Z")
    return PersistedInbound(event, replayed=False)


class FakeFlow:
    def __init__(self, action: str = "send_reply", failure: Exception | None = None):
        self.calls = []
        self.outbox = []
        self.action = action
        self.failure = failure

    async def __call__(self, tenant, event):
        self.calls.append((tenant.client_account.id, event.body))
        if self.failure:
            raise self.failure
        if self.action == "send_reply":
            self.outbox.append({"event_type": "AgentReplyPersisted",
                                "conversation_id": event.conversation_id,
                                "body": "grounded reply"})
        return self.outbox[-1] if self.outbox else None


def test_new_inbound_runs_once_and_creates_reply_outbox_event():
    flow = FakeFlow()
    result = asyncio.run(KapsoAgentOrchestrator(flow).process(context(), inbound()))
    assert flow.calls == [(ACCOUNT, "hello")]
    assert result["event_type"] == "AgentReplyPersisted"
    assert len(flow.outbox) == 1


def test_replayed_inbound_skips_second_analysis():
    flow = FakeFlow()
    service = KapsoAgentOrchestrator(flow)
    replay = PersistedInbound(inbound().event, replayed=True)
    assert asyncio.run(service.process(context(), replay)) is None
    assert flow.calls == []


def test_handoff_without_reply_has_no_outbox_event():
    flow = FakeFlow(action="handoff")
    asyncio.run(KapsoAgentOrchestrator(flow).process(context(), inbound("human")))
    assert flow.calls == [(ACCOUNT, "human")]
    assert flow.outbox == []


def test_agent_failure_does_not_create_outbox_event():
    flow = FakeFlow(failure=RuntimeError("provider unavailable"))
    try:
        asyncio.run(KapsoAgentOrchestrator(flow).process(context(), inbound()))
        assert False, "provider failure must be visible to the webhook boundary"
    except RuntimeError:
        pass
    assert flow.outbox == []


def test_flow_receives_the_mapped_tenant_not_provider_identifiers():
    flow = FakeFlow()
    asyncio.run(KapsoAgentOrchestrator(flow).process(context(), inbound()))
    assert flow.calls[0][0] == ACCOUNT
