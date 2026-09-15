"""MVP Commercial Demo WU7: server-owned demo selector + scoped cache + seed/reset.

Runs against production_runtime_mvp_wu7 (disposable database: migrations
001-004 + deterministic mvp_seed + WU5-shaped command fixtures created here).
Never touches production_runtime_mvp_wu2/wu3/wu4/wu5/wu6.

Config: RUNTIME_DB_HOST/PORT/NAME/USER env vars; password via libpq pgpass.

Covers: select/switch A<->B with server-side membership resolution, prior
context preserved on every denial, no-mutation on denial, cross-account
idempotency-key replay conflict, scoped cache hit/miss/invalidation/isolation,
demo seed/reset controls (demo-* markers only, unmarked rows survive), and
401/403 envelopes with correlation on all surfaces.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.crm_commands_wu5 import ProcessInboundMessageCommand
from api.composition_wu3_mvp import CrmRepositoryComposition
from api.composition_wu7_mvp import (
    CURRENT_ROUTE,
    DASHBOARD_ROUTE,
    INBOX_ROUTE,
    RESET_ROUTE,
    SEED_ROUTE,
    SELECT_ROUTE,
    build_mvp_wu7_application,
)
from api.identity.demo_identity import (
    AbsentDemoIdentityAdapter,
    DemoIdentityAdapter,
)
from api.inbound_boundary import NormalizedInboundEvent
from api.queries.demo_cache import ScopedQueryCache, build_scoped_key
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    Membership,
    TenantContext,
    User,
)

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_SEEDED = "10000000-0000-0000-0000-000000000001"
A_CONVERSATION = "66000000-0000-0000-0000-000000000001"
B_CONVERSATION = "66000000-0000-0000-0000-000000000002"
GHOST = "0000dead-0000-4000-8000-000000000000"
PERMISSIONS_RW = frozenset({"client_account:read", "client_account:write"})
SCRATCH_CONTACT = "6f100000-0000-0000-0000-000000000007"


def envelope_ok(response, status: int, correlation: str, code: str) -> bool:
    if response.status_code != status:
        return False
    body = response.json()
    if "error" not in body:
        return False
    err = body["error"]
    return (err.get("code") == code
            and err.get("correlation_id") == correlation
            and response.headers.get("x-correlation-id") == correlation
            and isinstance(err.get("message"), str) and err["message"])


def projection_ok(response, correlation: str, account_id: str) -> bool:
    if response.status_code != 200:
        return False
    body = response.json()
    return (body.get("contract_version") == "v1"
            and body.get("environment") == "dev-only-demo-selector"
            and body.get("correlation_id") == correlation
            and response.headers.get("x-correlation-id") == correlation
            and body.get("user_id") == USER_SEEDED
            and body.get("agency_id") == AGENCY_A
            and body.get("client_account_id") == account_id
            and "client_account:read" in body.get("permissions", []))


class Sql:
    def __init__(self, kwargs: dict):
        self._kwargs = kwargs
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
            if cur.description is None:
                return []
            return await cur.fetchall()

    async def one(self, sql: str, params: tuple = ()):
        rows = await self.q(sql, params)
        return rows[0] if rows else None

    async def count(self, sql: str, params: tuple = ()) -> int:
        row = await self.one(sql, params)
        return int(row["n"]) if row else -1


async def preclean(db: Sql) -> None:
    await clear_unmarked(db)
    for stmt in (
        "UPDATE conversations SET unread_count = 1, updated_at = created_at WHERE id = "
        "'66000000-0000-0000-0000-000000000001'",
        "UPDATE conversations SET unread_count = 0, updated_at = created_at WHERE id = "
        "'66000000-0000-0000-0000-000000000002'",
    ):
        await db.q(stmt)


async def clear_unmarked(db: Sql) -> None:
    """Remove command-created rows (unmarked). Seed-marked rows are untouched.

    Required before the demo seed/reset controls: the accepted mvp_seed /
    mvp_reset scripts delete ONLY demo-marked rows, so an unmarked child
    (e.g. a command message referencing a seed conversation) fails their
    RESTRICT FKs closed. Documented, tested fail-closed (scenario D9b).
    """
    for stmt in (
        "DELETE FROM follow_ups WHERE demo_seed_key IS NULL",
        "DELETE FROM messages WHERE demo_seed_key IS NULL",
        "UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000001', "
        "status='new', updated_at = now() WHERE demo_seed_key = 'demo-a-lead-001'",
        "DELETE FROM leads WHERE demo_seed_key IS NULL",
        "DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL",
        "DELETE FROM conversations WHERE demo_seed_key IS NULL",
        "DELETE FROM contacts WHERE demo_seed_key IS NULL",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records",
        "DELETE FROM outbox_events",
    ):
        await db.q(stmt)


def context_for(account_id: str) -> TenantContext:
    return TenantContext(
        user=User(USER_SEEDED, "demo-user"),
        agency=Agency(AGENCY_A, "Agency A"),
        client_account=ClientAccount(account_id, AGENCY_A, "account"),
        membership=Membership(USER_SEEDED, AGENCY_A, account_id, PERMISSIONS_RW),
        correlation_id="wu7",
    )


async def run_async_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    cfg = load_database_configuration()
    db = Sql(cfg)
    await db.open()
    try:
        await preclean(db)

        identity = DemoIdentityAdapter()
        app = build_mvp_wu7_application(cfg, ACCOUNT_A, identity)
        client = TestClient(app)

        # D1. Select A on a fresh session: 200 projection, server-resolved.
        r = client.post(SELECT_ROUTE, json={"requestedAccountId": ACCOUNT_A},
                        headers={"X-Correlation-Id": "wu7-d1"})
        record("select A: 200 server-resolved projection + correlation",
               projection_ok(r, "wu7-d1", ACCOUNT_A),
               f"status={r.status_code}")

        # D2. Switch A->B: B sees only its own seed data.
        r_b = client.post(SELECT_ROUTE, json={"requestedAccountId": ACCOUNT_B},
                          headers={"X-Correlation-Id": "wu7-d2a"})
        dash_b = client.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu7-d2b"})
        inbox_b = client.get(INBOX_ROUTE, headers={"X-Correlation-Id": "wu7-d2c"})
        b_dash, b_inbox = dash_b.json(), inbox_b.json()
        dumped_b = json.dumps([b_dash, b_inbox], ensure_ascii=False)
        ok = (projection_ok(r_b, "wu7-d2a", ACCOUNT_B)
              and dash_b.status_code == 200 and b_dash["account_id"] == ACCOUNT_B
              and b_dash["counts"]["leads_total"] == 1
              and inbox_b.status_code == 200 and b_inbox["count"] == 1
              and b_inbox["items"][0]["conversation_id"] == B_CONVERSATION
              and A_CONVERSATION not in dumped_b and ACCOUNT_A not in dumped_b)
        record("switch A->B: B session shows only B seed data", ok,
               f"b_leads={b_dash.get('counts', {}).get('leads_total')}")

        # D3. Durable A-side effect survives the round-trip: run the real WU5
        # inbound command with context A, switch back, reload shows it.
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        crm = CrmRepositoryComposition(runner)
        command = ProcessInboundMessageCommand(dict(cfg))
        event = NormalizedInboundEvent(
            event_id=f"wu7-ev-{uuid4().hex[:8]}", client_account_id=ACCOUNT_A,
            conversation_id=A_CONVERSATION, message_id=str(uuid4()),
            direction="inbound", body="Efecto durable WU7",
            received_at="2026-09-06T12:00:00.000Z")
        outcome = await command.execute_event(
            context_for(ACCOUNT_A), event=event, idempotency_key=f"wu7-{uuid4().hex[:8]}")
        r_a = client.post(SELECT_ROUTE, json={"requestedAccountId": ACCOUNT_A},
                          headers={"X-Correlation-Id": "wu7-d3a"})
        dash_a = client.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu7-d3b"})
        inbox_a = client.get(INBOX_ROUTE, headers={"X-Correlation-Id": "wu7-d3c"})
        a_dash, a_inbox = dash_a.json(), inbox_a.json()
        target = next((i for i in a_inbox.get("items", [])
                       if i["conversation_id"] == A_CONVERSATION), {})
        ok = (not outcome.replayed and projection_ok(r_a, "wu7-d3a", ACCOUNT_A)
              and a_dash["counts"]["unread_messages"] == 2
              and a_dash["counts"]["follow_ups_pending"] == 2
              and (target.get("last_message") or {}).get("body") == "Efecto durable WU7")
        record("switch B->A: durable command effect visible after reload", ok,
               f"unread={a_dash.get('counts', {}).get('unread_messages')}")

        # D4. Forged/foreign select denied non-enumerating; prior context kept.
        before = await db.count("SELECT count(*) AS n FROM messages"), \
            await db.count("SELECT count(*) AS n FROM audit_records WHERE outcome='success'")
        r_forge = client.post(SELECT_ROUTE, json={"requestedAccountId": GHOST},
                              headers={"X-Correlation-Id": "wu7-d4a"})
        r_foreign_b = client.post(
            SELECT_ROUTE,
            json={"requestedAccountId": "99999999-0000-4000-8000-000000000000"},
            headers={"X-Correlation-Id": "wu7-d4b"})
        cur = client.get(CURRENT_ROUTE, headers={"X-Correlation-Id": "wu7-d4c"})
        cur_body = cur.json()
        after = await db.count("SELECT count(*) AS n FROM messages"), \
            await db.count("SELECT count(*) AS n FROM audit_records WHERE outcome='success'")
        same_shape = (r_forge.json()["error"]["code"] == r_foreign_b.json()["error"]["code"]
                      and r_forge.json()["error"]["message"] == r_foreign_b.json()["error"]["message"])
        ok = (envelope_ok(r_forge, 403, "wu7-d4a", "tenant_access_denied")
              and envelope_ok(r_foreign_b, 403, "wu7-d4b", "tenant_access_denied")
              and same_shape
              and cur.status_code == 200 and cur_body["client_account_id"] == ACCOUNT_A
              and before == after)
        record("forged/foreign select 403 one shape; prior context A preserved; no mutation", ok,
               f"current={cur_body.get('client_account_id')}")

        # D5. Malformed intents rejected before SQL; context preserved.
        cases = [
            ("wu7-d5a", {}, 400, "invalid_selector_intent"),
            ("wu7-d5b", {"requestedAccountId": ACCOUNT_A, "extra": 1}, 400, "invalid_selector_intent"),
            ("wu7-d5c", {"requestedAccountId": "  "}, 400, "invalid_selector_intent"),
            ("wu7-d5d", {"requestedAccountId": "../escape"}, 400, "invalid_selector_intent"),
        ]
        malformed_ok = True
        for corr, payload, status, code in cases:
            resp = client.post(SELECT_ROUTE, json=payload,
                               headers={"X-Correlation-Id": corr})
            malformed_ok = malformed_ok and envelope_ok(resp, status, corr, code)
        cur2 = client.get(CURRENT_ROUTE, headers={"X-Correlation-Id": "wu7-d5e"})
        record("malformed intents 400 pre-SQL; context still A",
               malformed_ok and cur2.json()["client_account_id"] == ACCOUNT_A,
               f"all_400={malformed_ok}")

        # D6. Absent server identity: select + current + seed all 401.
        app_none = build_mvp_wu7_application(cfg, ACCOUNT_A, AbsentDemoIdentityAdapter())
        none_client = TestClient(app_none)
        r_s = none_client.post(SELECT_ROUTE, json={"requestedAccountId": ACCOUNT_A},
                               headers={"X-Correlation-Id": "wu7-d6a"})
        r_c = none_client.get(CURRENT_ROUTE, headers={"X-Correlation-Id": "wu7-d6b"})
        r_seed = none_client.post(SEED_ROUTE, headers={"X-Correlation-Id": "wu7-d6c"})
        record("no identity: select/current/seed 401 envelope + correlation",
               envelope_ok(r_s, 401, "wu7-d6a", "authentication_required")
               and envelope_ok(r_c, 401, "wu7-d6b", "authentication_required")
               and envelope_ok(r_seed, 401, "wu7-d6c", "authentication_required"),
               f"{r_s.status_code}/{r_c.status_code}/{r_seed.status_code}")

        # D7. Idempotency key committed for A replayed against B with a
        # different payload -> conflict, zero B mutation, zero B success audit.
        b_msgs_before = await db.count(
            "SELECT count(*) AS n FROM messages WHERE client_account_id = %s", (ACCOUNT_B,))
        key_x = f"wu7-x-{uuid4().hex[:8]}"
        ev_a = NormalizedInboundEvent(
            event_id=f"wu7-xa-{uuid4().hex[:8]}", client_account_id=ACCOUNT_A,
            conversation_id=A_CONVERSATION, message_id=str(uuid4()),
            direction="inbound", body="reserva A", received_at="2026-09-06T14:00:00.000Z")
        await command.execute_event(context_for(ACCOUNT_A), event=ev_a, idempotency_key=key_x)
        ev_b = NormalizedInboundEvent(
            event_id=f"wu7-xb-{uuid4().hex[:8]}", client_account_id=ACCOUNT_B,
            conversation_id=B_CONVERSATION, message_id=str(uuid4()),
            direction="inbound", body="intrusion B", received_at="2026-09-06T14:00:00.000Z")
        conflict = False
        try:
            await command.execute_event(context_for(ACCOUNT_B), event=ev_b, idempotency_key=key_x)
        except IdempotencyConflict:
            conflict = True
        b_msgs_after = await db.count(
            "SELECT count(*) AS n FROM messages WHERE client_account_id = %s", (ACCOUNT_B,))
        b_success = await db.count(
            "SELECT count(*) AS n FROM audit_records WHERE outcome='success' "
            "AND client_account_id = %s", (ACCOUNT_B,))
        record("cross-account key replay: conflict, B untouched, no B success audit",
               conflict and b_msgs_after == b_msgs_before and b_success == 0,
               f"conflict={conflict} b_msgs={b_msgs_after}")

        # D8. Scoped cache: miss->hit same scope; cross-scope key denied with
        # zero fill; invalidation forces a fresh miss.
        cache = app.state.demo_composition.cache
        key_a = build_scoped_key(context_for(ACCOUNT_A), "probe", "unit")
        key_b = build_scoped_key(context_for(ACCOUNT_B), "probe", "unit")
        ok_shape = (key_a != key_b and ACCOUNT_A in key_a and AGENCY_A in key_a
                    and ACCOUNT_B in key_b)
        v1, hit1 = await cache.get_or_fill(context_for(ACCOUNT_A), key_a,
                                           lambda ctx: asyncio.sleep(0, result="payload-a"))
        v2, hit2 = await cache.get_or_fill(context_for(ACCOUNT_A), key_a,
                                           lambda ctx: asyncio.sleep(0, result="payload-a2"))
        def _must_not_fill(ctx):
            raise AssertionError("cross-scope fill must never run")
        denied = False
        try:
            await cache.get_or_fill(context_for(ACCOUNT_B), key_a, _must_not_fill)
        except Exception as error:
            denied = type(error).__name__ == "TenantAccessDenied"
        record("cache: miss->hit same scope; B denied on A's key with zero fill",
               ok_shape and not hit1 and hit2 and v1 == v2 == "payload-a"
               and denied and cache.hits >= 1 and cache.misses >= 1,
               f"hits={cache.hits} misses={cache.misses}")
        # HTTP-level: invalidate the A scope, then dashboard miss then hit,
        # with per-request correlation echoed on the hit.
        flushed = cache.invalidate_scope(AGENCY_A, ACCOUNT_A)
        h1 = client.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu7-d8a"})
        h2 = client.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu7-d8b"})
        ok = (flushed >= 1
              and h1.headers.get("x-cache-status") == "miss"
              and h2.headers.get("x-cache-status") == "hit"
              and h2.json()["correlation_id"] == "wu7-d8b"
              and h2.headers.get("x-correlation-id") == "wu7-d8b")
        record("HTTP cache: invalidate->miss->hit with per-request correlation on hit", ok,
               f"flushed={flushed} {h1.headers.get('x-cache-status')}->{h2.headers.get('x-cache-status')}")

        # D9. Seed/reset controls: unmarked scratch row survives; reset
        # restores deterministic seed state; cache flushed by reset.
        await clear_unmarked(db)  # removes D3/D7 command rows; seed markers untouched
        await db.q("INSERT INTO contacts (id, client_account_id, name) VALUES (%s, %s, 'Scratch') "
                   "ON CONFLICT (id) DO NOTHING", (SCRATCH_CONTACT, ACCOUNT_A))
        r_reset = client.post(RESET_ROUTE, headers={"X-Correlation-Id": "wu7-d9a"})
        reset_body = r_reset.json()
        scratch = await db.one("SELECT id FROM contacts WHERE id = %s", (SCRATCH_CONTACT,))
        a_contacts = await db.count(
            "SELECT count(*) AS n FROM contacts WHERE client_account_id = %s", (ACCOUNT_A,))
        h3 = client.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu7-d9b"})
        dash_after = h3.json()
        ok = (r_reset.status_code == 200 and reset_body.get("reset") is True
              and reset_body.get("correlation_id") == "wu7-d9a"
              and scratch is not None and a_contacts == 2  # seed contact + scratch
              and dash_after["counts"]["leads_total"] == 1
              and dash_after["counts"]["unread_messages"] == 1
              and h3.headers.get("x-cache-status") == "miss")
        record("reset: scratch survives, seed state restored, cache flushed", ok,
               f"a_contacts={a_contacts} leads={dash_after['counts'].get('leads_total')}")
        await db.q("DELETE FROM contacts WHERE id = %s", (SCRATCH_CONTACT,))
        r_seed = client.post(SEED_ROUTE, headers={"X-Correlation-Id": "wu7-d9c"})
        record("seed re-apply converges (200, idempotent)",
               r_seed.status_code == 200 and r_seed.json().get("seeded") is True,
               f"status={r_seed.status_code}")

        # D9b. Fail-closed: an unmarked child of a seed row blocks reset with
        # an explicit 500 and leaves seed state untouched.
        await db.q("INSERT INTO messages (id, client_account_id, conversation_id, direction, body) "
                   "VALUES (%s, %s, %s, 'inbound', 'hijo sin marca')",
                   (str(uuid4()), ACCOUNT_A, A_CONVERSATION))
        conv_before = await db.one("SELECT id FROM conversations WHERE id = %s", (A_CONVERSATION,))
        r_blocked = client.post(RESET_ROUTE, headers={"X-Correlation-Id": "wu7-d9d"})
        conv_after = await db.one("SELECT id FROM conversations WHERE id = %s", (A_CONVERSATION,))
        ok = (envelope_ok(r_blocked, 500, "wu7-d9d", "demo_script_failed")
              and conv_before is not None and conv_after is not None)
        record("reset blocked by unmarked child: 500, seed rows untouched", ok,
               f"status={r_blocked.status_code}")
        await db.q("DELETE FROM messages WHERE demo_seed_key IS NULL")
        r_reset2 = client.post(RESET_ROUTE, headers={"X-Correlation-Id": "wu7-d9e"})
        record("reset succeeds again after clearing unmarked children",
               r_reset2.status_code == 200 and r_reset2.json().get("reset") is True,
               f"status={r_reset2.status_code}")

        # D10. Unknown route + wrong method share the envelope (never default).
        r_404 = client.get("/v1/demo/nope", headers={"X-Correlation-Id": "wu7-d10a"})
        r_405 = client.get(SELECT_ROUTE, headers={"X-Correlation-Id": "wu7-d10b"})
        record("unknown route 404 / GET-to-POST 405 share error envelope",
               envelope_ok(r_404, 404, "wu7-d10a", "route_not_found")
               and envelope_ok(r_405, 405, "wu7-d10b", "method_not_allowed"),
               f"{r_404.status_code}/{r_405.status_code}")
    finally:
        await db.close()
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return asyncio.run(run_async_scenarios())


def test_demo_context():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"MVP-CRM WU7 DEMO CONTEXT SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
