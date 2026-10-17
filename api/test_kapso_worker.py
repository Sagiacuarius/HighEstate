from __future__ import annotations

import asyncio

import api.kapso_worker as worker


def test_dispatch_once_wires_repository_consumer_and_dispatcher(monkeypatch):
    calls = []

    class FakeRepository:
        pass

    class FakeConsumer:
        pass

    class FakeDispatcher:
        def __init__(self, conn_kwargs, repository, consumer):
            calls.append((conn_kwargs, repository, consumer))

        async def dispatch_once(self, *, limit, lease_seconds, event_types):
            calls.append((limit, lease_seconds, event_types))
            return 1

    monkeypatch.setattr(worker, "PostgresAuditOutboxRepository", FakeRepository)
    monkeypatch.setattr(worker, "build_kapso_outbound_consumer", lambda runner, **kwargs: FakeConsumer())
    monkeypatch.setattr(worker, "OutboxDispatcher", FakeDispatcher)

    result = asyncio.run(worker.dispatch_once(
        {"dbname": "test"}, "runner", limit=7, lease_seconds=11
    ))

    assert result == 1
    assert calls[0][0] == {"dbname": "test"}
    assert isinstance(calls[0][1], FakeRepository)
    assert isinstance(calls[0][2], FakeConsumer)
    assert calls[1] == (7, 11, ("AgentReplyPersisted",))


def test_dispatch_once_accepts_test_transport_and_configuration():
    config = object()
    mapping = object()
    transport = object()
    captured = {}

    class FakeDispatcher:
        def __init__(self, *_args):
            pass

        async def dispatch_once(self, **_kwargs):
            return 0

    class FakeRepository:
        pass

    def build(runner, **kwargs):
        captured.update(runner=runner, **kwargs)
        return object()

    original_repository = worker.PostgresAuditOutboxRepository
    original_builder = worker.build_kapso_outbound_consumer
    original_dispatcher = worker.OutboxDispatcher
    worker.PostgresAuditOutboxRepository = FakeRepository
    worker.build_kapso_outbound_consumer = build
    worker.OutboxDispatcher = FakeDispatcher
    try:
        assert asyncio.run(worker.dispatch_once({}, "runner", config, mapping, transport)) == 0
    finally:
        worker.PostgresAuditOutboxRepository = original_repository
        worker.build_kapso_outbound_consumer = original_builder
        worker.OutboxDispatcher = original_dispatcher
    assert captured == {"runner": "runner", "config": config, "phone_mapping": mapping, "transport": transport}


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test(type("MonkeyPatch", (), {"setattr": lambda self, obj, name, value: setattr(obj, name, value)})()) if test.__code__.co_argcount else test()
    print(f"KAPSO WORKER SCENARIOS: {len(tests)}/{len(tests)} passed")
