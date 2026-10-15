"""Agent autonomy (change: agent-autonomy, WU15) real-PostgreSQL harness.

Runs against production_runtime_mvp_wu15 (disposable: runner 001-009 +
mvp_seed.sql + mvp_seed_005.sql + mvp_seed_008.sql + mvp_seed_009.sql) with the StubModelProvider
(no network, no API key, no langchain import). The 008 seed flips the two demo
conversations to `auto`; setup re-pins `auto` explicitly via the dev-only
state endpoint (earlier WU14 runs may have left `manual` behind).

Scenarios map to the design's Testing Strategy B1-B10:
B1 FAQ zero-click (executed/agent_policy/sentinel, outbound row, audit+outbox)
B2 live-catalog price (edit changes answer), last-interest fallback,
unavailable-target escalate
B3 search + interest + follow-up + transition_lead via execute_transition
B4 handoff explicit + complaint (pause + flag + follow-up, silence, resume)
B5 low-confidence / KB no-match stays proposed, KB unmutated, cross-account
B6 auto booking zero-click + manual behaves as WU14 (slot-race denial is
    covered by the shared dispatcher + WU14 A10, not reproducible through the
    single-saga auto HTTP path — see the evidence file)
B7 persisted policy_decision matches the Python oracle across the corpus
B8 forged account / foreign ids non-enumerating
B9 stub runs B1-B8 offline (langchain_openai never imported)
B10 auto lifecycle monotonic (approve on executed -> 409, finalize from auto
    legal — the latter proven by every auto execution reaching `executed`)

Drafts/events/decisions are created EXCLUSIVELY by RUNNING the accepted
command surfaces (analyze/auto/approve/state/resume) — never by hand-inserting
rows. The one catalog fixture (a Palermo apartment, account A) is cloned from
the seeded PROP1 row in setup so the B3 transition scenario has a resolvable
city+budget target; preclean removes it (demo_seed_key NULL) every run.

Idempotency note (WU14 A17 lesson): analyze fallback keys hash
account|conversation|body, so every scenario needing fresh analysis uses a
DISTINCT body — no body is ever reused across scenarios.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.application.agent_policy import PolicySignals, evaluate as evaluate_policy
from api.composition_wu8_mvp import build_mvp_wu8_application
from api.domain.calendar_event import BUSINESS_TIMEZONE
from api.identity.demo_identity import AbsentDemoIdentityAdapter, DemoIdentityAdapter
from api.routes.simulator_wu9 import SIMULATE_PATH

ROOT = Path(__file__).resolve().parent.parent
SEED_FILES = [ROOT / "infra" / "migrations" / "validation" / "mvp_seed.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_005.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_008.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_009.sql"]

ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
A_CONTACT = "61000000-0000-0000-0000-000000000001"
A_LEAD = "65000000-0000-0000-0000-000000000001"
A_CONV = "66000000-0000-0000-0000-000000000001"
A_PROP_1 = "62000000-0000-0000-0000-000000000001"
PALERMO_PROP = "62000000-0000-0000-0000-000000000009"
CALIFICADO_STAGE = "64000000-0000-0000-0000-000000000003"
GHOST = "0000dead-0000-4000-8000-000000000000"

SENTINEL = str(uuid5(NAMESPACE_URL, "agent-policy"))

AGENT_ANALYZE = "/v1/demo/agent/analyze"
AGENT_DRAFTS = "/v1/demo/agent/drafts"
AGENT_STATE = "/v1/demo/agent/state"
AGENT_RESUME = "/v1/demo/agent/handoff/resume"
INBOX_MESSAGES = f"/v1/demo/inbox/{A_CONV}/messages"


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
    """Deterministic baseline: mirrors test_agent_wu14 preclean (008 tables and
    Calificado stages kept via the same DROP-order care for RESTRICT FKs), then
    clones the Palermo catalog fixture from the seeded PROP1 row."""
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
        "DELETE FROM pipeline_stages WHERE demo_seed_key IN "
        "('demo-a-stage-calificado', 'demo-b-stage-calificado')",
        "DELETE FROM knowledge_base_articles",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records",
        "DELETE FROM outbox_events",
    ):
        await db.q(stmt)
    await run_seed(db)
    # Palermo fixture for B3/B2b: same shape as PROP1 (apartment/sale/
    # available) but city Palermo at 195000, so operation-only price searches
    # still top-match PROP1 while city+budget searches resolve here.
    await db.q(
        "INSERT INTO properties (id, client_account_id, demo_seed_key, title, "
        "operation_type, location, bedrooms, price, currency, availability, "
        "reference_code, property_type, address, neighborhood, city, "
        "expenses_amount, accepts_swap, covered_area_m2, total_area_m2, rooms, "
        "bathrooms, parking_spaces, property_age_years, construction_status, "
        "capture_source, responsible_agent_id, status, price_amount, price_currency) "
        "SELECT %s, client_account_id, NULL, 'Depto en Palermo', operation_type, "
        "location, bedrooms, 195000, currency, availability, 'WU15-PALERMO', "
        "property_type, address, 'Palermo', 'Palermo', expenses_amount, accepts_swap, "
        "covered_area_m2, total_area_m2, rooms, bathrooms, parking_spaces, "
        "property_age_years, construction_status, capture_source, responsible_agent_id, "
        "status, 195000, price_currency FROM properties WHERE id = %s",
        (PALERMO_PROP, A_PROP_1))


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


def _analyze(client, body, correlation, idempotency_key=None):
    payload = {"body": body}
    if idempotency_key:
        payload["idempotencyKey"] = idempotency_key
    return _post(client, AGENT_ANALYZE, payload, correlation)


def _set_mode(client, mode, correlation):
    return _post(client, AGENT_STATE, {"mode": mode}, correlation)


def _resume(client, correlation):
    return _post(client, AGENT_RESUME, {}, correlation)


def _draft_row(db: Sql, draft_id: str):
    return asyncio.run(db.one(
        "SELECT status, policy_decision, execution_mode, decided_by, "
        "auto_decision_actor FROM agent_drafts WHERE id = %s", (draft_id,)))


def oracle_verdict(draft: dict) -> str:
    """Independent recomputation of the policy verdict from persisted draft
    data (intent + actions + match/slot evidence) via the pure policy."""
    actions = draft.get("proposed_actions", [])
    types = [a.get("type") for a in actions]
    params = {a.get("type"): (a.get("params") or {}) for a in actions}
    kb = params.get("send_reply", {}).get("source") == "kb_article"
    price = params.get("send_reply", {}).get("source") == "price_catalog"
    transition_stage = (params.get("transition_lead") or {}).get("to_stage_id")
    handoff = draft.get("intent") == "handoff"
    complaint = (params.get("handoff_to_human") or {}).get("reason") == "complaint"
    signals = PolicySignals(
        intent=draft.get("intent", "general"),
        kb_match=kb, price_resolved=price,
        matched_count=len(draft.get("matched_property_ids", [])),
        slot_found=("create_event" in types or "reschedule_event" in types),
        handoff_requested=handoff, complaint=complaint,
        transition_stage_id=transition_stage,
    )
    return evaluate_policy(signals, types).verdict


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    # Relative calendar dates (date-aging fix): the booking window is
    # [datetime.now(timezone.utc), now + 7d], so hardcoded 2026-09-* dates fall
    # outside it once the system clock moves past them. Compute the next Monday
    # and the next Tuesday (strictly after Monday) inside the window, then build
    # the analyze bodies and event lookups from them (Buenos Aires is UTC-3).
    _now = datetime.now(timezone.utc)
    _tz = ZoneInfo(BUSINESS_TIMEZONE)
    _today = _now.astimezone(_tz).date()

    def _next_day(predicate):
        for n in range(1, 8):
            candidate = _today + timedelta(days=n)
            if predicate(candidate.weekday()):
                return candidate
        raise AssertionError("no matching day within the 7-day window")

    _mon = _next_day(lambda w: w == 0)
    _tue = _next_day(lambda w: w == 1)
    if _tue <= _mon:
        _tue = _mon + timedelta(days=1)
    MON = _mon.isoformat()
    TUE = _tue.isoformat()

    cfg = load_database_configuration()
    assert cfg.get("dbname") == "production_runtime_mvp_wu15", (
        f"refusing to run on {cfg.get('dbname')}")
    assert not (sys.modules.get("langchain_openai")), "stub must stay import-free"
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))
        kb_n = asyncio.run(db.count(
            "SELECT count(*) AS n FROM knowledge_base_articles"))
        wu8_a = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter()))
        wu8_b = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_B, DemoIdentityAdapter()))

        with wu8_a as a, wu8_b as b:
            # Setup: conversations stay agent_mode='auto' per the 008 seed for
            # the AUTO scenarios (re-pin explicitly — WU14 runs leave manual).
            for _client, _corr in ((a, "wu15-pin-a"), (b, "wu15-pin-b")):
                _pin = _set_mode(_client, "auto", _corr)
                assert _pin.status_code == 200, (
                    f"auto pin failed: {_pin.status_code} {_pin.text[:200]}")
                assert _pin.json().get("agent_mode") == "auto"
            corpus: list[tuple[str, dict]] = []

            # B2a first: the only pre-existing interest points at a
            # capturing (unavailable) property -> escalate, never guess.
            seed_int = asyncio.run(db.one(
                "SELECT i.property_id, p.status FROM property_interests i "
                "JOIN properties p ON p.id = i.property_id "
                "WHERE i.client_account_id = %s AND i.contact_id = %s",
                (ACCOUNT_A, A_CONTACT)))
            b2a = _analyze(a, "¿cuánto cuesta, por favor?", "wu15-b2a")
            b2aj = b2a.json() if b2a.status_code == 200 else {}
            d2a = (b2aj.get("draft") or {})
            a2a = [x.get("type") for x in d2a.get("proposed_actions", [])]
            out2a = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND direction = 'outbound'",
                (ACCOUNT_A, A_CONV)))
            ok = (b2a.status_code == 200 and b2aj.get("auto_executed") is False
                  and d2a.get("status") == "proposed"
                  and d2a.get("intent") == "ask_question"
                  and d2a.get("policy_decision") == "escalate"
                  and a2a == ["create_follow_up"] and out2a == 0
                  and seed_int is not None
                  and seed_int["status"] != "available")
            record("B2a price with only an unavailable target escalates (silent)", ok,
                   f"policy={d2a.get('policy_decision')} actions={a2a} "
                   f"seed_target={seed_int['status'] if seed_int else None}")
            corpus.append(("B2a", d2a))

            # B1. FAQ zero-click on the seeded auto conversation.
            b1 = _analyze(a, "¿Qué incluyen las expensas del edificio?", "wu15-b1")
            b1j = b1.json() if b1.status_code == 200 else {}
            d1 = (b1j.get("draft") or {})
            t1 = [x.get("type") for x in d1.get("proposed_actions", [])]
            ok = (b1.status_code == 200 and b1j.get("accepted") is True
                  and b1j.get("replayed") is False and b1j.get("auto_executed") is True
                  and d1.get("status") == "executed"
                  and d1.get("intent") == "ask_question"
                  and d1.get("policy_decision") == "auto"
                  and d1.get("execution_mode") == "agent_policy"
                  and t1 == ["send_reply", "create_follow_up"]
                  and all((r.get("outcome") == "success")
                          for r in (d1.get("execution_result") or [])))
            record("B1 FAQ zero-click (executed/agent_policy, auto_executed)", ok,
                   f"policy={d1.get('policy_decision')} types={t1}")
            row1 = _draft_row(db, d1.get("draft_id"))
            ok = (row1 is not None and row1["status"] == "executed"
                  and row1["decided_by"] is None
                  and str(row1["auto_decision_actor"]) == SENTINEL)
            record("B1 decided_by NULL + sentinel actor (SQL)", ok,
                   f"actor={str(row1['auto_decision_actor'])[:8] if row1 else None}")
            corpus.append(("B1", d1))
            msg1 = asyncio.run(db.one(
                "SELECT direction, body FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND external_event_id = %s",
                (ACCOUNT_A, A_CONV, f"agent-draft:{d1.get('draft_id')}:0")))
            thread = _get(a, INBOX_MESSAGES, "wu15-b1-thread")
            items = thread.json().get("items", []) if thread.status_code == 200 else []
            reply_param = next((x.get("params", {}).get("reply_text") for x in
                                d1.get("proposed_actions", [])
                                if x.get("type") == "send_reply"), None)
            ok = (msg1 is not None and msg1["direction"] == "outbound"
                  and msg1["body"] == reply_param
                  and any(m.get("direction") == "outbound"
                          and m.get("body") == reply_param for m in items))
            record("B1 outbound row visible in the inbox thread", ok,
                   f"thread_items={len(items)}")
            ok = (reply_param is not None and d1.get("reply_text") is not None
                  and msg1 is not None and msg1["body"] != d1.get("reply_text"))
            record("B1 grounded reply only (model reply_text never sent)", ok,
                   f"sent={str(reply_param)[:40]}")
            au1 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id = %s "
                "AND action IN ('crm.agent.reply', 'crm.follow_up.create')",
                ("wu15-b1",)))
            out1 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM outbox_events WHERE correlation_id = %s",
                ("wu15-b1",)))
            ok = au1 == 2 and out1 >= 2
            record("B1 one audit + one outbox per action", ok,
                   f"audits={au1} outbox={out1}")
            # Cross-account KB isolation: B's answer comes from B's article.
            b1b = _analyze(b, "¿Qué incluyen las expensas del edificio?", "wu15-b1b")
            d1b = ((b1b.json().get("draft") or {}))
            art_a = next((x.get("params", {}).get("article_id") for x in
                          d1.get("proposed_actions", [])
                          if x.get("type") == "send_reply"), None)
            art_b = next((x.get("params", {}).get("article_id") for x in
                          d1b.get("proposed_actions", [])
                          if x.get("type") == "send_reply"), None)
            ok = (b1b.status_code == 200 and d1b.get("status") == "executed"
                  and art_a is not None and art_b is not None and art_a != art_b)
            record("B1 cross-account KB isolated (B answers from own article)", ok,
                   f"a={str(art_a)[:8]} b={str(art_b)[:8]}")
            corpus.append(("B1b", d1b))

            # B5. Low-confidence / KB no-match stays proposed, replies nothing.
            kb_before = asyncio.run(db.count(
                "SELECT count(*) AS n FROM knowledge_base_articles"))
            b5 = _analyze(a, "¿cómo es el trámite notarial?", "wu15-b5")
            b5j = b5.json() if b5.status_code == 200 else {}
            d5 = (b5j.get("draft") or {})
            out5 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND direction = 'outbound'",
                (ACCOUNT_A, A_CONV)))
            kb_after = asyncio.run(db.count(
                "SELECT count(*) AS n FROM knowledge_base_articles"))
            ok = (b5.status_code == 200 and b5j.get("auto_executed") is False
                  and d5.get("status") == "proposed"
                  and d5.get("policy_decision") == "escalate"
                  and [x.get("type") for x in d5.get("proposed_actions", [])]
                  == ["create_follow_up"]
                  and out5 == 1 and kb_after == kb_before == kb_n
                  and d5.get("reply_text") is not None)
            record("B5 no-match stays proposed, silent, KB unmutated", ok,
                   f"outbound={out5} kb={kb_after}")
            corpus.append(("B5", d5))
            b5b = _analyze(b, "¿cómo es el trámite notarial?", "wu15-b5b")
            d5b = (b5b.json().get("draft") or {})
            ok = (b5b.status_code == 200 and d5b.get("status") == "proposed"
                  and d5b.get("policy_decision") == "escalate")
            record("B5 cross-account KB invisible (B proposed too)", ok,
                   f"policy={d5b.get('policy_decision')}")
            corpus.append(("B5b", d5b))

            # B3. Search + interest + follow-up + transition_lead.
            b3 = _analyze(a, "busco un depto en Palermo hasta 200000", "wu15-b3")
            b3j = b3.json() if b3.status_code == 200 else {}
            d3 = (b3j.get("draft") or {})
            t3 = [x.get("type") for x in d3.get("proposed_actions", [])]
            res3 = {r.get("type"): r.get("outcome")
                    for r in (d3.get("execution_result") or [])}
            ok = (b3.status_code == 200 and b3j.get("auto_executed") is True
                  and d3.get("status") == "executed"
                  and d3.get("policy_decision") == "auto"
                  and t3 == ["register_interest", "transition_lead", "create_follow_up"]
                  and res3 == {"register_interest": "success",
                               "transition_lead": "success",
                               "create_follow_up": "success"})
            record("B3 search auto-executes interest+transition+follow-up", ok,
                   f"types={t3}")
            corpus.append(("B3", d3))
            want3 = [PALERMO_PROP]
            got3 = list(d3.get("matched_property_ids", []))
            lead3 = asyncio.run(db.one(
                "SELECT stage_id FROM leads WHERE id = %s", (A_LEAD,)))
            au3 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id = %s "
                "AND action IN ('crm.property.register_interest', 'crm.lead.transition', "
                "'crm.follow_up.create')", ("wu15-b3",)))
            intr3 = asyncio.run(db.q(
                "SELECT property_id FROM property_interests WHERE client_account_id = %s "
                "AND contact_id = %s AND property_id = %s AND origin = 'ai_agent'",
                (ACCOUNT_A, A_CONTACT, PALERMO_PROP)))
            ok = (got3 == want3
                  and lead3 is not None
                  and str(lead3["stage_id"]) == CALIFICADO_STAGE
                  and au3 == 3 and len(intr3) == 1)
            record("B3 oracle match + lead in Calificado + transition audit", ok,
                   f"matched={got3} stage={str(lead3['stage_id'])[:8] if lead3 else None}")
            n_fu3 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM follow_ups WHERE client_account_id = %s "
                "AND contact_id = %s AND demo_seed_key IS NULL", (ACCOUNT_A, A_CONTACT)))
            b3r = _analyze(a, "busco un depto en Palermo hasta 200000", "wu15-b3r")
            b3rj = b3r.json() if b3r.status_code == 200 else {}
            d3r = (b3rj.get("draft") or {})
            n_fu3b = asyncio.run(db.count(
                "SELECT count(*) AS n FROM follow_ups WHERE client_account_id = %s "
                "AND contact_id = %s AND demo_seed_key IS NULL", (ACCOUNT_A, A_CONTACT)))
            n_int3 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM property_interests WHERE client_account_id = %s "
                "AND contact_id = %s AND property_id = %s", (ACCOUNT_A, A_CONTACT, PALERMO_PROP)))
            ok = (b3rj.get("replayed") is True
                  and d3r.get("draft_id") == d3.get("draft_id")
                  and n_fu3b == n_fu3 and n_int3 == 1)
            record("B3 replayed analyze -> zero duplicate effects", ok,
                   f"replayed={b3rj.get('replayed')} interests={n_int3}")

            # B2b: last-interest fallback resolves the Palermo property live.
            b2b = _analyze(a, "¿cuánto vale hoy, por favor?", "wu15-b2b")
            b2bj = b2b.json() if b2b.status_code == 200 else {}
            d2b = (b2bj.get("draft") or {})
            p2b = next((x.get("params", {}) for x in d2b.get("proposed_actions", [])
                        if x.get("type") == "send_reply"), {})
            ok = (b2b.status_code == 200 and b2bj.get("auto_executed") is True
                  and d2b.get("policy_decision") == "auto"
                  and p2b.get("source") == "price_catalog"
                  and p2b.get("property_id") == PALERMO_PROP
                  and "195000" in str(p2b.get("reply_text", "")))
            record("B2b last-interest fallback resolves live catalog", ok,
                   f"prop={str(p2b.get('property_id'))[:8]}")
            corpus.append(("B2b", d2b))
            # B2c: editing the property price changes the next answer.
            b2c1 = _analyze(a, "¿cuánto sale ahora?", "wu15-b2c1")
            d2c1 = (b2c1.json().get("draft") or {})
            p2c1 = next((x.get("params", {}) for x in d2c1.get("proposed_actions", [])
                         if x.get("type") == "send_reply"), {})
            before = str(p2c1.get("reply_text", ""))
            ok1 = ("185000" in before and p2c1.get("property_id") == A_PROP_1)
            asyncio.run(db.q(
                "UPDATE properties SET price_amount = 190000, price = 190000, "
                "updated_at = now() WHERE id = %s", (A_PROP_1,)))
            b2c2 = _analyze(a, "¿cuánto sale hoy?", "wu15-b2c2")
            d2c2 = (b2c2.json().get("draft") or {})
            p2c2 = next((x.get("params", {}) for x in d2c2.get("proposed_actions", [])
                         if x.get("type") == "send_reply"), {})
            after = str(p2c2.get("reply_text", ""))
            ok = (ok1 and b2c2.status_code == 200 and "190000" in after
                  and "185000" not in after and before != after)
            record("B2c edited catalog price changes the next answer", ok,
                   f"before={before[:40]} after={after[:40]}")
            corpus.append(("B2c", d2c2))

            # B4. Explicit handoff: pause + flag + follow-up, never a reply.
            fu4_before = asyncio.run(db.count(
                "SELECT count(*) AS n FROM follow_ups WHERE client_account_id = %s "
                "AND contact_id = %s AND demo_seed_key IS NULL", (ACCOUNT_A, A_CONTACT)))
            b4 = _analyze(a, "quiero hablar con un asesor humano, por favor", "wu15-b4")
            b4j = b4.json() if b4.status_code == 200 else {}
            d4 = (b4j.get("draft") or {})
            t4 = [x.get("type") for x in d4.get("proposed_actions", [])]
            st4 = _get(a, AGENT_STATE, "wu15-b4-state").json()
            msg4 = asyncio.run(db.q(
                "SELECT id FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND external_event_id LIKE %s",
                (ACCOUNT_A, A_CONV, f"agent-draft:{d4.get('draft_id')}:%")))
            fu4_after = asyncio.run(db.count(
                "SELECT count(*) AS n FROM follow_ups WHERE client_account_id = %s "
                "AND contact_id = %s AND demo_seed_key IS NULL", (ACCOUNT_A, A_CONTACT)))
            row4 = _draft_row(db, d4.get("draft_id"))
            ok = (b4.status_code == 200 and b4j.get("auto_executed") is True
                  and d4.get("intent") == "handoff"
                  and d4.get("policy_decision") == "escalate"
                  and d4.get("status") == "executed"
                  and d4.get("execution_mode") == "agent_policy"
                  and t4 == ["handoff_to_human", "create_follow_up"]
                  and row4 is not None and row4["decided_by"] is None
                  and str(row4["auto_decision_actor"]) == SENTINEL
                  and st4.get("agent_mode") == "paused" and st4.get("handoff_open") is True
                  and msg4 == [] and fu4_after == fu4_before + 1)
            record("B4 explicit handoff pauses (mechanics only, silent)", ok,
                   f"mode={st4.get('agent_mode')} types={t4}")
            corpus.append(("B4", d4))
            sw4 = _set_mode(a, "manual", "wu15-b4-toggle")
            record("B4 state toggle while paused -> 409",
                   envelope_ok(sw4, 409, "wu15-b4-toggle", "agent_handoff_open"),
                   f"status={sw4.status_code}")
            # Next inbound on a paused conversation gets no reply.
            out4_before = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND direction = 'outbound'",
                (ACCOUNT_A, A_CONV)))
            b4p = _analyze(a, "buen día, gracias por favor", "wu15-b4p")
            b4pj = b4p.json() if b4p.status_code == 200 else {}
            d4p = (b4pj.get("draft") or {})
            out4_after = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND direction = 'outbound'",
                (ACCOUNT_A, A_CONV)))
            ok = (b4p.status_code == 200 and d4p.get("status") == "proposed"
                  and d4p.get("policy_decision") == "escalate"
                  and b4pj.get("auto_executed") is False
                  and out4_after == out4_before)
            record("B4 paused inbound stays proposed, silent", ok,
                   f"outbound={out4_after}")
            corpus.append(("B4p", d4p))
            rs4 = _resume(a, "wu15-b4-resume")
            rs4j = rs4.json() if rs4.status_code == 200 else {}
            st4r = _get(a, AGENT_STATE, "wu15-b4-resumed").json()
            ok = (rs4.status_code == 200 and rs4j.get("agent_mode") == "auto"
                  and rs4j.get("handoff_open") is False
                  and st4r.get("agent_mode") == "auto")
            record("B4 resume restores auto", ok,
                   f"mode={st4r.get('agent_mode')}")
            # Complaint variant on account B: the dev resume endpoint keys
            # idempotency as agent-resume:{conversation_id}, so a second resume
            # of the SAME conversation would replay the first stored outcome
            # instead of re-applying (dev-only one-shot). B's conversation has
            # a fresh key, so the complaint cycle runs there.
            b_contact = asyncio.run(db.one(
                "SELECT contact_id FROM conversations WHERE id = %s",
                ("66000000-0000-0000-0000-000000000002",)))
            b4c = _analyze(b, "estoy decepcionado con el servicio, quiero hacer "
                              "un reclamo formal", "wu15-b4c")
            b4cj = b4c.json() if b4c.status_code == 200 else {}
            d4c = (b4cj.get("draft") or {})
            t4c = [x.get("type") for x in d4c.get("proposed_actions", [])]
            r4c = next((x.get("params", {}).get("reason") for x in
                        d4c.get("proposed_actions", [])
                        if x.get("type") == "handoff_to_human"), None)
            st4c = _get(b, AGENT_STATE, "wu15-b4c-state").json()
            rs4c = _resume(b, "wu15-b4c-resume")
            rs4cj = rs4c.json() if rs4c.status_code == 200 else {}
            st4cr = _get(b, AGENT_STATE, "wu15-b4c-resumed").json()
            ok = (b4c.status_code == 200 and b4cj.get("auto_executed") is True
                  and d4c.get("intent") == "handoff" and r4c == "complaint"
                  and t4c == ["handoff_to_human", "create_follow_up"]
                  and d4c.get("status") == "executed"
                  and st4c.get("agent_mode") == "paused"
                  and st4c.get("handoff_open") is True
                  and rs4c.status_code == 200
                  and rs4cj.get("agent_mode") == "auto"
                  and st4cr.get("agent_mode") == "auto"
                  and b_contact is not None)
            record("B4 complaint on B re-pauses (reason complaint) + resume", ok,
                   f"reason={r4c} mode={st4c.get('agent_mode')}->{st4cr.get('agent_mode')}")
            corpus.append(("B4c", d4c))

            # B6. Auto booking zero-click, then manual behaves as WU14.
            b6 = _analyze(a, f"quiero coordinar una visita el {MON} "
                             "a las 10:00, por favor", "wu15-b6")
            b6j = b6.json() if b6.status_code == 200 else {}
            d6 = (b6j.get("draft") or {})
            res6 = {r.get("type"): r.get("outcome")
                    for r in (d6.get("execution_result") or [])}
            ev6 = asyncio.run(db.one(
                "SELECT source FROM calendar_events WHERE client_account_id = %s "
                "AND starts_at = %s AND status = 'scheduled'",
                (ACCOUNT_A, datetime(_mon.year, _mon.month, _mon.day, 13, 0,
                                     tzinfo=timezone.utc))))
            ok = (b6.status_code == 200 and b6j.get("auto_executed") is True
                  and d6.get("status") == "executed"
                  and d6.get("intent") == "schedule_visit"
                  and d6.get("policy_decision") == "auto"
                  and res6.get("create_event") == "success"
                  and res6.get("create_follow_up") == "success"
                  and ev6 is not None and ev6["source"] == "agent")
            record("B6 auto booking zero-click (event row, agent source)", ok,
                   f"outcomes={res6}")
            corpus.append(("B6", d6))
            pin6 = _set_mode(a, "manual", "wu15-b6-pin")
            assert pin6.status_code == 200
            b6m = _analyze(a, f"quiero coordinar una visita el {TUE} "
                               "a las 10:00, por favor", "wu15-b6m")
            b6mj = b6m.json() if b6m.status_code == 200 else {}
            d6m = (b6mj.get("draft") or {})
            t6m = [x.get("type") for x in d6m.get("proposed_actions", [])]
            ok_manual = (b6m.status_code == 200 and b6mj.get("auto_executed") is False
                         and d6m.get("status") == "proposed"
                         and d6m.get("policy_decision") == "auto"
                         and "create_event" in t6m)
            ap6m = _post(a, f"{AGENT_DRAFTS}/{d6m.get('draft_id')}/approve", {},
                         "wu15-b6m-approve")
            d6mr = (ap6m.json().get("draft") or {}).get("execution_result") or []
            out6m = {r.get("type"): r.get("outcome") for r in d6mr}
            ev6m = asyncio.run(db.one(
                "SELECT source FROM calendar_events WHERE client_account_id = %s "
                "AND starts_at = %s AND status = 'scheduled'",
                (ACCOUNT_A, datetime(_tue.year, _tue.month, _tue.day, 13, 0,
                                     tzinfo=timezone.utc))))
            ok = (ok_manual and ap6m.status_code == 200
                  and out6m.get("create_event") == "success"
                  and ev6m is not None and ev6m["source"] == "agent")
            record("B6 manual behaves as WU14 (proposed -> approve -> event)", ok,
                   f"outcomes={out6m}")
            corpus.append(("B6m", d6m))
            unpin6 = _set_mode(a, "auto", "wu15-b6-unpin")
            assert unpin6.status_code == 200

            # B7. Persisted policy_decision matches the Python oracle.
            bad7 = [(name, d.get("policy_decision"), oracle_verdict(d))
                    for name, d in corpus
                    if oracle_verdict(d) != d.get("policy_decision")]
            record("B7 policy_decision matches oracle across corpus", bad7 == [],
                   f"mismatches={bad7} corpus={len(corpus)}")

            # B8. Forged account / foreign ids non-enumerating.
            gb8 = _get(b, f"{AGENT_DRAFTS}/{d1.get('draft_id')}", "wu15-b8-get")
            ab8 = _post(a, f"{AGENT_DRAFTS}/{(b1b.json().get('draft') or {}).get('draft_id')}"
                           f"/approve", {}, "wu15-b8-cross")
            ghost8 = _get(a, f"{AGENT_DRAFTS}/{GHOST}", "wu15-b8-ghost")
            ghost8a = _post(a, f"{AGENT_DRAFTS}/{GHOST}/approve", {},
                            "wu15-b8-ghost-approve")
            ok = (envelope_ok(gb8, 404, "wu15-b8-get", "resource_not_found")
                  and envelope_ok(ab8, 404, "wu15-b8-cross", "resource_not_found")
                  and envelope_ok(ghost8, 404, "wu15-b8-ghost", "resource_not_found")
                  and envelope_ok(ghost8a, 404, "wu15-b8-ghost-approve",
                                  "resource_not_found"))
            record("B8 forged/foreign ids non-enumerating (404)", ok,
                   f"get={gb8.status_code} cross={ab8.status_code}")

            # B9. The stub ran everything offline.
            models = {d.get("model_name") for _, d in corpus}
            ok = ("langchain_openai" not in sys.modules
                  and models == {"stub-keyword-v1"})
            record("B9 stub offline (no langchain import, stub provider)", ok,
                   f"models={models} langchain={('langchain_openai' in sys.modules)}")

            # B10. Monotonic lifecycle via the HTTP override surface.
            ap10a = _post(a, f"{AGENT_DRAFTS}/{d1.get('draft_id')}/approve", {},
                          "wu15-b10-executed")
            pin10 = _set_mode(a, "manual", "wu15-b10-pin")
            assert pin10.status_code == 200
            b10 = _analyze(a, "buen día, gracias de nuevo", "wu15-b10")
            d10 = (b10.json().get("draft") or {})
            ap10b = _post(a, f"{AGENT_DRAFTS}/{d10.get('draft_id')}/approve", {},
                          "wu15-b10-approve")
            ap10c = _post(a, f"{AGENT_DRAFTS}/{d10.get('draft_id')}/approve", {},
                          "wu15-b10-reapprove")
            unpin10 = _set_mode(a, "auto", "wu15-b10-unpin")
            ok = (envelope_ok(ap10a, 409, "wu15-b10-executed", "draft_not_proposed")
                  and b10.status_code == 200 and d10.get("status") == "proposed"
                  and ap10b.status_code == 200
                  and envelope_ok(ap10c, 409, "wu15-b10-reapprove",
                                  "draft_not_proposed")
                  and unpin10.status_code == 200)
            record("B10 lifecycle monotonic (executed/manual approve -> 409)", ok,
                   f"reapprove={ap10c.status_code}")
            corpus.append(("B10", d10))

            # Envelope probes on the new state/resume surface.
            none_app = TestClient(build_mvp_wu8_application(
                cfg, ACCOUNT_A, AbsentDemoIdentityAdapter()))
            with none_app as napp:
                r401s = _post(napp, AGENT_STATE, {"mode": "manual"}, "wu15-401-s")
                r401r = _post(napp, AGENT_RESUME, {}, "wu15-401-r")
                record("401 without identity (state+resume)", envelope_ok(
                    r401s, 401, "wu15-401-s", "authentication_required")
                    and envelope_ok(r401r, 401, "wu15-401-r",
                                    "authentication_required"),
                    f"state={r401s.status_code} resume={r401r.status_code}")
            r404 = _get(a, "/v1/demo/agent/nope", "wu15-404")
            r405 = a.delete(AGENT_STATE, headers={"X-Correlation-Id": "wu15-405"})
            r409 = _resume(a, "wu15-409")
            record("404 unknown agent route", envelope_ok(
                r404, 404, "wu15-404", "route_not_found"),
                f"status={r404.status_code}")
            record("405 wrong method on state", envelope_ok(
                r405, 405, "wu15-405", "method_not_allowed"),
                f"status={r405.status_code}")
            record("409 resume when none open", envelope_ok(
                r409, 409, "wu15-409", "agent_handoff_not_open"),
                f"status={r409.status_code}")

            # WU9 simulator unchanged after the autonomy mount.
            sim = _post(a, SIMULATE_PATH, {"body": "wu15 autonomy inbound"},
                        "wu15-sim")
            record("WU9 simulator unchanged (accepted)",
                   sim.status_code == 200 and sim.json().get("accepted") is True,
                   f"status={sim.status_code}")
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_agent_autonomy_wu15():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT AUTONOMY WU15 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
