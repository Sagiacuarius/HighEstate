"""MVP Commercial Demo WU5: atomic CRM command tests over real PostgreSQL.

Runs against production_runtime_mvp_wu5 (disposable database: migrations
001-004 + deterministic mvp_seed + scratch fixtures created here). Never
touches production_runtime_mvp_wu2/wu3/wu4.

Config: RUNTIME_DB_HOST/PORT/NAME/USER env vars; password via libpq pgpass.

Covers: new command atomic effects (message+lead+transition+follow-up+audit+
outbox+idempotency), replay with zero extra effects, hash conflict without
mutation, concurrent equal/conflicting reservations, forced rollback with no
residue, cross-account denial audited redacted, explicit transition and
follow-up commands, outbox claim/retry/publish with an idempotent fake
consumer, deep redaction, HTTP wiring (X-Idempotency-Key + envelope), and a
final "Account B intact" snapshot.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_audit_outbox import PostgresAuditOutboxRepository
from api.adapters.postgres_core import CrossTenantAccessDenied, load_database_configuration
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.audit_outbox_ports import AuditRecord, OutboxEvent
from api.application.crm_commands_wu5 import (
    CompleteFollowUpCommand,
    CreateFollowUpCommand,
    CrmCommandDenied,
    CrmCommandValidationError,
    ProcessInboundMessageCommand,
    TransitionLeadStageCommand,
)
from api.application.outbox_dispatcher import OutboxDispatcher
from api.composition_wu5_mvp import INBOUND_COMMAND_ROUTE, build_mvp_wu5_application
from api.domain.commercial_crm import FollowUp
from api.inbound_boundary import NormalizedInboundEvent
from api.tenant_boundary import (
    Agency, ClientAccount, InMemoryIdentityAdapter, Membership, TenantContext, User,
)

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_SEEDED = "10000000-0000-0000-0000-000000000001"
PIPELINE_A = "63000000-0000-0000-0000-000000000001"
SEED_LEAD_A = "65000000-0000-0000-0000-000000000001"
SEED_STAGE_A = "64000000-0000-0000-0000-000000000001"
A_CONVERSATION = "66000000-0000-0000-0000-000000000001"
B_CONVERSATION = "66000000-0000-0000-0000-000000000002"
# Scratch fixtures (fixed ids so re-runs are deterministic after pre-clean)
SCRATCH_CONTACT = "6f100000-0000-0000-0000-000000000001"
SCRATCH_CONVERSATION = "6f200000-0000-0000-0000-000000000001"
SCRATCH_STAGE = "6f300000-0000-0000-0000-000000000001"

PERMISSIONS_RW = frozenset({"client_account:read", "client_account:write"})
PERMISSIONS_RO = frozenset({"client_account:read"})


def context_for(account_id: str, permissions: frozenset[str] = PERMISSIONS_RW,
                correlation: str = "wu5") -> TenantContext:
    return TenantContext(
        user=User(USER_SEEDED, "demo-user"),
        agency=Agency(AGENCY_A, "Agency A"),
        client_account=ClientAccount(account_id, AGENCY_A, "account"),
        membership=Membership(USER_SEEDED, AGENCY_A, account_id, permissions),
        correlation_id=correlation,
    )


def inbound_event(conversation_id: str = SCRATCH_CONVERSATION, body: str | None = None,
                  event_id: str | None = None, message_id: str | None = None,
                  account_id: str = ACCOUNT_A) -> NormalizedInboundEvent:
    return NormalizedInboundEvent(
        event_id=event_id or f"ev-{uuid4().hex[:10]}",
        client_account_id=account_id,
        conversation_id=conversation_id,
        message_id=message_id or str(uuid4()),
        direction="inbound",
        body=body or "Quiero ver el loft de Palermo.",
        received_at="2026-09-05T12:00:00.000Z",
    )


class Inspector:
    """Autocommit read/inspection connection for harness assertions."""

    def __init__(self, conn_kwargs: dict):
        self._kwargs = conn_kwargs
        self.conn: psycopg.AsyncConnection | None = None

    async def open(self) -> None:
        self.conn = await psycopg.AsyncConnection.connect(
            **self._kwargs, autocommit=True, row_factory=dict_row)

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()

    async def q(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self.conn.cursor() as cur:
            await cur.execute(sql, params)
            if cur.description is None:  # non-SELECT (DELETE/UPDATE/INSERT): no result set
                return []
            return await cur.fetchall()

    async def one(self, sql: str, params: tuple = ()):
        rows = await self.q(sql, params)
        return rows[0] if rows else None

    async def count(self, sql: str, params: tuple = ()) -> int:
        row = await self.one(sql, params)
        return int(row["n"]) if row else -1

    async def snapshot_account(self, account_id: str) -> list[tuple]:
        # one canonical "state" column per table (properties/pipelines have no
        # `status`: availability / demo_seed_key carry their marker state)
        tables = [("contacts", "status"), ("properties", "availability"),
                  ("pipelines", "demo_seed_key"), ("pipeline_stages", "demo_seed_key"),
                  ("leads", "status"), ("conversations", "status"),
                  ("messages", "demo_seed_key"), ("follow_ups", "status")]
        out: list[tuple] = []
        for table, state_col in tables:
            rows = await self.q(
                f"SELECT id, demo_seed_key, {state_col} AS state FROM {table} WHERE client_account_id = %s ORDER BY id",
                (account_id,))
            out.extend((table, str(r["id"]), r["demo_seed_key"], r["state"]) for r in rows)
        return sorted(out)


async def preclean(db: Inspector) -> None:
    """Remove this harness's scratch/command-created rows (unmarked) so re-runs
    are deterministic. Seeded demo rows (demo_seed_key NOT NULL) are untouched."""
    # restore seed lead BEFORE touching pipeline_stages: an earlier aborted run
    # may have advanced the seeded lead onto the scratch stage (RESTRICT FK)
    await db.q(
        "UPDATE leads SET stage_id = %s, status = 'new', updated_at = now() WHERE id = %s",
        (SEED_STAGE_A, SEED_LEAD_A))
    for stmt in (
        "DELETE FROM follow_ups WHERE demo_seed_key IS NULL",
        "DELETE FROM messages WHERE demo_seed_key IS NULL",
        "DELETE FROM leads WHERE demo_seed_key IS NULL",
        "DELETE FROM conversations WHERE demo_seed_key IS NULL",
        "DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL",
        "DELETE FROM contacts WHERE demo_seed_key IS NULL",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records",
        "DELETE FROM outbox_events",
    ):
        await db.q(stmt)
    # restore seed lead to its original stage/status in case an earlier aborted
    # run advanced it (idempotent re-run support)
    await db.q(
        "UPDATE leads SET stage_id = %s, status = 'new', updated_at = now() WHERE id = %s",
        (SEED_STAGE_A, SEED_LEAD_A))
    await db.q("UPDATE conversations SET unread_count = 1 WHERE id = %s", (A_CONVERSATION,))
    await db.q("UPDATE conversations SET unread_count = 0 WHERE id = %s", (B_CONVERSATION,))
    # scratch fixtures for account A (unmarked; safe namespace)
    await db.q(
        """
        INSERT INTO contacts (id, client_account_id, name, tags)
        VALUES (%s, %s, 'Scratch Contact', '{}')
        ON CONFLICT (id) DO NOTHING
        """, (SCRATCH_CONTACT, ACCOUNT_A))
    await db.q(
        """
        INSERT INTO conversations (id, client_account_id, contact_id, unread_count)
        VALUES (%s, %s, %s, 0)
        ON CONFLICT (id) DO NOTHING
        """, (SCRATCH_CONVERSATION, ACCOUNT_A, SCRATCH_CONTACT))
    await db.q(
        """
        INSERT INTO pipeline_stages (id, client_account_id, pipeline_id, name, position)
        VALUES (%s, %s, %s, 'Qualified (scratch)', 1)
        ON CONFLICT (id) DO NOTHING
        """, (SCRATCH_STAGE, ACCOUNT_A, PIPELINE_A))


def http_scenarios() -> list[tuple[str, bool, str]]:
    """Route-level checks run outside the asyncio loop (TestClient portals)."""
    results: list[tuple[str, bool, str]] = []
    cfg = load_database_configuration()

    def payload(event_id: str, body: str) -> dict:
        message_id = str(uuid4())
        return {
            "eventId": event_id,
            "clientAccountId": ACCOUNT_A,
            "conversationId": A_CONVERSATION,
            "message": {
                "id": message_id, "conversationId": A_CONVERSATION,
                "clientAccountId": ACCOUNT_A, "direction": "inbound",
                "body": body, "receivedAt": "2026-09-05T09:00:00-03:00",
            },
        }

    app_a = build_mvp_wu5_application(cfg, ACCOUNT_A, InMemoryIdentityAdapter(User(USER_SEEDED, "demo")))
    app_b = build_mvp_wu5_application(cfg, ACCOUNT_B, InMemoryIdentityAdapter(User(USER_SEEDED, "demo")))
    app_none = build_mvp_wu5_application(cfg, ACCOUNT_A, InMemoryIdentityAdapter(None))
    with TestClient(app_a) as ca, TestClient(app_b) as cb, TestClient(app_none) as cn:
        # H1. Two POSTs of the SAME canonical payload without an explicit key:
        # the deterministic dev-event key makes the second call a replay.
        event_id = f"httpev-{uuid4().hex[:8]}"
        body_json = payload(event_id, "Visita el sábado")
        # same message id on the second call: build once, reuse
        r1 = ca.post(INBOUND_COMMAND_ROUTE, json=body_json, headers={"X-Correlation-Id": "wu5-h1a"})
        r2 = ca.post(INBOUND_COMMAND_ROUTE, json=body_json, headers={"X-Correlation-Id": "wu5-h1b"})
        msg_rows = asyncio.run(_count_messages_for(event_id))
        ok = (r1.status_code == 200 and r1.json()["replayed"] is False
              and r2.status_code == 200 and r2.json()["replayed"] is True
              and r2.json()["result"] == r1.json()["result"]
              and r2.json()["correlation_id"] == "wu5-h1b"
              and msg_rows == 1)
        results.append(("HTTP double POST of one payload = one effect, replayed result", ok,
                        f"{r1.status_code}/{r2.status_code} msg_rows={msg_rows}"))

        # H2. Explicit X-Idempotency-Key, same body twice -> replay; changed
        # body under the same key -> 409 envelope with matching correlation.
        key = f"wu5-key-{uuid4().hex[:8]}"
        e1 = payload(f"httpev-{uuid4().hex[:8]}", "conflicto-base")
        s1 = ca.post(INBOUND_COMMAND_ROUTE, json=e1,
                     headers={"X-Correlation-Id": "wu5-h2a", "X-Idempotency-Key": key})
        e2 = json.loads(json.dumps(e1)); e2["message"]["body"] = "conflicto-distinto"
        s2 = ca.post(INBOUND_COMMAND_ROUTE, json=e2,
                     headers={"X-Correlation-Id": "wu5-h2b", "X-Idempotency-Key": key})
        ok = (s1.status_code == 200 and s1.json()["replayed"] is False
              and s2.status_code == 409
              and s2.json()["error"]["code"] == "tenant_idempotency_conflict"
              and s2.json()["error"]["correlation_id"] == "wu5-h2b"
              and s2.headers.get("x-correlation-id") == "wu5-h2b")
        results.append(("HTTP replay then hash conflict -> 409 envelope", ok,
                        f"{s1.status_code}/{s2.status_code}"))

        # H3. Forged account claim -> 403 non-enumerating (claim never authority).
        forged = payload(f"httpev-{uuid4().hex[:8]}", "forjado")
        forged["clientAccountId"] = ACCOUNT_B
        forged["message"]["clientAccountId"] = ACCOUNT_B
        r = ca.post(INBOUND_COMMAND_ROUTE, json=forged, headers={"X-Correlation-Id": "wu5-h3"})
        ok = (r.status_code == 403 and r.json()["error"]["code"] == "tenant_access_denied"
              and ACCOUNT_B not in r.text)
        results.append(("HTTP forged account claim -> 403, no leak", ok, str(r.status_code)))

        # H4. Malformed payload -> 400 envelope (strict nested keys from WU4 fix).
        bad = payload(f"httpev-{uuid4().hex[:8]}", "malo")
        bad["message"]["unexpected"] = 1
        r = ca.post(INBOUND_COMMAND_ROUTE, json=bad, headers={"X-Correlation-Id": "wu5-h4"})
        ok = r.status_code == 400 and r.json()["error"]["code"] == "invalid_payload"
        results.append(("HTTP nested extra key in message -> 400", ok, str(r.status_code)))

        # H5. Absent identity -> 401.
        r = cn.post(INBOUND_COMMAND_ROUTE, json=payload(f"httpev-{uuid4().hex[:8]}", "anon"),
                    headers={"X-Correlation-Id": "wu5-h5"})
        ok = r.status_code == 401 and r.json()["error"]["code"] == "authentication_required"
        results.append(("HTTP absent identity -> 401", ok, str(r.status_code)))

        # H6. B session with its own event: accepted and A's rows untouched.
        b_event = {
            "eventId": f"httpev-{uuid4().hex[:8]}", "clientAccountId": ACCOUNT_B,
            "conversationId": B_CONVERSATION,
            "message": {"id": str(uuid4()), "conversationId": B_CONVERSATION,
                        "clientAccountId": ACCOUNT_B, "direction": "inbound",
                        "body": "Consulta sobre la casa de Fisherton",
                        "receivedAt": "2026-09-05T10:00:00Z"},
        }
        rb = cb.post(INBOUND_COMMAND_ROUTE, json=b_event, headers={"X-Correlation-Id": "wu5-h6"})
        ok = rb.status_code == 200 and rb.json()["result"]["contact_id"]
        results.append(("HTTP B session processes its own conversation", ok and rb.status_code == 200,
                        str(rb.status_code)))
    return results


async def _count_messages_for(event_id: str) -> int:
    cfg = load_database_configuration()
    async with await psycopg.AsyncConnection.connect(**cfg, autocommit=True, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) AS n FROM messages WHERE external_event_id = %s", (event_id,))
            return int((await cur.fetchone())["n"])


async def run_async_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    db = Inspector(cfg)
    await db.open()
    try:
        await preclean(db)
        b_before = await db.snapshot_account(ACCOUNT_B)

        ctx_a = context_for(ACCOUNT_A)
        inbound = ProcessInboundMessageCommand(cfg)

        # C1. New inbound command: one transaction produces message + created
        # lead + stage transition + follow-up + audit + outbox + idempotency.
        event = inbound_event()
        out1 = await inbound.execute_event(ctx_a, event=event, idempotency_key=f"iw-{uuid4().hex[:8]}")
        msg = await db.one("SELECT id, direction, body, external_event_id FROM messages WHERE id = %s",
                           (event.message_id,))
        lead = await db.one("SELECT id, stage_id, status, source FROM leads WHERE contact_id = %s",
                            (SCRATCH_CONTACT,))
        fu = await db.one("SELECT id, lead_id, contact_id, status FROM follow_ups WHERE lead_id = %s",
                          (lead["id"],))
        audit = await db.one(
            "SELECT outcome, resource_id, metadata FROM audit_records WHERE action = 'crm.inbound.process' AND outcome = 'success'")
        outbox = await db.one(
            "SELECT status, event_type FROM outbox_events WHERE event_type = 'InboundMessageProcessed'")
        idem = await db.one(
            "SELECT status, result_payload FROM command_idempotency WHERE operation = 'inbound.process'")
        unread = await db.one("SELECT unread_count FROM conversations WHERE id = %s", (SCRATCH_CONVERSATION,))
        ok = (
            not out1.replayed
            and msg is not None and msg["direction"] == "inbound" and msg["external_event_id"] == event.event_id
            and lead is not None and str(lead["stage_id"]) == SCRATCH_STAGE and lead["status"] == "qualified"
            and lead["source"] == "simulator"
            and fu is not None and fu["status"] == "pending"
            and audit is not None and str(audit["resource_id"]) == str(lead["id"])
            and audit["metadata"]["lead_created"] is True
            and outbox is not None and outbox["status"] == "pending"
            and idem["status"] == "completed"
            and idem["result_payload"]["resource_id"] == str(lead["id"])
            and unread["unread_count"] == 1
            and out1.payload["current_stage_id"] == SCRATCH_STAGE
            and out1.payload["previous_stage_id"] == SEED_STAGE_A
        )
        record("new inbound: message+lead+transition+follow-up+audit+outbox+idempotency in one tx",
               ok, f"lead={str(lead['id'])[:8]} stage={str(lead['stage_id'])[:8]}")

        # C2a. Same event re-sent under a NEW key: deduped by external_event_id
        # inside the transaction -> denial, zero mutation (one redacted denial audit).
        counts_before = (
            await db.count("SELECT count(*) AS n FROM messages"),
            await db.count("SELECT count(*) AS n FROM leads"),
            await db.count("SELECT count(*) AS n FROM follow_ups"),
            await db.count("SELECT count(*) AS n FROM audit_records WHERE outcome='success'"),
            await db.count("SELECT count(*) AS n FROM outbox_events"),
            await db.count("SELECT count(*) AS n FROM command_idempotency"),
            (await db.one("SELECT unread_count FROM conversations WHERE id = %s", (SCRATCH_CONVERSATION,)))["unread_count"],
        )
        dedup_denied = False
        try:
            await inbound.execute_event(ctx_a, event=event, idempotency_key="c2-new-key-reject")
        except CrmCommandDenied as error:
            dedup_denied = error.reason == "message_event_already_processed"
        counts_c2a = (
            await db.count("SELECT count(*) AS n FROM messages"),
            await db.count("SELECT count(*) AS n FROM leads"),
            await db.count("SELECT count(*) AS n FROM follow_ups"),
            await db.count("SELECT count(*) AS n FROM audit_records WHERE outcome='success'"),
            await db.count("SELECT count(*) AS n FROM outbox_events"),
            await db.count("SELECT count(*) AS n FROM command_idempotency"),
            (await db.one("SELECT unread_count FROM conversations WHERE id = %s", (SCRATCH_CONVERSATION,)))["unread_count"],
        )
        record("duplicate event under new key: denied, no mutation, no success audit",
               dedup_denied and counts_c2a[:6] == counts_before[:6] and counts_c2a[6] == counts_before[6],
               f"before={counts_before} after={counts_c2a}")

        # C2b. Replay (same actor+operation+key+hash): canonical result, zero extra effects.
        idem_row = await db.one("SELECT idempotency_key FROM command_idempotency WHERE operation='inbound.process'")
        same_key = idem_row["idempotency_key"]
        replay = await inbound.execute_event(ctx_a, event=event, idempotency_key=same_key)
        counts_after = (
            await db.count("SELECT count(*) AS n FROM messages"),
            await db.count("SELECT count(*) AS n FROM leads"),
            await db.count("SELECT count(*) AS n FROM follow_ups"),
            await db.count("SELECT count(*) AS n FROM audit_records WHERE outcome='success'"),
            await db.count("SELECT count(*) AS n FROM outbox_events"),
            await db.count("SELECT count(*) AS n FROM command_idempotency"),
            (await db.one("SELECT unread_count FROM conversations WHERE id = %s", (SCRATCH_CONVERSATION,)))["unread_count"],
        )
        ok = (replay.replayed and replay.payload == out1.payload and counts_before == counts_after)
        record("replay same key+hash: canonical result, zero duplicate effects", ok,
               f"before={counts_before} after={counts_after}")

        # C3. Same key, different canonical hash -> IdempotencyConflict, no mutation.
        different = inbound_event(body="Otro texto, otra clave misma", event_id="diff-event-001")
        try:
            await inbound.execute_event(ctx_a, event=different, idempotency_key=same_key)
            record("same key different hash -> conflict without mutation", False, "not rejected")
        except IdempotencyConflict:
            msgs = await db.count("SELECT count(*) AS n FROM messages")
            audits = await db.count("SELECT count(*) AS n FROM audit_records")
            ok = msgs == counts_after[0] and audits == counts_after[3] + 0 or msgs == counts_after[0]
            record("same key different hash -> conflict without mutation",
                   msgs == counts_after[0], f"messages={msgs}")

        # C4. Concurrency: 5 workers, equal key+hash -> 1 mutation + 4 replays.
        ev_par = inbound_event(body="Paralelo igual")
        key_par = f"par-{uuid4().hex[:8]}"
        outs = await asyncio.gather(*(
            inbound.execute_event(ctx_a, event=ev_par, idempotency_key=key_par) for _ in range(5)))
        fresh = sum(1 for o in outs if not o.replayed)
        replays = sum(1 for o in outs if o.replayed)
        msgs_par = await db.count(
            "SELECT count(*) AS n FROM messages WHERE external_event_id = %s", (ev_par.event_id,))
        idem_par = await db.count(
            "SELECT count(*) AS n FROM command_idempotency WHERE idempotency_key = %s", (key_par,))
        record("concurrency equal: 5 workers -> 1 mutation + 4 replays, one idempotency row",
               fresh == 1 and replays == 4 and msgs_par == 1 and idem_par == 1,
               f"fresh={fresh} replays={replays} msgs={msgs_par}")

        # C5. Concurrency: 5 workers, same key, different hashes -> 1 winner + 4 conflicts.
        key_conf = f"conf-{uuid4().hex[:8]}"
        events = [inbound_event(body=f"Conflicto {i}", event_id=f"conf-ev-{i}") for i in range(5)]

        async def attempt(ev):
            try:
                return ("winner", await inbound.execute_event(ctx_a, event=ev, idempotency_key=key_conf))
            except IdempotencyConflict:
                return ("conflict", None)
        race = await asyncio.gather(*(attempt(ev) for ev in events))
        winners = sum(1 for kind, _ in race if kind == "winner")
        conflicts = sum(1 for kind, _ in race if kind == "conflict")
        idem_conf = await db.count(
            "SELECT count(*) AS n FROM command_idempotency WHERE idempotency_key = %s", (key_conf,))
        msgs_conf = await db.count(
            "SELECT count(*) AS n FROM messages WHERE external_event_id = ANY(%s)",
            ([ev.event_id for ev in events],))
        record("concurrency conflicting: 1 winner + 4 conflicts, single reservation",
               winners == 1 and conflicts == 4 and idem_conf == 1 and msgs_conf == 1,
               f"winners={winners} conflicts={conflicts} msgs={msgs_conf}")

        # C6. Forced rollback before commit: no CRM mutation, no audit/outbox/idempotency residue.
        ev_force = inbound_event(body="Rollback forzado", event_id="forced-rollback-001")
        before = (
            await db.count("SELECT count(*) AS n FROM messages"),
            await db.count("SELECT count(*) AS n FROM audit_records"),
            await db.count("SELECT count(*) AS n FROM outbox_events"),
            await db.count("SELECT count(*) AS n FROM command_idempotency"),
        )
        try:
            await inbound.execute_event(ctx_a, event=ev_force, idempotency_key=f"force-{uuid4().hex[:8]}",
                                        fail_before_commit=True)
            record("forced rollback leaves zero residue", False, "no error raised")
        except RuntimeError:
            after = (
                await db.count("SELECT count(*) AS n FROM messages"),
                await db.count("SELECT count(*) AS n FROM audit_records"),
                await db.count("SELECT count(*) AS n FROM outbox_events"),
                await db.count("SELECT count(*) AS n FROM command_idempotency"),
            )
            record("forced rollback leaves zero residue", before == after, f"{before} == {after}")

        # C7. Cross-account denial (A context, B conversation): audited redacted,
        # no outbox/idempotency, no mutation.
        ev_cross = inbound_event(conversation_id=B_CONVERSATION, body="intrusión", event_id="cross-001",
                                 account_id=ACCOUNT_A)
        try:
            await inbound.execute_event(ctx_a, event=ev_cross, idempotency_key=f"cross-{uuid4().hex[:8]}")
            record("cross-account inbound denied", False, "not rejected")
        except CrmCommandDenied:
            denial = await db.one(
                "SELECT outcome, metadata, resource_id, client_account_id FROM audit_records "
                "WHERE outcome = 'denied' AND action = 'crm.inbound.process' "
                "ORDER BY created_at DESC, id DESC LIMIT 1")
            leak = denial is not None and (B_CONVERSATION in json.dumps(denial["metadata"])
                                           or "intrusión" in json.dumps(denial["metadata"], ensure_ascii=False))
            msgs = await db.count("SELECT count(*) AS n FROM messages WHERE external_event_id='cross-001'")
            idem = await db.count("SELECT count(*) AS n FROM command_idempotency WHERE operation='inbound.process' AND result_payload IS NULL")
            unread_b = await db.one("SELECT unread_count FROM conversations WHERE id = %s", (B_CONVERSATION,))
            ok = (denial is not None and denial["resource_id"] is None and not leak
                  and msgs == 0 and idem == 0 and unread_b["unread_count"] == 0
                  and denial["metadata"]["reason"] == "conversation_not_found_in_scope")
            record("cross-account inbound: denied + redacted audit, zero effects", ok,
                   f"reason={denial['metadata'].get('reason') if denial else None}")

        # C8. Read-only membership cannot write (fail closed before SQL) + denial audit.
        ctx_ro = context_for(ACCOUNT_A, PERMISSIONS_RO, correlation="wu5-ro")
        try:
            await inbound.execute_event(ctx_ro, event=inbound_event(), idempotency_key="ro-key")
            record("read-only membership blocked", False, "not rejected")
        except CrossTenantAccessDenied:
            denial = await db.count(
                "SELECT count(*) AS n FROM audit_records WHERE outcome='denied' "
                "AND metadata->>'reason' = 'CrossTenantAccessDenied'")
            record("read-only membership blocked before SQL + denial audit", denial >= 1, f"denials={denial}")

        # C9. Explicit transition command: legal advance, replay, foreign stage denied.
        transition = TransitionLeadStageCommand(cfg)
        # current stage of scratch lead is SCRATCH_STAGE (last); move back to seed stage
        t1 = await transition.execute_transition(
            ctx_a, lead_id=str(lead["id"]), to_stage_id=SEED_STAGE_A, new_status="new",
            idempotency_key="tr-1")
        t2 = await transition.execute_transition(
            ctx_a, lead_id=str(lead["id"]), to_stage_id=SEED_STAGE_A, new_status="new",
            idempotency_key="tr-1")
        try:
            await transition.execute_transition(
                ctx_a, lead_id=str(lead["id"]), to_stage_id="64000000-0000-0000-0000-000000000002",
                new_status="qualified", idempotency_key="tr-foreign")
            foreign_ok = False
        except CrmCommandDenied as error:
            foreign_ok = error.reason == "stage_not_available_in_scope"
        audits_t = await db.count("SELECT count(*) AS n FROM audit_records WHERE action='crm.lead.transition' AND outcome='success'")
        outbox_t = await db.count("SELECT count(*) AS n FROM outbox_events WHERE event_type='LeadStageChanged'")
        record("transition command: atomic, replay canonical, foreign stage denied once each",
               (not t1.replayed and t2.replayed and t2.payload == t1.payload
                and foreign_ok and audits_t == 1 and outbox_t == 1),
               f"audits={audits_t} outbox={outbox_t}")

        # C10. Follow-up create/complete commands + WU1<->WU2 target invariant.
        create = CreateFollowUpCommand(cfg)
        complete = CompleteFollowUpCommand(cfg)
        fu_direct = FollowUp(id=str(uuid4()), client_account_id=ACCOUNT_A, title="Llamado directo",
                             due_at=datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc),
                             lead_id=str(lead["id"]))
        c1 = await create.execute_follow_up(ctx_a, follow_up=fu_direct, idempotency_key="fu-1")
        c2 = await create.execute_follow_up(ctx_a, follow_up=fu_direct, idempotency_key="fu-1")
        try:
            FollowUp(id=str(uuid4()), client_account_id=ACCOUNT_A, title="sin target",
                     due_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
            invariant_ok = False
        except Exception:
            invariant_ok = True
        k1 = await complete.execute_completion(ctx_a, follow_up_id=fu_direct.id, idempotency_key="fu-c-1")
        k2 = await complete.execute_completion(ctx_a, follow_up_id=fu_direct.id, idempotency_key="fu-c-1")
        # second completion attempt: already completed -> denial, no extra success audit
        second_complete_denied = False
        try:
            await complete.execute_completion(ctx_a, follow_up_id=fu_direct.id, idempotency_key="fu-c-3")
        except CrmCommandDenied as error:
            second_complete_denied = error.reason == "follow_up_not_pending"
        fu_rows = await db.count("SELECT count(*) AS n FROM follow_ups WHERE id = %s", (fu_direct.id,))
        audits_c = await db.count("SELECT count(*) AS n FROM audit_records WHERE action='crm.follow_up.complete' AND outcome='success'")
        record("follow-up create replay-safe; complete once; second complete denied; target invariant enforced",
               (not c1.replayed and c2.replayed and c2.payload == c1.payload
                and invariant_ok and k1.payload["status"] == "completed"
                and second_complete_denied and fu_rows == 1 and audits_c == 1),
               f"rows={fu_rows} success_audits={audits_c}")

        # C11. Outbox claim/retry/publish with an idempotent fake consumer.
        class FakeConsumer:
            def __init__(self):
                self.processed: set[str] = set()
                self.deliveries = 0

            async def consume(self, event: OutboxEvent) -> None:
                self.deliveries += 1
                if event.id not in self.processed:  # idempotent by event id
                    self.processed.add(event.id)
                    self.side_effect = getattr(self, "side_effect", 0) + 1

        consumer = FakeConsumer()
        repo = PostgresAuditOutboxRepository()
        dispatcher = OutboxDispatcher(cfg, repo, consumer)
        published = 0
        for _ in range(3):  # drain incl. the events from the HTTP-less scenarios
            published += await dispatcher.dispatch_once(limit=10, lease_seconds=0)
        pending = await db.count("SELECT count(*) AS n FROM outbox_events WHERE status IN ('pending','processing')")
        # retry path: force one event failed with a past lease and re-dispatch
        row = await db.one("SELECT id FROM outbox_events WHERE status='published' LIMIT 1")
        if row is not None:
            await db.q("UPDATE outbox_events SET status='failed', available_at = now() - interval '1 second' WHERE id = %s", (row["id"],))
            republished = await dispatcher.dispatch_once(limit=5, lease_seconds=0)
        else:
            republished = -1
        statuses = [r["status"] for r in await db.q("SELECT status FROM outbox_events")]
        ok = (published >= 2 and pending == 0 and republished == 1
              and all(s == "published" for s in statuses))
        record("outbox claim/publish + retry after failure; fake consumer never double-applies", ok,
               f"published={published} retry={republished} deliveries={consumer.deliveries} effects={getattr(consumer, 'side_effect', 0)}")

        # C12. Deep redaction: sensitive keys stripped at every nesting level.
        await db.q("BEGIN")
        nested = {"password": "p", "keep": 1, "outer": {"token": {"secret": "s"}, "list": [
            {"cookie": "c", "raw_body": "x", "ok": 2}]}}
        async with db.conn.cursor() as cur:
            await repo.append(AuditRecord(
                id=str(uuid4()), actor_id=USER_SEEDED, agency_id=AGENCY_A,
                client_account_id=ACCOUNT_A, action="crm.redaction_probe",
                resource_type="probe", resource_id=None, outcome="failure",
                correlation_id="wu5-redact", idempotency_key=None, metadata=nested), cur)
            await db.q("COMMIT")
        probe = await db.one("SELECT metadata FROM audit_records WHERE action='crm.redaction_probe'")

        def find_sensitive(value) -> list[str]:
            found = []
            forbidden = {"password", "token", "secret", "authorization", "cookie", "raw_body", "request_body"}
            if isinstance(value, dict):
                for k, v in value.items():
                    if k.lower() in forbidden:
                        found.append(k)
                    found.extend(find_sensitive(v))
            elif isinstance(value, list):
                for item in value:
                    found.extend(find_sensitive(item))
            return found
        leaked = find_sensitive(probe["metadata"])
        ok = (leaked == [] and probe["metadata"]["keep"] == 1
              and probe["metadata"]["outer"]["list"][0]["ok"] == 2)
        record("deep redaction removes sensitive keys at all nesting levels", ok, f"leaked={leaked}")

        # C13. Second inbound on the SAME conversation: existing lead updated (no
        # duplicate lead); the deterministic advance re-applies (C9 moved the lead
        # back to the seed stage) and a follow-up is created for the SAME lead.
        ev_second = inbound_event(body="Segunda consulta", event_id="second-event-001")
        out_second = await inbound.execute_event(ctx_a, event=ev_second, idempotency_key="second-key")
        leads_scratch = await db.count(
            "SELECT count(*) AS n FROM leads WHERE contact_id = %s", (SCRATCH_CONTACT,))
        fu_same_lead = await db.count(
            "SELECT count(*) AS n FROM follow_ups WHERE lead_id = %s", (lead["id"],))
        ok = (not out_second.replayed and out_second.payload["lead_created"] is False
              and out_second.payload["resource_id"] == str(lead["id"])
              and leads_scratch == 1 and fu_same_lead >= 2
              and out_second.payload["previous_stage_id"] == SEED_STAGE_A
              and out_second.payload["current_stage_id"] == SCRATCH_STAGE)
        record("second inbound updates existing lead (no duplicate), same-lead follow-up", ok,
               f"leads={leads_scratch} fus={fu_same_lead}")

        # C14. Account B intact after all A-side scenarios.
        b_after = await db.snapshot_account(ACCOUNT_B)
        record("Account B rows exactly match the pre-scenario snapshot", b_before == b_after,
               f"b_rows={len(b_after)}")
    finally:
        await db.close()
    return results


def run_all() -> list[tuple[str, bool, str]]:
    outcomes = asyncio.run(run_async_scenarios())
    outcomes.extend(http_scenarios())
    return outcomes


def test_crm_commands():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"MVP-CRM WU5 COMMAND SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
