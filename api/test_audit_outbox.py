"""Real PostgreSQL Work Unit 6 tests.

Required env: RUNTIME_DB_HOST, RUNTIME_DB_PORT, RUNTIME_DB_NAME,
RUNTIME_DB_USER. Password stays in local libpq/pgpass and is never printed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_audit_outbox import PostgresAuditOutboxRepository
from api.adapters.postgres_core import CrossTenantAccessDenied, load_database_configuration
from api.application.audit_outbox_ports import AuditRecord, OutboxEvent
from api.application.outbox_dispatcher import OutboxDispatcher
from api.commands.audited_update_tenant_resource import AuditedCommandResult, AuditedUpdateTenantResource
from api.tenant_boundary import Agency, ClientAccount, Membership, TenantContext, User

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_A = "11000000-0000-0000-0000-000000000001"
RESOURCE_A = "40000000-0000-0000-0000-000000000001"
RESOURCE_B = "41000000-0000-0000-0000-000000000101"
PERMS = frozenset({"client_account:read", "client_account:write"})


def ctx_a() -> TenantContext:
    return TenantContext(User(USER_A, "A-only"), Agency(AGENCY_A, "Agency A"), ClientAccount(ACCOUNT_A, AGENCY_A, "Account A"), Membership(USER_A, AGENCY_A, ACCOUNT_A, PERMS), "corr-wu6")


async def conn(cfg: dict[str, Any]):
    return await psycopg.AsyncConnection.connect(**cfg, autocommit=False, row_factory=dict_row)


async def query(cfg, sql: str, params: tuple[Any, ...] = ()):
    c = await conn(cfg)
    try:
        async with c.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()
    finally:
        await c.close()


async def execute(cfg, sql: str, params: tuple[Any, ...] = ()):
    c = await conn(cfg)
    try:
        async with c.cursor() as cur:
            await cur.execute(sql, params)
        await c.commit()
    finally:
        await c.close()


async def reset(cfg):
    await execute(cfg, "TRUNCATE command_idempotency, audit_records, outbox_events")
    await execute(cfg, "UPDATE tenant_resource SET data=%s::jsonb WHERE id=%s", ('{"value":"A"}', RESOURCE_A))
    await execute(cfg, "UPDATE tenant_resource SET data=%s::jsonb WHERE id=%s", ('{"value":"B"}', RESOURCE_B))
    await execute(cfg, "DELETE FROM tenant_resource WHERE resource_key LIKE %s", ("wu6-%",))


class FakeConsumer:
    def __init__(self):
        self.seen: set[str] = set()
        self.deliveries = 0

    async def consume(self, event: OutboxEvent) -> None:
        if event.id not in self.seen:
            self.seen.add(event.id)
            self.deliveries += 1


class FailOnceConsumer:
    def __init__(self):
        self.failed = False
        self.successful_event_ids: set[str] = set()

    async def consume(self, event: OutboxEvent) -> None:
        if not self.failed:
            self.failed = True
            raise RuntimeError("fake publisher failure")
        self.successful_event_ids.add(event.id)


async def main() -> list[tuple[str, bool, str]]:
    cfg = load_database_configuration()
    await reset(cfg)
    command = AuditedUpdateTenantResource(cfg)
    context = ctx_a()
    results = []

    first = await command.execute(context, resource_id=RESOURCE_A, request_hash="h1", data={"value": "success"}, idempotency_key="success-key")
    counts = await query(cfg, "SELECT (SELECT count(*) FROM audit_records) AS audit, (SELECT count(*) FROM outbox_events) AS outbox, (SELECT count(*) FROM command_idempotency) AS idem")
    ok = not first.replayed and first.rows_affected == 1 and counts[0]["audit"] == 1 and counts[0]["outbox"] == 1 and counts[0]["idem"] == 1
    results.append(("success creates exactly one mutation/audit/outbox/idempotency", ok, str(counts[0])))

    replay = await command.execute(context, resource_id=RESOURCE_A, request_hash="h1", data={"value": "ignored"}, idempotency_key="success-key")
    counts = await query(cfg, "SELECT (SELECT count(*) FROM audit_records) AS audit, (SELECT count(*) FROM outbox_events) AS outbox, data::text AS data FROM tenant_resource WHERE id=%s", (RESOURCE_A,))
    ok = replay.replayed and replay.rows_affected == 0 and counts[0]["audit"] == 1 and counts[0]["outbox"] == 1 and counts[0]["data"] == '{"value": "success"}'
    results.append(("replay returns same result without duplicate audit/outbox/mutation", ok, str(counts[0])))

    try:
        await command.execute(context, resource_id=RESOURCE_A, request_hash="different", data={"value": "conflict"}, idempotency_key="success-key")
        results.append(("hash conflict rejects without success records", False, "not rejected"))
    except Exception as error:
        counts = await query(cfg, "SELECT (SELECT count(*) FROM audit_records) AS audit, (SELECT count(*) FROM outbox_events) AS outbox, data::text AS data FROM tenant_resource WHERE id=%s", (RESOURCE_A,))
        ok = type(error).__name__ == "IdempotencyConflict" and counts[0]["audit"] == 1 and counts[0]["outbox"] == 1 and counts[0]["data"] == '{"value": "success"}'
        results.append(("hash conflict rejects without success records", ok, type(error).__name__))

    await reset(cfg)
    async def same():
        return await command.execute(context, resource_id=RESOURCE_A, request_hash="parallel", data={"value": "parallel"}, idempotency_key="parallel-key")
    concurrent = await asyncio.gather(*(same() for _ in range(5)))
    counts = await query(cfg, "SELECT (SELECT count(*) FROM audit_records) AS audit, (SELECT count(*) FROM outbox_events) AS outbox, (SELECT count(*) FROM command_idempotency) AS idem")
    fresh = sum(not r.replayed for r in concurrent)
    replayed = sum(r.replayed for r in concurrent)
    ok = fresh == 1 and replayed == 4 and counts[0]["audit"] == 1 and counts[0]["outbox"] == 1 and counts[0]["idem"] == 1
    results.append(("five equal concurrent commands create one mutation/audit/outbox and four replays", ok, f"fresh={fresh} replayed={replayed} counts={counts[0]}"))

    await reset(cfg)
    async def different(i):
        try:
            r = await command.execute(context, resource_id=RESOURCE_A, request_hash=f"hash-{i}", data={"value": f"v-{i}"}, idempotency_key="conflict-key")
            return "winner", r
        except Exception as error:
            return type(error).__name__, None
    conflicts = await asyncio.gather(*(different(i) for i in range(5)))
    counts = await query(cfg, "SELECT (SELECT count(*) FROM audit_records) AS audit, (SELECT count(*) FROM outbox_events) AS outbox, (SELECT count(*) FROM command_idempotency) AS idem")
    winners = sum(k == "winner" for k, _ in conflicts)
    rejected = sum(k == "IdempotencyConflict" for k, _ in conflicts)
    ok = winners == 1 and rejected == 4 and counts[0]["audit"] == 1 and counts[0]["outbox"] == 1 and counts[0]["idem"] == 1
    results.append(("five conflicting concurrent commands yield one winner and four conflicts", ok, f"winners={winners} conflicts={rejected} counts={counts[0]}"))

    await reset(cfg)
    try:
        await command.execute(context, resource_id=RESOURCE_A, request_hash="rollback", data={"value": "must-rollback"}, idempotency_key="rollback-key", fail_before_commit=True)
    except RuntimeError:
        pass
    counts = await query(cfg, "SELECT (SELECT count(*) FROM audit_records) AS audit, (SELECT count(*) FROM outbox_events) AS outbox, (SELECT count(*) FROM command_idempotency) AS idem, data::text AS data FROM tenant_resource WHERE id=%s", (RESOURCE_A,))
    ok = counts[0]["audit"] == 0 and counts[0]["outbox"] == 0 and counts[0]["idem"] == 0 and counts[0]["data"] == '{"value": "A"}'
    results.append(("forced pre-commit failure rolls back mutation/audit/outbox/idempotency", ok, str(counts[0])))

    # Denial audit is redacted and scoped; no raw protected payload.
    audit = PostgresAuditOutboxRepository()
    c = await conn(cfg)
    try:
        async with c.cursor() as cur:
            await audit.append(AuditRecord(str(uuid4()), USER_A, AGENCY_A, ACCOUNT_A, "authorization_denied", "tenant_resource", RESOURCE_B, "denied", "denial-corr", None, {"reason": "cross_account", "resource_class": "tenant_resource"}), cur)
        await c.commit()
    finally:
        await c.close()
    row = (await query(cfg, "SELECT metadata, client_account_id, correlation_id FROM audit_records WHERE action=%s", ("authorization_denied",)))[0]
    ok = row["metadata"] == {"reason": "cross_account", "resource_class": "tenant_resource"} and str(row["client_account_id"]) == ACCOUNT_A and row["correlation_id"] == "denial-corr"
    results.append(("denial audit is redacted and retains safe scope/correlation", ok, str(row)))

    # Event payload is scoped and contains no secret-like keys.
    event_row = (await query(cfg, "SELECT payload, client_account_id, status FROM outbox_events ORDER BY created_at DESC LIMIT 1"))
    # reset leaves no outbox; insert a safe event for dispatcher test below.
    await reset(cfg)
    c = await conn(cfg)
    event = OutboxEvent(str(uuid4()), "TenantResourceUpdated", "tenant_resource", RESOURCE_A, AGENCY_A, ACCOUNT_A, "outbox-corr", "outbox-key", {"resource_id": RESOURCE_A, "operation": "tenant_resource.update"})
    try:
        async with c.cursor() as cur:
            await audit.append_outbox(event, cur)
        await c.commit()
    finally:
        await c.close()
    row = (await query(cfg, "SELECT payload, client_account_id, status FROM outbox_events WHERE id=%s", (event.id,)))[0]
    ok = str(row["client_account_id"]) == ACCOUNT_A and row["status"] == "pending" and not any(k in row["payload"] for k in ("password", "token", "secret", "raw_body"))
    results.append(("outbox event is pending, scoped and redacted", ok, str(row)))

    fake = FakeConsumer()
    dispatcher = OutboxDispatcher(cfg, audit, fake)
    claimed = await dispatcher.dispatch_once()
    claimed_again = await dispatcher.dispatch_once()
    row = (await query(cfg, "SELECT status, attempts, processed_at FROM outbox_events WHERE id=%s", (event.id,)))[0]
    ok = claimed == 1 and claimed_again == 0 and fake.deliveries == 1 and row["status"] == "published" and row["attempts"] == 1 and row["processed_at"] is not None
    results.append(("outbox claim/publish and fake consumer delivery are idempotent", ok, f"claimed={claimed}/{claimed_again} deliveries={fake.deliveries} row={row}"))

    # Real publisher failure: dispatcher itself marks failed; next due claim publishes.
    await reset(cfg)
    failed_event = OutboxEvent(str(uuid4()), "PublisherFailureEvent", "tenant_resource", RESOURCE_A, AGENCY_A, ACCOUNT_A, "failure-corr", "failure-key", {"resource_id": RESOURCE_A})
    c = await conn(cfg)
    try:
        async with c.cursor() as cur:
            await audit.append_outbox(failed_event, cur)
        await c.commit()
    finally:
        await c.close()
    fail_once = FailOnceConsumer()
    failing_dispatcher = OutboxDispatcher(cfg, audit, fail_once)
    first_dispatch = await failing_dispatcher.dispatch_once(lease_seconds=0)
    failed_row = (await query(cfg, "SELECT status, attempts, available_at FROM outbox_events WHERE id=%s", (failed_event.id,)))[0]
    retry_dispatch = await failing_dispatcher.dispatch_once(lease_seconds=0)
    published_row = (await query(cfg, "SELECT status, attempts, processed_at FROM outbox_events WHERE id=%s", (failed_event.id,)))[0]
    ok = first_dispatch == 1 and failed_row["status"] == "failed" and failed_row["attempts"] == 1 and failed_row["available_at"] is not None and retry_dispatch == 1 and published_row["status"] == "published" and published_row["attempts"] == 2 and published_row["processed_at"] is not None and len(fail_once.successful_event_ids) == 1
    results.append(("publisher failure is marked failed then retried and published", ok, f"first={first_dispatch} failed={failed_row} retry={retry_dispatch} published={published_row}"))

    # Failed events are eligible for retry when available_at is due.
    await reset(cfg)
    retry_event = OutboxEvent(str(uuid4()), "RetryEvent", "tenant_resource", RESOURCE_A, AGENCY_A, ACCOUNT_A, "retry-corr", "retry-key", {"resource_id": RESOURCE_A})
    c = await conn(cfg)
    try:
        async with c.cursor() as cur:
            await audit.append_outbox(retry_event, cur)
        await c.commit()
    finally:
        await c.close()
    c = await conn(cfg)
    try:
        async with c.cursor() as cur:
            claimed_rows = await audit.claim(1, 60, cur)
        await c.commit()
        assert len(claimed_rows) == 1
        async with c.cursor() as cur:
            await audit.mark_failed(retry_event.id, datetime.now(timezone.utc), cur)
        await c.commit()
    finally:
        await c.close()
    retry_fake = FakeConsumer()
    retry_dispatcher = OutboxDispatcher(cfg, audit, retry_fake)
    retried = await retry_dispatcher.dispatch_once()
    retry_row = (await query(cfg, "SELECT status, attempts FROM outbox_events WHERE id=%s", (retry_event.id,)))[0]
    ok = retried == 1 and retry_fake.deliveries == 1 and retry_row["status"] == "published" and retry_row["attempts"] == 2
    results.append(("failed event is reclaimed when due and published on retry", ok, f"dispatch={retried} deliveries={retry_fake.deliveries} row={retry_row}"))

    # Two dispatchers must not claim the same due events.
    await reset(cfg)
    dispatcher_events = [
        OutboxEvent(str(uuid4()), "ConcurrentEvent", "tenant_resource", RESOURCE_A, AGENCY_A, ACCOUNT_A, "concurrent-corr", f"concurrent-{i}", {"resource_id": RESOURCE_A, "i": i})
        for i in range(2)
    ]
    c = await conn(cfg)
    try:
        async with c.cursor() as cur:
            for item in dispatcher_events:
                await audit.append_outbox(item, cur)
        await c.commit()
    finally:
        await c.close()
    shared_fake = FakeConsumer()
    d1 = OutboxDispatcher(cfg, audit, shared_fake)
    d2 = OutboxDispatcher(cfg, audit, shared_fake)
    claimed_by_dispatchers = await asyncio.gather(d1.dispatch_once(limit=2), d2.dispatch_once(limit=2))
    rows = await query(cfg, "SELECT status, attempts FROM outbox_events WHERE event_type=%s", ("ConcurrentEvent",))
    ok = sum(claimed_by_dispatchers) == 2 and shared_fake.deliveries == 2 and all(r["status"] == "published" and r["attempts"] == 1 for r in rows)
    results.append(("concurrent dispatchers do not double-claim events", ok, f"claimed={claimed_by_dispatchers} deliveries={shared_fake.deliveries} rows={rows}"))

    # Deep redaction: nested objects and arrays are sanitized before storage.
    await reset(cfg)
    nested_audit = AuditRecord(str(uuid4()), USER_A, AGENCY_A, ACCOUNT_A, "nested_redaction", "tenant_resource", RESOURCE_A, "success", "nested-corr", None, {
        "safe": "kept", "nested": {"password": "x", "safe_nested": "kept"},
        "items": [{"token": "x", "safe_item": 1}, {"deep": {"secret": "x", "authorization": "x", "cookie": "x", "raw_body": "x", "request_body": "x", "safe_deep": True}}],
    })
    c = await conn(cfg)
    try:
        async with c.cursor() as cur:
            await audit.append(nested_audit, cur)
        await c.commit()
    finally:
        await c.close()
    stored = (await query(cfg, "SELECT metadata FROM audit_records WHERE id=%s", (nested_audit.id,)))[0]["metadata"]
    sensitive = {"password", "token", "secret", "authorization", "cookie", "raw_body", "request_body"}
    def has_sensitive(value):
        if isinstance(value, dict):
            return any(str(k).lower() in sensitive or has_sensitive(v) for k, v in value.items())
        if isinstance(value, list):
            return any(has_sensitive(v) for v in value)
        return False
    ok = not has_sensitive(stored) and stored["safe"] == "kept" and stored["nested"]["safe_nested"] == "kept"
    results.append(("nested audit secrets are deeply redacted", ok, str(stored)))

    # Composite Agency/ClientAccount FK rejects inconsistent scope.
    c = await conn(cfg)
    fk_rejected = False
    try:
        async with c.cursor() as cur:
            await cur.execute(
                "INSERT INTO audit_records (id, actor_id, agency_id, client_account_id, action, resource_type, outcome, correlation_id, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (str(uuid4()), USER_A, "00000000-0000-0000-0000-000000000002", ACCOUNT_A, "bad_scope", "tenant_resource", "denied", "bad-scope", "{}"),
            )
    except psycopg.errors.ForeignKeyViolation:
        fk_rejected = True
        await c.rollback()
    finally:
        await c.close()
    results.append(("audit inconsistent Agency/ClientAccount is rejected by composite FK", fk_rejected, f"rejected={fk_rejected}"))

    # Cross-account command uses A context and cannot mutate B; the real denial
    # is persisted separately after the rejected command transaction rolls back.
    await reset(cfg)
    before_denial = await query(cfg, "SELECT count(*) AS count FROM audit_records")
    try:
        await command.execute(context, resource_id=RESOURCE_B, request_hash="cross", data={"value": "tampered"}, idempotency_key="cross-key")
    except Exception as error:
        denied = type(error).__name__ == "CommandResourceNotFound"
    else:
        denied = False
    row = (await query(cfg, "SELECT data::text AS data FROM tenant_resource WHERE id=%s", (RESOURCE_B,)))[0]
    denial_row = (await query(cfg, "SELECT outcome, correlation_id, client_account_id, metadata FROM audit_records WHERE action='authorization_denied' ORDER BY created_at DESC LIMIT 1"))[0]
    success_count = (await query(cfg, "SELECT count(*) AS count FROM audit_records WHERE outcome='success'"))[0]["count"]
    outbox_count = (await query(cfg, "SELECT count(*) AS count FROM outbox_events"))[0]["count"]
    ok = denied and row["data"] == '{"value": "B"}' and denial_row["outcome"] == "denied" and denial_row["correlation_id"] == context.correlation_id and str(denial_row["client_account_id"]) == ACCOUNT_A and denial_row["metadata"] == {"reason": "tenant_scope_denied"} and success_count == 0 and outbox_count == 0 and (await query(cfg, "SELECT count(*) AS count FROM audit_records"))[0]["count"] == before_denial[0]["count"] + 1
    results.append(("cross-account command records redacted denial without success audit/outbox/mutation", ok, f"resource={row} denial={denial_row} success_audit={success_count} outbox={outbox_count}"))

    return results


def test_audit_outbox():
    outcomes = asyncio.run(main())
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], failures


if __name__ == "__main__":
    outcomes = asyncio.run(main())
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"AUDIT OUTBOX SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
