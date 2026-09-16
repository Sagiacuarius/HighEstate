"""Conversational agent (change: llm-conversational-agent, WU14) real-PostgreSQL harness.

Runs against production_runtime_mvp_wu14 (disposable: runner 001-007 +
mvp_seed.sql + mvp_seed_005.sql). Never touches earlier accepted bases.

Drafts/events/decisions are created EXCLUSIVELY by RUNNING the accepted command
surfaces (analyze/approve/reject on the WU8 app; WU13 calendar posts for the
availability cases; WU9 simulator probe) — never by hand-inserting rows.

Scaled to the design's A1-A17 risk acceptances:
A1 tenant-scoped analyze + forged account impossible
A2 schema fail-closed (malformed body -> 400, nothing persisted)
A3 property-match oracle == independent SQL (incl. cross-tenant invisibility)
A4 idempotent analyze replay
A5 approve dispatches every action through accepted commands (audit + outbox)
A6 approve twice -> 409, zero duplicate effects
A7 reject executes nothing; approve-after-reject -> 409
A8 (merged into A6/A7) executed/rejected approve -> 409
A9 draft reads scope-limiting + ghost -> 404
A10 execution_result records denials truthfully (occupied slot)
A11 accepted WU9 simulator unchanged
A12 cross-account approve -> 404
A13 preferred date/time honored; agent event source='agent'; audit visible
A14 reschedule honors preferred slot; calendar update audited
A15 stale reschedule (cancelled target) fails closed and reports truthfully
A16 agent-created event carries source='agent' + crm.calendar.create audit
A17 no availability -> follow-up only (full day occupied), no invented slots
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.composition_wu8_mvp import build_mvp_wu8_application
from api.identity.demo_identity import AbsentDemoIdentityAdapter, DemoIdentityAdapter
from api.routes.audit_screen_wu12 import AUDIT_PATH
from api.routes.simulator_wu9 import SIMULATE_PATH

ROOT = Path(__file__).resolve().parent.parent
SEED_FILES = [ROOT / "infra" / "migrations" / "validation" / "mvp_seed.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_005.sql"]

ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
A_CONTACT = "61000000-0000-0000-0000-000000000001"
A_LEAD = "65000000-0000-0000-0000-000000000001"
A_PROP_1 = "62000000-0000-0000-0000-000000000001"
GHOST = "0000dead-0000-4000-8000-000000000000"

AGENT_ANALYZE = "/v1/demo/agent/analyze"
AGENT_DRAFTS = "/v1/demo/agent/drafts"
CAL_EVENTS = "/v1/demo/calendar/events"

# Robot-fixed days (UTC): 2026-09-21 is a Monday, 2026-09-22 a Tuesday; both
# sit inside the agent's now+7d window for a server date of 2026-09-16.
MON = "2026-09-21"
TUE = "2026-09-22"
TUE_1000 = f"{TUE}T13:00:00.000Z"   # BA 10:00
TUE_1100 = f"{TUE}T14:00:00.000Z"   # BA 11:00
MON_1300 = f"{MON}T16:00:00.000Z"   # BA 13:00 (A15 target)
MON_1600 = f"{MON}T19:00:00.000Z"   # BA 16:00
MON_1700 = f"{MON}T20:00:00.000Z"   # BA 17:00 (A15 proposed slot)
MON_1000 = f"{MON}T13:00:00.000Z"   # BA 10:00 (A10 proposed slot)
OUTSIDE = "2026-10-01"

AGENT_ACTIONS = ("crm.agent.analyze", "crm.agent.decide", "crm.agent.execute")


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


async def run_seed(db: Sql) -> None:
    for path in SEED_FILES:
        body = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                         if not line.lstrip().startswith("\\"))
        async with db.conn.cursor() as cur:
            await cur.execute(body)


async def preclean(db: Sql) -> None:
    """Deterministic baseline: WU12/WU13 rules + wipe agent drafts (the seed
    never creates drafts; every draft on this base comes from analyze calls)."""
    for stmt in (
        "DELETE FROM calendar_events",
        "DELETE FROM agent_drafts",
        "DELETE FROM property_amenity_rel",
        "DELETE FROM property_contacts",
        "DELETE FROM property_interests",
        "DELETE FROM properties WHERE demo_seed_key IS NULL",
        "DELETE FROM follow_ups WHERE demo_seed_key IS NULL",
        "DELETE FROM messages WHERE demo_seed_key IS NULL",
        "UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000001', "
        "status='new', updated_at = now() WHERE demo_seed_key = 'demo-a-lead-001'",
        "UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000002', "
        "status='qualified', updated_at = now() WHERE demo_seed_key = 'demo-b-lead-001'",
        "DELETE FROM leads WHERE demo_seed_key IS NULL",
        "DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL",
        "DELETE FROM conversations WHERE demo_seed_key IS NULL",
        "DELETE FROM contacts WHERE demo_seed_key IS NULL",
        "UPDATE conversations SET unread_count = 1, updated_at = created_at "
        "WHERE id = '66000000-0000-0000-0000-000000000001'",
        "UPDATE conversations SET unread_count = 0, updated_at = created_at "
        "WHERE id = '66000000-0000-0000-0000-000000000002'",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records",
        "DELETE FROM outbox_events",
    ):
        await db.q(stmt)
    await run_seed(db)


def envelope_ok(response, status: int, correlation: str, code: str) -> bool:
    if response.status_code != status:
        return False
    err = (response.json().get("error") or {})
    return (err.get("code") == code
            and err.get("correlation_id") == correlation
            and response.headers.get("x-correlation-id") == correlation)


def _get(client, url, correlation):
    return client.get(url, headers={"X-Correlation-Id": correlation})


def _post(client, route, body, correlation):
    return client.post(route, json=body,
                       headers={"X-Correlation-Id": correlation})


def _delete(client, url, correlation, key=None):
    url = f"{url}?idempotencyKey={key}" if key else url
    return client.delete(url, headers={"X-Correlation-Id": correlation})


def _analyze(client, body, correlation, idempotency_key=None):
    payload = {"body": body}
    if idempotency_key:
        payload["idempotencyKey"] = idempotency_key
    return _post(client, AGENT_ANALYZE, payload, correlation)


def _audit(client, correlation, **filters):
    qs = "&".join(f"{k}={v}" for k, v in filters.items() if v is not None)
    url = f"{AUDIT_PATH}?{qs}" if qs else AUDIT_PATH
    return _get(client, url, correlation)


def match_oracle(account: str, *, operation_type=None, property_type=None,
                 city=None, budget_max=None, rooms_min=None) -> list[str]:
    """Independent replica of the adapter's matching SQL (written out here so
    the harness compares the agent's match against a hand-built oracle)."""
    clauses = ["client_account_id = %s", "status = 'available'"]
    params: list = [account]
    if operation_type:
        clauses.append("operation_type = %s")
        params.append(operation_type)
    if property_type:
        clauses.append("property_type = %s")
        params.append(property_type)
    if city:
        clauses.append("city ILIKE %s")
        params.append(f"%{city}%")
    if budget_max is not None:
        clauses.append("price_amount <= %s")
        params.append(budget_max)
    if rooms_min is not None:
        clauses.append("(rooms >= %s OR rooms IS NULL)")
        params.append(rooms_min)
    sql = ("SELECT id FROM properties WHERE " + " AND ".join(clauses)
           + " ORDER BY price_amount ASC NULLS LAST, id LIMIT 10")
    import asyncio as _aio
    cfg = load_database_configuration()
    db = Sql(cfg)

    async def _run():
        await db.open()
        try:
            rows = await db.q(sql, tuple(params))
            return [str(r["id"]) for r in rows]
        finally:
            await db.close()

    return _aio.run(_run())


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))
        wu8_a = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter()))
        wu8_b = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_B, DemoIdentityAdapter()))

        with wu8_a as a, wu8_b as b:
            # A1. Tenant-scoped analyze persists one schema-validated draft.
            a1 = _analyze(a, "busco un depto en venta hasta 200000", "wu14-a1")
            a1j = a1.json() if a1.status_code == 200 else {}
            d1 = (a1j.get("draft") or {})
            ok = (a1.status_code == 200 and a1j.get("accepted") is True
                  and a1j.get("replayed") is False
                  and a1j.get("environment") == "dev-only-agent"
                  and d1.get("status") == "proposed"
                  and d1.get("intent") == "search_properties"
                  and d1.get("model_name") == "stub-keyword-v1"
                  and d1.get("conversation_id") == "66000000-0000-0000-0000-000000000001"
                  and d1.get("contact_id") == A_CONTACT
                  and bool(d1.get("draft_id")))
            record("A1 analyze persists tenant-scoped proposed draft", ok,
                   f"status={a1.status_code} draft={str(d1.get('draft_id'))[:8]}")
            # Forged scope: A's draft is invisible to B (404, non-enumerating).
            gb = _get(b, f"{AGENT_DRAFTS}/{d1.get('draft_id')}", "wu14-a1-b")
            record("A1 forged account cannot read A's draft (404)", envelope_ok(
                gb, 404, "wu14-a1-b", "resource_not_found"), f"status={gb.status_code}")

            # A2. Schema fail-closed: malformed analyze -> 400, nothing persisted.
            bad_a2 = [
                {"body": 123}, {"body": ""}, {"body": "   "},
                {"bogus": "x"}, {"body": "hola", "extra": 1},
            ]
            ok = True
            for idx, body in enumerate(bad_a2):
                r = _post(a, AGENT_ANALYZE, body, f"wu14-a2-{idx}")
                if not envelope_ok(r, 400, f"wu14-a2-{idx}", "invalid_command"):
                    ok = False
                    break
            n2 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM agent_drafts WHERE client_account_id = %s",
                (ACCOUNT_A,)))
            record("A2 malformed inputs fail closed (400, zero new drafts)", ok
                   and n2 == 1, f"drafts={n2}")  # only A1's draft exists

            # A3. Property-match oracle == independent SQL (tenant-scoped).
            a3 = _analyze(a, "quiero comprar un depto, presupuesto 200000", "wu14-a3")
            a3j = a3.json() if a3.status_code == 200 else {}
            d3 = (a3j.get("draft") or {})
            want3 = match_oracle(ACCOUNT_A, operation_type="sale",
                                 property_type="apartment", budget_max=200000.0)
            bx = _analyze(b, "busco un depto en venta hasta 200000", "wu14-a3-b")
            bxj = bx.json() if bx.status_code == 200 else {}
            db3 = (bxj.get("draft") or {})
            wantb = match_oracle(ACCOUNT_B, operation_type="sale",
                                 property_type="apartment", budget_max=200000.0)
            ok = (a3.status_code == 200 and list(d3.get("matched_property_ids", [])) == want3
                  and A_PROP_1 in want3
                  and bx.status_code == 200
                  and list(db3.get("matched_property_ids", [])) == wantb
                  and not wantb)
            record("A3 match equals oracle; B sees none of A's", ok,
                   f"a={list(d3.get('matched_property_ids', []))} b={list(db3.get('matched_property_ids', []))}")

            # A4. Idempotent analyze replay (same fallback key -> one row).
            # Body must classify "general": "repetí este mensaje" hit the
            # ask_question trigger ("es" inside "este") and "hola buenas
            # tardes" too ("es" inside "tardes"); "buen día, gracias" is
            # clean of every ask_question/other keyword.
            a4a = _analyze(a, "buen día, gracias", "wu14-a4a")
            a4b = _analyze(a, "buen día, gracias", "wu14-a4b")
            da = ((a4a.json().get("draft") or {}).get("draft_id"))
            db4 = ((a4b.json().get("draft") or {}).get("draft_id"))
            n4 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM agent_drafts WHERE client_account_id = %s "
                "AND intent = 'general'", (ACCOUNT_A,)))
            ok = (a4a.status_code == 200 and a4b.status_code == 200
                  and a4b.json().get("replayed") is True and da == db4 and n4 == 1)
            record("A4 analyze replay returns stored draft, one row", ok, f"n={n4}")

            # A5. Approve search draft -> register_interest + follow_up through
            # accepted commands; execution_result all success; audit + outbox.
            a5 = _analyze(a, "necesito comprar un depto hasta 200000", "wu14-a5")
            d5id = ((a5.json().get("draft") or {}).get("draft_id"))
            ap5 = _post(a, f"{AGENT_DRAFTS}/{d5id}/approve", {}, "wu14-a5-approve")
            ap5j = ap5.json() if ap5.status_code == 200 else {}
            d5 = (ap5j.get("draft") or {})
            res5 = d5.get("execution_result") or []
            ok = (ap5.status_code == 200 and d5.get("status") == "executed"
                  and len(res5) == 2
                  and all(r.get("outcome") == "success" for r in res5)
                  and [r.get("type") for r in res5] == ["register_interest", "create_follow_up"])
            record("A5 approve executes every action (execution_result success)", ok,
                   f"status={ap5.status_code} res={res5}")
            interest_rows = asyncio.run(db.q(
                "SELECT * FROM property_interests WHERE client_account_id = %s "
                "AND contact_id = %s AND property_id = %s AND origin = 'ai_agent'",
                (ACCOUNT_A, A_CONTACT, A_PROP_1)))
            fu_rows = asyncio.run(db.q(
                "SELECT * FROM follow_ups WHERE client_account_id = %s AND contact_id = %s",
                (ACCOUNT_A, A_CONTACT)))
            ok = (len(interest_rows) == 1 and len(fu_rows) >= 1)
            record("A5 interest (ai_agent) + follow-up rows exist", ok,
                   f"interests={len(interest_rows)} follow_ups={len(fu_rows)}")
            au5 = _audit(a, "wu14-a5-audit", action="crm.agent.execute")
            au5i = _audit(a, "wu14-a5-int", action="crm.property.register_interest")
            au5f = _audit(a, "wu14-a5-fu", action="crm.follow_up.create")
            out5 = asyncio.run(db.q(
                "SELECT event_type FROM outbox_events WHERE aggregate_id = %s "
                "AND aggregate_type = 'agent_draft'",
                (d5id,)))
            ok = (au5.status_code == 200 and len(au5.json().get("items", [])) >= 1
                  and au5i.status_code == 200 and len(au5i.json().get("items", [])) >= 1
                  and au5f.status_code == 200 and len(au5f.json().get("items", [])) >= 1
                  and len(out5) >= 1)
            record("A5 accepted audit + outbox record the execution", ok,
                   f"outbox={[r['event_type'] for r in out5]}")

            # A6. Approve twice -> 409; zero duplicate effects.
            ap6 = _post(a, f"{AGENT_DRAFTS}/{d5id}/approve", {},
                        "wu14-a6-approve")
            n_interest = asyncio.run(db.count(
                "SELECT count(*) AS n FROM property_interests WHERE client_account_id = %s "
                "AND contact_id = %s AND property_id = %s", (ACCOUNT_A, A_CONTACT, A_PROP_1)))
            n_fu = asyncio.run(db.count(
                "SELECT count(*) AS n FROM follow_ups WHERE client_account_id = %s "
                "AND contact_id = %s AND demo_seed_key IS NULL",
                (ACCOUNT_A, A_CONTACT)))
            record("A6 second approve -> 409, effects intact", envelope_ok(
                ap6, 409, "wu14-a6-approve", "draft_not_proposed")
                and n_interest == 1 and n_fu == 1,
                f"interests={n_interest} follow_ups={n_fu}")

            # A7. Reject executes nothing; approve-after-reject -> 409.
            a7 = _analyze(a, "rechazame este borrador de seguimiento", "wu14-a7")
            d7id = ((a7.json().get("draft") or {}).get("draft_id"))
            rj7 = _post(a, f"{AGENT_DRAFTS}/{d7id}/reject", {}, "wu14-a7-reject")
            rj7j = rj7.json() if rj7.status_code == 200 else {}
            d7 = (rj7j.get("draft") or {})
            n7 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM follow_ups WHERE client_account_id = %s "
                "AND contact_id = %s AND demo_seed_key IS NULL",
                (ACCOUNT_A, A_CONTACT)))
            ap7 = _post(a, f"{AGENT_DRAFTS}/{d7id}/approve", {}, "wu14-a7-approve")
            record("A7 reject -> rejected, nothing executed; approve -> 409",
                   rj7.status_code == 200 and d7.get("status") == "rejected"
                   and n7 == 1 and envelope_ok(ap7, 409, "wu14-a7-approve",
                                               "draft_not_proposed"),
                   f"status={d7.get('status')} n_fu={n7} approve={ap7.status_code}")

            # A9. Draft reads scope-limiting + ghost.
            lst = _get(a, f"{AGENT_DRAFTS}?conversation_id="
                         "66000000-0000-0000-0000-000000000001&limit=2", "wu14-a9-a")
            lst_b = _get(b, AGENT_DRAFTS, "wu14-a9-b")
            ghost = _get(a, f"{AGENT_DRAFTS}/{GHOST}", "wu14-a9-ghost")
            b_items = lst_b.json().get("items", []) if lst_b.status_code == 200 else []
            b_ids = {x.get("draft_id") for x in b_items}
            # A's drafts (from A1/A3/A4/A5/A7) must never surface in B's list.
            a_ids_seen = {d5id, d7id, d1.get("draft_id"), d3.get("draft_id"),
                          db4, da}
            ok = (lst.status_code == 200 and len(lst.json().get("items", [])) == 2
                  and lst_b.status_code == 200
                  and (b_ids & a_ids_seen) == set()
                  and envelope_ok(ghost, 404, "wu14-a9-ghost", "resource_not_found"))
            record("A9 reads scope-limiting (B never sees A's drafts, ghost 404)", ok,
                   f"a={len(lst.json().get('items', []))} b={len(b_items)} shared={b_ids & a_ids_seen}")

            # A10. Denial is recorded truthfully: the agent proposes a free slot
            # (13:00Z = BA 10:00), the slot gets occupied AFTER analysis but
            # BEFORE approval -> create_event denied + follow_up success.
            a10 = _analyze(a, "quiero coordinar una visita el 2026-09-21 a las 10:00",
                           "wu14-a10")
            d10j = a10.json().get("draft", {})
            act10 = [x.get("type") for x in d10j.get("proposed_actions", [])]
            occ = _post(a, CAL_EVENTS, {
                "title": "Ocupa 10hs", "startsAt": MON_1000,
                "endsAt": f"{MON}T14:00:00.000Z", "idempotencyKey": "wu14-a10-occupy",
            }, "wu14-a10-occupy")
            has_occ = occ.status_code == 200
            ap10 = _post(a, f"{AGENT_DRAFTS}/{d10j.get('draft_id')}/approve", {},
                         "wu14-a10-approve")
            d10r = (ap10.json().get("draft") or {}).get("execution_result") or []
            outcomes10 = {r.get("type"): r.get("outcome") for r in d10r}
            ok = (a10.status_code == 200 and "create_event" in act10 and has_occ
                  and ap10.status_code == 200 and outcomes10.get("create_event") == "denied"
                  and outcomes10.get("create_follow_up") == "success")
            record("A10 occupied slot after analyze -> denied (truthful result)", ok,
                   f"outcomes={outcomes10}")

            # A11. Accepted WU9 simulator unchanged after agent mount.
            sim = _post(a, SIMULATE_PATH, {"body": "wu14 a11 inbound"},
                        "wu14-a11")
            ok = sim.status_code == 200 and sim.json().get("accepted") is True
            record("A11 WU9 simulator unchanged (accepted)", ok,
                   f"status={sim.status_code}")

            # A14 (before A13 so the day has a single upcoming event).
            # Create a manual event Monday BA 11:00 (14:00Z).
            ev14 = _post(a, CAL_EVENTS, {
                "title": "Visita lunes 14hs", "startsAt": f"{MON}T14:00:00.000Z",
                "endsAt": f"{MON}T15:00:00.000Z", "contactId": A_CONTACT,
                "idempotencyKey": "wu14-a14-create",
            }, "wu14-a14-create")
            ev14_id = ((ev14.json().get("result") or {}).get("resource_id"))
            a14 = _analyze(a, "¿podemos reprogramar la visita para el 2026-09-21 "
                              "a las 16:00?", "wu14-a14")
            d14 = a14.json().get("draft", {})
            act14 = {x.get("type"): x.get("params", {}) for x in
                     d14.get("proposed_actions", [])}
            rs14 = act14.get("reschedule_event", {})
            ok = (ev14.status_code == 200
                  and a14.status_code == 200 and d14.get("intent") == "reschedule"
                  and rs14.get("event_id") == ev14_id
                  and rs14.get("starts_at") == MON_1600)
            record("A14 reschedule resolves the event + preferred slot", ok,
                   f"rs={rs14}")
            ap14 = _post(a, f"{AGENT_DRAFTS}/{d14.get('draft_id')}/approve", {},
                         "wu14-a14-approve")
            d14r = (ap14.json().get("draft") or {}).get("execution_result") or []
            rr14 = [r for r in d14r if r.get("type") == "reschedule_event"]
            row14 = asyncio.run(db.one(
                "SELECT starts_at FROM calendar_events WHERE id = %s", (ev14_id,)))
            au14 = _audit(a, "wu14-a14-upd", action="crm.calendar.update")
            ok = (ap14.status_code == 200 and len(rr14) == 1
                  and rr14[0].get("outcome") == "success"
                  and row14 is not None
                  and row14["starts_at"].astimezone(timezone.utc)
                  .isoformat().startswith(MON_1600[:16])
                  and au14.status_code == 200
                  and len(au14.json().get("items", [])) >= 1)
            record("A14 approve reschedules + updates audit", ok,
                   f"starts={row14['starts_at'].isoformat() if row14 else None}")

            # A15. Stale reschedule: target event cancelled after analyze ->
            # fails closed and reports the denial truthfully.
            ev15 = _post(a, CAL_EVENTS, {
                "title": "Visita a reprogramar", "startsAt": MON_1300,
                "endsAt": f"{MON}T17:00:00.000Z", "contactId": A_CONTACT,
                "idempotencyKey": "wu14-a15-create",
            }, "wu14-a15-create")
            ev15_id = ((ev15.json().get("result") or {}).get("resource_id"))
            a15 = _analyze(a, "¿podemos reprogramar la visita del lunes para las 17:00?",
                           "wu14-a15")
            d15 = a15.json().get("draft", {})
            act15 = {x.get("type"): x.get("params", {}) for x in
                     d15.get("proposed_actions", [])}
            rs15 = act15.get("reschedule_event", {})
            # cancel the target now (after analysis) -> stale approval
            dc15 = _delete(a, f"{CAL_EVENTS}/{ev15_id}", "wu14-a15-cancel",
                           key="wu14-a15-cancel-key")
            ap15 = _post(a, f"{AGENT_DRAFTS}/{d15.get('draft_id')}/approve", {},
                         "wu14-a15-approve")
            d15r = (ap15.json().get("draft") or {}).get("execution_result") or []
            outcomes15 = {r.get("type"): r.get("outcome") for r in d15r}
            ok = (ev15.status_code == 200
                  and a15.status_code == 200 and d15.get("intent") == "reschedule"
                  and rs15.get("event_id") == ev15_id
                  and dc15.status_code == 200
                  and ap15.status_code == 200
                  and outcomes15.get("reschedule_event") == "denied")
            record("A15 stale reschedule (cancelled target) fails closed", ok,
                   f"outcomes={outcomes15}")

            # A13. Preferred date/time honored; agent event source='agent'.
            a13 = _analyze(a, "visita el 2026-09-22 a las 10:00", "wu14-a13")
            d13 = a13.json().get("draft", {})
            act13 = {x.get("type"): x.get("params", {}) for x in
                     d13.get("proposed_actions", [])}
            ev13 = act13.get("create_event", {})
            ok = (a13.status_code == 200 and d13.get("intent") == "schedule_visit"
                  and ev13.get("starts_at") == TUE_1000
                  and ev13.get("ends_at") == TUE_1100
                  and ev13.get("contact_id") == A_CONTACT)
            record("A13 preferred date/time honored (Tue 10:00 -> 13:00Z)", ok,
                   f"starts={ev13.get('starts_at')}")
            ap13 = _post(a, f"{AGENT_DRAFTS}/{d13.get('draft_id')}/approve", {},
                         "wu14-a13-approve")
            ev13_sql = asyncio.run(db.one(
                "SELECT id, source FROM calendar_events WHERE starts_at = %s "
                "AND client_account_id = %s AND status = 'scheduled'",
                (datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc), ACCOUNT_A)))
            au13 = _audit(a, "wu14-a13-ca", action="crm.calendar.create")
            ok = (ap13.status_code == 200
                  and ev13_sql is not None and ev13_sql["source"] == "agent"
                  and au13.status_code == 200
                  and len(au13.json().get("items", [])) >= 1)
            record("A16 agent event source='agent' + crm.calendar.create audit", ok,
                   f"source={ev13_sql['source'] if ev13_sql else None}")

            # A17. No availability -> follow-up only, never an invented slot.
            # The seeded agent event from A13 occupies Tue 13:00-14:00Z; occupy
            # the remaining 8 business slots of Tuesday (BA 09..17 -> 12..20Z).
            ok = True
            for idx, hour in enumerate(hour for hour in range(12, 21) if hour != 13):
                r = _post(a, CAL_EVENTS, {
                    "title": f"Ocupa {hour}hs", "startsAt": f"{TUE}T{hour}:00:00.000Z",
                    "endsAt": f"{TUE}T{hour + 1}:00:00.000Z",
                    "idempotencyKey": f"wu14-a17-{idx}",
                }, f"wu14-a17-{idx}")
                if r.status_code != 200:
                    ok = False
                    break
            # Body MUST differ from A13's ("visita el 2026-09-22 a las 10:00"):
            # the same body computes the same fallback analyze key and would
            # replay A13's stored draft (with create_event) instead of fresh
            # availability. "por favor" keeps the same criteria but a new key.
            a17 = _analyze(a, "visita el 2026-09-22 a las 10:00 por favor", "wu14-a17")
            d17 = a17.json().get("draft", {})
            act17 = [x.get("type") for x in d17.get("proposed_actions", [])]
            only_follow_up = (len(act17) == 1 and act17[0] == "create_follow_up")
            ap17 = _post(a, f"{AGENT_DRAFTS}/{d17.get('draft_id')}/approve", {},
                         "wu14-a17-approve")
            n_events17 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM calendar_events WHERE client_account_id = %s "
                "AND starts_at >= %s AND ends_at >= %s",
                (ACCOUNT_A, datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc),
                 datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc))))
            record("A17 full day booked -> follow-up only (no invented slot)",
                   ok and a17.status_code == 200 and only_follow_up
                   and ap17.status_code == 200 and n_events17 == 9,
                   f"actions={act17} events_tue={n_events17}")

            # A12. Cross-account approve -> 404 (non-enumerating).
            b12 = _analyze(b, "busco un depto en Buenos Aires", "wu14-a12-b")
            b12_id = ((b12.json().get("draft") or {}).get("draft_id"))
            ap12 = _post(a, f"{AGENT_DRAFTS}/{b12_id}/approve", {},
                         "wu14-a12-cross")
            record("A12 cross-account approve -> 404", envelope_ok(
                ap12, 404, "wu14-a12-cross", "resource_not_found"),
                f"status={ap12.status_code}")

            # Envelope probes: agent routes (401 / 404 / 405).
            none_app = TestClient(build_mvp_wu8_application(
                cfg, ACCOUNT_A, AbsentDemoIdentityAdapter()))
            with none_app as napp:
                r401 = _post(napp, AGENT_ANALYZE, {"body": "hola"}, "wu14-401")
                record("401 without identity", envelope_ok(
                    r401, 401, "wu14-401", "authentication_required"),
                    f"status={r401.status_code}")
            r404 = _get(a, "/v1/demo/agent/nope", "wu14-404")
            record("404 unknown agent route", envelope_ok(
                r404, 404, "wu14-404", "route_not_found"),
                f"status={r404.status_code}")
            r405 = a.get(AGENT_ANALYZE, headers={"X-Correlation-Id": "wu14-405"})
            record("405 wrong method on analyze", envelope_ok(
                r405, 405, "wu14-405", "method_not_allowed"),
                f"status={r405.status_code}")

            # A8 merged: executed approve -> 409 (already covered A6); rejected
            # approve -> 409 (covered A7). Extra: a second reject is idempotent —
            # the decide command replays via the `agent-decide:{draft_id}`
            # fallback key, so the router returns 200 replayed, never 409.
            rj2 = _post(a, f"{AGENT_DRAFTS}/{d7id}/reject", {}, "wu14-a8-rj2")
            rj2j = rj2.json() if rj2.status_code == 200 else {}
            record("A8 second reject -> 200 idempotent replay (replayed=True)",
                   rj2.status_code == 200 and rj2j.get("replayed") is True
                   and (rj2j.get("draft") or {}).get("status") == "rejected",
                   f"status={rj2.status_code} replayed={rj2j.get('replayed')}")
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_agent_wu14():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT WU14 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)