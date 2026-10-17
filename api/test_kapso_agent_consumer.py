from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from api.application.audit_outbox_ports import OutboxEvent
from api.application.kapso_agent_consumer import KapsoAgentConsumer
from api.tenant_boundary import (
    Agency, ClientAccount, InMemoryIdentityAdapter, InMemoryTenantRepository,
    TenantContextResolver,
)


ACCOUNT = "account-a"


class Runner:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def query(self, sql, params=()):
        self.calls.append((sql, params))
        return self.rows


class Orchestrator:
    def __init__(self):
        self.calls = []

    async def process(self, context, inbound):
        self.calls.append((context, inbound))


def event(message_id="message-a"):
    return OutboxEvent("event-a", "InboundMessageProcessed", "lead", "lead-a", "agency-a",
                       ACCOUNT, "corr-a", "key-a", {"message_id": message_id})


def consumer(rows):
    runner = Runner(rows)
    resolver = TenantContextResolver(
        InMemoryIdentityAdapter(None),
        InMemoryTenantRepository([Agency("agency-a", "Agency")],
                                 [ClientAccount(ACCOUNT, "agency-a", "Account")]),
        type("Memberships", (), {})(),
    )
    orchestrator = Orchestrator()
    return KapsoAgentConsumer(runner, resolver, orchestrator), runner, orchestrator


def test_consumer_reconstructs_scoped_event_from_trusted_message():
    item = {"id": "message-a", "conversation_id": "conversation-a", "direction": "inbound",
            "body": "trusted body", "external_event_id": "provider-event-a",
            "created_at": datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)}
    service, runner, orchestrator = consumer([item])
    asyncio.run(service.consume(event()))
    context, persisted = orchestrator.calls[0]
    assert context.client_account.id == ACCOUNT
    assert persisted.replayed is False
    assert persisted.event.body == "trusted body"
    assert persisted.event.event_id == "provider-event-a"
    assert persisted.event.client_account_id == ACCOUNT
    assert runner.calls[0][1] == ("message-a", ACCOUNT)


def test_missing_message_fails_for_dispatch_retry():
    service, _runner, orchestrator = consumer([])
    try:
        asyncio.run(service.consume(event()))
        assert False, "missing message must fail"
    except ValueError as error:
        assert str(error) == "trusted inbound message is missing"
    assert orchestrator.calls == []


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"KAPSO AGENT CONSUMER SCENARIOS: {len(tests)}/{len(tests)} passed")
