from __future__ import annotations

import asyncio

import api.application.outbox_dispatcher as dispatcher_module
from api.adapters.postgres_audit_outbox import PostgresAuditOutboxRepository
from api.application.audit_outbox_ports import OutboxEvent
from api.application.outbox_dispatcher import OutboxDispatcher


class Cursor:
    def __init__(self):
        self.sql = ""
        self.params = ()

    async def execute(self, sql, params):
        self.sql, self.params = sql, params

    async def fetchall(self):
        return []


def test_claim_filter_is_server_side_and_optional():
    cursor = Cursor()
    asyncio.run(PostgresAuditOutboxRepository().claim(5, 10, cursor, ("InboundMessageProcessed",)))
    assert "event_type = ANY(%s)" in cursor.sql
    assert cursor.params == (["InboundMessageProcessed"], 5, 10)

    unfiltered = Cursor()
    asyncio.run(PostgresAuditOutboxRepository().claim(5, 10, unfiltered))
    assert "event_type = ANY(%s)" not in unfiltered.sql
    assert unfiltered.params == (5, 10)


def test_dispatcher_passes_filter_only_when_requested():
    calls = []

    class Connection:
        async def commit(self):
            pass

        async def rollback(self):
            pass

        async def close(self):
            pass

        def cursor(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    class Repository:
        async def claim(self, *args):
            calls.append(args)
            return []

    class Consumer:
        async def consume(self, _event):
            pass

    class Psycopg:
        class AsyncConnection:
            @staticmethod
            async def connect(**_kwargs):
                return Connection()

    original = dispatcher_module.psycopg
    dispatcher_module.psycopg = Psycopg
    try:
        repository = Repository()
        dispatcher = OutboxDispatcher({}, repository, Consumer())
        asyncio.run(dispatcher.dispatch_once())
        asyncio.run(dispatcher.dispatch_once(event_types=("AgentReplyPersisted",)))
    finally:
        dispatcher_module.psycopg = original
    assert len(calls[0]) == 3
    assert calls[1][3] == ("AgentReplyPersisted",)


def test_new_inbound_event_is_claimed_and_published_once():
    event = OutboxEvent("event-a", "InboundMessageProcessed", "lead", "lead-a", "agency-a",
                        "account-a", "corr-a", "key-a", {"message_id": "message-a"})
    state = {"pending": True, "published": 0, "filters": []}

    class Repository:
        async def claim(self, _limit, _lease, _cursor, event_types=None):
            state["filters"].append(event_types)
            if not state["pending"]:
                return []
            state["pending"] = False
            return [event]

        async def mark_published(self, _event_id, _cursor):
            state["published"] += 1

        async def mark_failed(self, _event_id, _available_at, _cursor):
            raise AssertionError("unexpected failure")

    class Consumer:
        def __init__(self):
            self.events = []

        async def consume(self, item):
            self.events.append(item)

    class Connection:
        async def commit(self):
            pass

        async def rollback(self):
            pass

        async def close(self):
            pass

        def cursor(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    class Psycopg:
        class AsyncConnection:
            @staticmethod
            async def connect(**_kwargs):
                return Connection()

    original = dispatcher_module.psycopg
    dispatcher_module.psycopg = Psycopg
    consumer = Consumer()
    try:
        dispatcher = OutboxDispatcher({}, Repository(), consumer)
        assert asyncio.run(dispatcher.dispatch_once(event_types=("InboundMessageProcessed",))) == 1
        assert asyncio.run(dispatcher.dispatch_once(event_types=("InboundMessageProcessed",))) == 0
    finally:
        dispatcher_module.psycopg = original
    assert len(consumer.events) == 1
    assert state["published"] == 1
    assert state["filters"] == [("InboundMessageProcessed",), ("InboundMessageProcessed",)]


if __name__ == "__main__":
    test_claim_filter_is_server_side_and_optional()
    test_dispatcher_passes_filter_only_when_requested()
    test_new_inbound_event_is_claimed_and_published_once()
