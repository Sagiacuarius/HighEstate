"""MVP Commercial Demo WU6: versioned tenant-scoped READ API tests.

Runs against production_runtime_mvp_wu6 (disposable database: migrations
001-004 + deterministic mvp_seed + WU5-shaped command fixtures created here).
Never touches production_runtime_mvp_wu2/wu3/wu4/wu5.

Config: RUNTIME_DB_HOST/PORT/NAME/USER env vars; password via libpq pgpass.

Covers: every read surface returns DB-derived counts matching direct SQL; a
WU5 inbound command executed through the real composition is visible in
dashboard/inbox/leads/activity (GET-after-command loop); scope is server-owned
(payload/param ids never authorize); foreign property/conversation ids share
the missing 404 non-enumerating shape; 401 without identity; unknown route and
wrong method share envelope shape; correlation header/body consistency on all
responses; activity never leaks raw bodies or metadata; account B sees nothing
of A; limit validation; reload determinism.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.application.crm_commands_wu5 import ProcessInboundMessageCommand
from api.composition_wu5_mvp import build_mvp_wu5_application
from api.composition_wu6_mvp import API_PREFIX, build_mvp_wu6_application
from api.inbound_boundary import NormalizedInboundEvent
from api.tenant_boundary import InMemoryIdentityAdapter, User

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_SEEDED = "10000000-0000-0000-0000-000000000001"
A_CONVERSATION = "66000000-0000-0000-0000-000000000001"
B_CONVERSATION = "66000000-0000-0000-0000-000000000002"
A_PROPERTY = "62000000-0000-0000-0000-000000000001"
B_PROPERTY = "62000000-0000-0000-0000-000000000002"
# valid-shape UUID that exists in no account
GHOST = "0000dead-0000-4000-8000-000000000000"


def envelope_ok(response, status: int, correlation: str, code: str | None = None) -> bool:
    if response.status_code != status:
        return False
    body = response.json()
    if "error" not in body:
        return False
    err = body["error"]
    return (err.get("correlation_id") == correlation
            and response.headers.get("x-correlation-id") == correlation
            and (code is None or err.get("code") == code)
            and isinstance(err.get("message"), str) and err["message"])


def header_ok(response, correlation: str) -> bool:
    body = response.json()
    return (response.headers.get("x-correlation-id") == correlation
            and body.get("correlation_id") == correlation)


class Sql:
    """Direct-SQL oracle: every count the read API exposes is checked here."""

    def __init__(self, kwargs: dict):
        self._kwargs = kwargs
        self.conn: psycopg.AsyncConnection | None = None

    async def open(self) -> None:
        self.conn = await psycopg.AsyncConnection.connect(
            **self._kwargs, autocommit=True, row_factory=dict_row)

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()

    async def one(self, sql: str, params: tuple = ()) -> dict:
        async with self.conn.cursor() as cur:
            await cur.execute(sql, params)
            return (await cur.fetchone())


async def preclean(db: Sql) -> None:
    """Idempotent re-runs: remove unmarked rows and audit/outbox/idempotency."""
    for stmt in (
        "DELETE FROM follow_ups WHERE demo_seed_key IS NULL",
        "DELETE FROM messages WHERE demo_seed_key IS NULL",
        "UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000001', "
        "status='new', updated_at = now() WHERE demo_seed_key = 'demo-a-lead-001'",
        "DELETE FROM leads WHERE demo_seed_key IS NULL",
        "DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL",
        "DELETE FROM conversations WHERE demo_seed_key IS NULL",
        "DELETE FROM contacts WHERE demo_seed_key IS NULL",
        "UPDATE conversations SET unread_count = 1, updated_at = created_at WHERE id = "
        "'66000000-0000-0000-0000-000000000001'",
        "UPDATE conversations SET unread_count = 0, updated_at = created_at WHERE id = "
        "'66000000-0000-0000-0000-000000000002'",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records",
        "DELETE FROM outbox_events",
    ):
        async with db.conn.cursor() as cur:
            await cur.execute(stmt)


async def run_async_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    cfg = load_database_configuration()
    db = Sql(cfg)
    await db.open()
    try:
        await preclean(db)

        identity = InMemoryIdentityAdapter(User(USER_SEEDED, "demo-user"))
        app_a = build_mvp_wu6_application(cfg, ACCOUNT_A, identity)
        app_b = build_mvp_wu6_application(cfg, ACCOUNT_B, identity)
        client_a = TestClient(app_a)
        client_b = TestClient(app_b)

        # R1. Baseline dashboard: seed-only counts match direct SQL exactly.
        seed_counts = await db.one(
            """
            SELECT (SELECT count(*) FROM leads WHERE client_account_id = %(a)s) AS leads,
                   (SELECT count(*) FROM properties WHERE client_account_id = %(a)s) AS props,
                   (SELECT COALESCE(sum(unread_count),0) FROM conversations
                     WHERE client_account_id = %(a)s AND status='open') AS unread
            """, {"a": ACCOUNT_A})
        r = client_a.get(f"{API_PREFIX}/dashboard", headers={"X-Correlation-Id": "wu6-r1"})
        body = r.json() if r.status_code == 200 else {}
        ok = (r.status_code == 200 and header_ok(r, "wu6-r1")
              and body["counts"]["leads_total"] == int(seed_counts["leads"])
              and body["counts"]["properties_total"] == int(seed_counts["props"])
              and body["counts"]["unread_messages"] == int(seed_counts["unread"])
              and body["account_id"] == ACCOUNT_A and "as_of" in body)
        record("dashboard seed counts == direct SQL", ok,
               f"leads={body.get('counts', {}).get('leads_total')}")

        # R2. Run the real WU5 inbound command (atomic, durable), then GET-after-command.
        ctx_user = User(USER_SEEDED, "demo-user")
        from api.composition_wu3_mvp import CrmRepositoryComposition
        from api.adapters.postgres_core import ConnectionPool, PostgresRunner
        from api.tenant_boundary import TenantContext, Agency, ClientAccount, Membership
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        crm = CrmRepositoryComposition(runner)
        command = ProcessInboundMessageCommand(dict(cfg))
        event = NormalizedInboundEvent(
            event_id=f"wu6-ev-{uuid4().hex[:8]}", client_account_id=ACCOUNT_A,
            conversation_id=A_CONVERSATION, message_id=str(uuid4()),
            direction="inbound", body="Consulta desde la demo WU6",
            received_at="2026-09-06T12:00:00.000Z")
        permission = frozenset({"client_account:read", "client_account:write"})
        ctx_a = TenantContext(
            user=ctx_user, agency=Agency(AGENCY_A, "Agency A"),
            client_account=ClientAccount(ACCOUNT_A, AGENCY_A, "Aurea Realty North"),
            membership=Membership(USER_SEEDED, AGENCY_A, ACCOUNT_A, permission),
            correlation_id="wu6-command")
        outcome = await command.execute_event(ctx_a, event=event,
                                              idempotency_key=f"wu6-{uuid4().hex[:8]}")
        r_dash = client_a.get(f"{API_PREFIX}/dashboard", headers={"X-Correlation-Id": "wu6-r2a"})
        r_inbox = client_a.get(f"{API_PREFIX}/inbox", headers={"X-Correlation-Id": "wu6-r2b"})
        r_leads = client_a.get(f"{API_PREFIX}/leads", headers={"X-Correlation-Id": "wu6-r2c"})
        dash = r_dash.json()
        inbox = r_inbox.json()
        leads = r_leads.json()
        target = next((i for i in inbox.get("items", [])
                       if i["conversation_id"] == A_CONVERSATION), {})
        ok = (not outcome.replayed
              and r_dash.status_code == 200 and r_inbox.status_code == 200
              and r_leads.status_code == 200
              and dash["counts"]["unread_messages"] == 2  # seed 1 + inbound 1
              and dash["counts"]["follow_ups_pending"] == 2
              and target.get("unread_count", -1) == 2
              and (target.get("last_message") or {}).get("body") == "Consulta desde la demo WU6"
              and any(l["lead_id"] == outcome.payload["resource_id"] for l in leads["items"]))
        record("WU5 inbound then GET dashboard/inbox/leads shows durable effects", ok,
               f"unread_dash={dash['counts'].get('unread_messages')} inbox_unread={target.get('unread_count')}")

        # R3. Activity read model shows the WU5 success audit (action/outcome/
        # correlation) and NOTHING leaks raw bodies/metadata secrets.
        r_act = client_a.get(f"{API_PREFIX}/activity", headers={"X-Correlation-Id": "wu6-r3"})
        act = r_act.json()
        inbound_rows = [i for i in act.get("items", []) if i["action"] == "crm.inbound.process"]
        dumped = json.dumps(act, ensure_ascii=False)
        ok = (r_act.status_code == 200 and header_ok(r_act, "wu6-r3")
              and any(i["outcome"] == "success" and i["correlation_id"] == "wu6-command"
                      for i in inbound_rows)
              and act["total"] == len(act["items"])
              and "Consulta desde la demo WU6" not in dumped
              and "raw_body" not in dumped and "metadata" not in dumped)
        record("activity shows action/outcome/correlation, no raw bodies leaked", ok,
               f"items={len(act.get('items', []))}")

        # R4. Pipeline + agenda reads derive from DB.
        pipe_sql = await db.one(
            """
            SELECT count(*) FILTER (WHERE l.status='new') AS new_now
            FROM leads l JOIN pipeline_stages s ON s.id = l.stage_id
            WHERE l.client_account_id = %(a)s
            """, {"a": ACCOUNT_A})
        r_pipe = client_a.get(f"{API_PREFIX}/pipeline", headers={"X-Correlation-Id": "wu6-r4a"})
        r_ag = client_a.get(f"{API_PREFIX}/agenda", headers={"X-Correlation-Id": "wu6-r4b"})
        pipe = r_pipe.json()
        ag = r_ag.json()
        stage_counts = {s["stage_id"]: s["lead_count"]
                        for p in pipe.get("items", []) for s in p["stages"]}
        sql_stage = await db.one(
            "SELECT count(*) AS n FROM leads WHERE client_account_id = %s AND stage_id = %s",
            (ACCOUNT_A, "64000000-0000-0000-0000-000000000001"))
        ok = (r_pipe.status_code == 200 and r_ag.status_code == 200
              and pipe["count"] == 1
              and stage_counts.get("64000000-0000-0000-0000-000000000001", -1) == int(sql_stage["n"])
              and ag["counts"]["pending"] == 2
              and all(re.match(r"\d{4}-\d{2}-\d{2}T.*Z$", i["due_at"]) for i in ag["items"]))
        record("pipeline stage lead_count and agenda counts match SQL", ok,
               f"stage_new={stage_counts.get('64000000-0000-0000-0000-000000000001')} pend={ag['counts'].get('pending')}")

        # R5. Properties list + detail; B property is invisible to A (404 same shape).
        r_props = client_a.get(f"{API_PREFIX}/properties", headers={"X-Correlation-Id": "wu6-r5a"})
        r_own = client_a.get(f"{API_PREFIX}/properties/{A_PROPERTY}",
                             headers={"X-Correlation-Id": "wu6-r5b"})
        r_foreign = client_a.get(f"{API_PREFIX}/properties/{B_PROPERTY}",
                                 headers={"X-Correlation-Id": "wu6-r5c"})
        r_ghost = client_a.get(f"{API_PREFIX}/properties/{GHOST}",
                               headers={"X-Correlation-Id": "wu6-r5d"})
        props = r_props.json()
        own = r_own.json()
        ok = (r_props.status_code == 200 and props["count"] == 1
              and props["items"][0]["price"] == "185000.00"
              and r_own.status_code == 200 and own["property_id"] == A_PROPERTY
              and own["account_id"] == ACCOUNT_A
              and envelope_ok(r_foreign, 404, "wu6-r5c", "resource_not_found")
              and envelope_ok(r_ghost, 404, "wu6-r5d", "resource_not_found")
              and r_foreign.json()["error"]["message"] == r_ghost.json()["error"]["message"])
        record("properties scoped; foreign id indistinguishable from missing (404 one shape)", ok,
               f"foreign={r_foreign.status_code} ghost={r_ghost.status_code}")

        # R6. Messages of A's conversation visible; B's conversation 404 for A.
        r_msg_ok = client_a.get(f"{API_PREFIX}/inbox/{A_CONVERSATION}/messages",
                                headers={"X-Correlation-Id": "wu6-r6a"})
        r_msg_x = client_a.get(f"{API_PREFIX}/inbox/{B_CONVERSATION}/messages",
                               headers={"X-Correlation-Id": "wu6-r6b"})
        msgs = r_msg_ok.json()
        dumped_msgs = json.dumps(msgs, ensure_ascii=False)
        # confirm against SQL the visible bodies are exactly A's
        sql_bodies = await db.one(
            "SELECT string_agg(body, '|' ORDER BY created_at, id) AS b FROM messages "
            "WHERE client_account_id = %s AND conversation_id = %s", (ACCOUNT_A, A_CONVERSATION))
        ok = (r_msg_ok.status_code == 200 and msgs["count"] == 2
              and "Consulta desde la demo WU6" in dumped_msgs
              and sql_bodies["b"] and "Palermo" in dumped_msgs
              and envelope_ok(r_msg_x, 404, "wu6-r6b", "resource_not_found"))
        record("conversation messages DB-derived; B conversation 404 under A session", ok,
               f"count={msgs.get('count')}")

        # R7. Account B session: sees only its own data; A's effects absent.
        r_b_dash = client_b.get(f"{API_PREFIX}/dashboard", headers={"X-Correlation-Id": "wu6-r7a"})
        r_b_inbox = client_b.get(f"{API_PREFIX}/inbox", headers={"X-Correlation-Id": "wu6-r7b"})
        r_b_act = client_b.get(f"{API_PREFIX}/activity", headers={"X-Correlation-Id": "wu6-r7c"})
        b_dash = r_b_dash.json()
        b_inbox = r_b_inbox.json()
        b_act = r_b_act.json()
        dumped_b = json.dumps([b_dash, b_inbox, b_act], ensure_ascii=False)
        ok = (r_b_dash.status_code == 200 and b_dash["account_id"] == ACCOUNT_B
              and b_dash["counts"]["leads_total"] == 1
              and b_inbox["count"] == 1
              and b_inbox["items"][0]["conversation_id"] == B_CONVERSATION
              and b_act["total"] == 0
              and A_CONVERSATION not in dumped_b and ACCOUNT_A not in dumped_b
              and "Consulta desde la demo WU6" not in dumped_b)
        record("B session: own counts only, zero A records visible, empty activity", ok,
               f"b_leads={b_dash['counts'].get('leads_total')} b_act={b_act.get('total')}")

        # R8. Absent identity -> 401 envelope on every surface (fail closed).
        app_none = build_mvp_wu6_application(cfg, ACCOUNT_A, InMemoryIdentityAdapter(None))
        none_client = TestClient(app_none)
        statuses = []
        for surface in ("dashboard", "inbox", "leads", "properties", "pipeline", "agenda", "activity"):
            resp = none_client.get(f"{API_PREFIX}/{surface}",
                                   headers={"X-Correlation-Id": f"wu6-r8-{surface}"})
            statuses.append(envelope_ok(resp, 401, f"wu6-r8-{surface}", "authentication_required"))
        record("no identity: 401 envelope with correlation on all 7 surfaces", all(statuses),
               f"all_401={all(statuses)}")

        # R9. Client cannot forge scope: the route takes no account id at all;
        # a query-param account id must be IGNORED, not trusted.
        r_forge = client_a.get(f"{API_PREFIX}/dashboard?clientAccountId={ACCOUNT_B}",
                               headers={"X-Correlation-Id": "wu6-r9"})
        forged = r_forge.json()
        ok = (r_forge.status_code == 200 and forged["account_id"] == ACCOUNT_A
              and forged["counts"]["leads_total"] != b_dash["counts"]["leads_total"] - 1
              and forged["counts"]["follow_ups_pending"] == 2)
        record("forged clientAccountId query param ignored; scope stays server-derived", ok,
               f"account={forged.get('account_id')}")

        # R10. Unknown route and wrong method -> v1 envelope, never FastAPI default.
        r_404 = client_a.get("/v1/crm/nonexistent", headers={"X-Correlation-Id": "wu6-r10a"})
        r_404b = client_a.get("/totally/unknown", headers={"X-Correlation-Id": "wu6-r10b"})
        r_405 = client_a.post(f"{API_PREFIX}/dashboard", json={},
                              headers={"X-Correlation-Id": "wu6-r10c"})
        ok = (envelope_ok(r_404, 404, "wu6-r10a", "route_not_found")
              and envelope_ok(r_404b, 404, "wu6-r10b", "route_not_found")
              and envelope_ok(r_405, 405, "wu6-r10c", "method_not_allowed"))
        record("unknown route 404 / POST-to-GET 405 share error envelope", ok,
               f"{r_404.status_code}/{r_404b.status_code}/{r_405.status_code}")

        # R11. activity limit validation happens before SQL-side work.
        r_bad = client_a.get(f"{API_PREFIX}/activity?limit=abc",
                             headers={"X-Correlation-Id": "wu6-r11a"})
        r_hi = client_a.get(f"{API_PREFIX}/activity?limit=9999",
                            headers={"X-Correlation-Id": "wu6-r11b"})
        r_zero = client_a.get(f"{API_PREFIX}/activity?limit=0",
                              headers={"X-Correlation-Id": "wu6-r11c"})
        r_ok = client_a.get(f"{API_PREFIX}/activity?limit=1",
                            headers={"X-Correlation-Id": "wu6-r11d"})
        ok = (envelope_ok(r_bad, 400, "wu6-r11a", "invalid_query")
              and envelope_ok(r_hi, 400, "wu6-r11b", "invalid_query")
              and envelope_ok(r_zero, 400, "wu6-r11c", "invalid_query")
              and r_ok.status_code == 200 and r_ok.json()["limit"] == 1)
        record("activity limit validated (abc/9999/0 -> 400; 1 -> page of 1)", ok)

        # R12. Deterministic reload: two identical GETs return identical bodies
        # (modulo correlation echoed), and the durable WU5 effect survives "reload".
        r1 = client_a.get(f"{API_PREFIX}/inbox", headers={"X-Correlation-Id": "wu6-r12a"}).json()
        r2 = client_a.get(f"{API_PREFIX}/inbox", headers={"X-Correlation-Id": "wu6-r12b"}).json()
        # persisted state must be byte-identical; clock/correlation metadata excluded
        for payload in (r1, r2):
            payload.pop("correlation_id", None)
            payload.pop("as_of", None)
        ok = (r1 == r2
              and r1["items"][0]["conversation_id"] == A_CONVERSATION)
        record("reload determinism: identical persisted inbox across calls", ok)

        # R13. Command audit denial path visible via activity WITHOUT leaking ids:
        # force a cross-scope denial via the direct command (context A, conv B).
        event_x = NormalizedInboundEvent(
            event_id=f"wu6x-{uuid4().hex[:8]}", client_account_id=ACCOUNT_A,
            conversation_id=B_CONVERSATION, message_id=str(uuid4()), direction="inbound",
            body="no debe persistirse", received_at="2026-09-06T13:00:00.000Z")
        denial_seen = False
        try:
            await command.execute_event(ctx_a, event=event_x,
                                        idempotency_key=f"wu6-x-{uuid4().hex[:8]}")
        except Exception as error:
            denial_seen = type(error).__name__ == "CrmCommandDenied"
        r_act2 = client_a.get(f"{API_PREFIX}/activity", headers={"X-Correlation-Id": "wu6-r13"})
        act2 = r_act2.json()
        denied = [i for i in act2["items"] if i["outcome"] == "denied"]
        dumped2 = json.dumps(act2, ensure_ascii=False)
        ok = (denial_seen and len(denied) >= 1
              and all(i["resource_id"] is None for i in denied)
              and all(i.get("reason") for i in denied)
              and B_CONVERSATION not in dumped2 and "no debe persistirse" not in dumped2)
        record("denied command surfaces in activity redacted (reason class, no ids/bodies)", ok,
               f"denied={len(denied)}")
    finally:
        await db.close()
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return asyncio.run(run_async_scenarios())


def test_crm_reads():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"MVP-CRM WU6 READ SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
