from __future__ import annotations

import asyncio

import api.kapso_agent_worker as worker


def test_dispatch_once_filters_inbound_events_without_live_dependencies(monkeypatch):
    calls = []

    class Repository:
        pass

    class Dispatcher:
        def __init__(self, conn_kwargs, repository, consumer):
            calls.append((conn_kwargs, repository, consumer))

        async def dispatch_once(self, **kwargs):
            calls.append(kwargs)
            return 1

    monkeypatch.setattr(worker, "PostgresAuditOutboxRepository", Repository)
    monkeypatch.setattr(worker, "build_kapso_agent_consumer", lambda conn_kwargs, runner: (conn_kwargs, runner))
    monkeypatch.setattr(worker, "OutboxDispatcher", Dispatcher)
    assert asyncio.run(worker.dispatch_once({"dbname": "test"}, "runner", 3, 9)) == 1
    assert calls[1] == {"limit": 3, "lease_seconds": 9, "event_types": ("InboundMessageProcessed",)}


if __name__ == "__main__":
    test_dispatch_once_filters_inbound_events_without_live_dependencies(
        type("MonkeyPatch", (), {"setattr": lambda self, obj, name, value: setattr(obj, name, value)})()
    )
    print("KAPSO AGENT WORKER SCENARIOS: 1/1 passed")
