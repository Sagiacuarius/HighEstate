"""MVP Commercial Demo WU10: full-loop PostgreSQL E2E harness (final unit).

Runs against production_runtime_mvp_wu10 (disposable database: runner 001-004
+ mvp_seed). Never touches earlier accepted bases.

The journey reuses the accepted harness units as building blocks rather than
re-implementing them: WU7/WU8 demo composition (selector-governed session +
scoped cache + seed/reset controls), WU9 simulator surface, and the WU5
/dev/inbound-message command route for the cross-account denial.

Covered: select A -> simulate -> message+lead+transition+follow-up+activity
visible in reads; fresh-client reload proves durability; switch B shows zero
A data (JSON-dump absence, not just counts); switch back preserves A effects;
idempotent replay + key-reuse conflict; cross-account inbound denial with
redacted audit and zero mutation on B; reset blocked by effects (409
demo_effects_pending) -> clear_effects -> seed baseline -> plain reset ->
re-seed convergence; 404/405 envelope discipline.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from api.adapters.postgres_core import load_database_configuration
from api.composition_wu5_mvp import INBOUND_COMMAND_ROUTE, build_mvp_wu5_application
from api.composition_wu8_mvp import (
    DEMO_PREFIX,
    RESET_ROUTE,
    SEED_ROUTE,
    SELECT_ROUTE,
    build_mvp_wu8_application,
)
from api.identity.demo_identity import DemoIdentityAdapter
from api.routes.simulator_wu9 import SIMULATE_PATH
from api.test_simulator import (
    A_CONVERSATION,
    ACCOUNT_A,
    ACCOUNT_B,
    CANONICAL_BODY,
    SEED_CONVERSATION_B,
    SEED_LEAD_A,
    SEED_STAGE_A,
    Sql,
    envelope_ok,
    preclean,
)

DASHBOARD_ROUTE = f"{DEMO_PREFIX}/dashboard"
INBOX_ROUTE = f"{DEMO_PREFIX}/inbox"
LEADS_ROUTE = f"{DEMO_PREFIX}/leads"
AGENDA_ROUTE = f"{DEMO_PREFIX}/agenda"
ACTIVITY_ROUTE = f"{DEMO_PREFIX}/activity"


def wu5_inbound_payload(event_id: str, conversation_id: str, account_id: str,
                        body: str) -> dict:
    message_id = str(uuid4())
    return {
        "eventId": event_id,
        "clientAccountId": account_id,
        "conversationId": conversation_id,
        "message": {
            "id": message_id, "conversationId": conversation_id,
            "clientAccountId": account_id, "direction": "inbound",
            "body": body, "receivedAt": "2026-09-05T09:00:00-03:00",
        },
    }


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))  # deterministic seed baseline for the journey

        app = build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter())
        with TestClient(app) as client:

            # E1. Select A explicitly; server-derived projection (not a claim).
            r = client.post(SELECT_ROUTE, json={"requestedAccountId": ACCOUNT_A},
                            headers={"X-Correlation-Id": "wu10-e1"})
            ok = (r.status_code == 200 and r.json().get("client_account_id") == ACCOUNT_A
                  and r.json().get("environment") == "dev-only-demo-selector")
            record("E1 select A: server-owned context projection", ok, f"{r.status_code}")

            # E2. Simulate inbound on A: durable effects proven with SQL oracles.
            r = client.post(SIMULATE_PATH, json={}, headers={"X-Correlation-Id": "wu10-e2"})
            body = r.json() if r.status_code == 200 else {}
            res = body.get("result", {})
            msg = asyncio.run(db.one(
                "SELECT direction, body FROM messages WHERE external_event_id = %s",
                (res.get("event_id", ""),)))
            lead = asyncio.run(db.one(
                "SELECT stage_id, status FROM leads WHERE id = %s", (SEED_LEAD_A,)))
            fu = asyncio.run(db.count(
                "SELECT count(*) AS n FROM follow_ups WHERE lead_id = %s "
                "AND demo_seed_key IS NULL", (SEED_LEAD_A,)))
            audit = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE outcome='success' "
                "AND action='crm.inbound.process'"))
            outbox = asyncio.run(db.count(
                "SELECT count(*) AS n FROM outbox_events "
                "WHERE event_type='InboundMessageProcessed'"))
            ok = (body.get("accepted") is True and body.get("replayed") is False
                  and msg is not None and msg["body"] == CANONICAL_BODY
                  and lead is not None and lead["status"] == "qualified"
                  and str(lead["stage_id"]) != SEED_STAGE_A
                  and fu == 1 and audit == 1 and outbox == 1)
            record("E2 simulate: message+lead update+transition+follow-up+audit+outbox durable",
                   ok, json.dumps({"lead": lead, "fu": fu, "ob": outbox}, default=str)[:140])

            # E3. Reads show the effect through the dashboard surfaces.
            r_inbox = client.get(INBOX_ROUTE, headers={"X-Correlation-Id": "wu10-e3a"})
            r_leads = client.get(LEADS_ROUTE, headers={"X-Correlation-Id": "wu10-e3b"})
            r_act = client.get(ACTIVITY_ROUTE, headers={"X-Correlation-Id": "wu10-e3c"})
            r_ag = client.get(AGENDA_ROUTE, headers={"X-Correlation-Id": "wu10-e3d"})
            inbox = r_inbox.json()
            leads = r_leads.json()
            act = r_act.json()
            items_ok = any(CANONICAL_BODY in json.dumps(i, ensure_ascii=False) for i in inbox.get("items", []))
            lead_ok = any(i.get("status") == "qualified" for i in leads.get("items", []))
            act_ok = any(i.get("action") == "crm.inbound.process"
                         and i.get("outcome") == "success" for i in act.get("items", []))
            ag_ok = r_ag.json()["counts"]["pending"] == 2  # seed + simulated
            ok = inbox.get("account_id") == ACCOUNT_A and items_ok and lead_ok and act_ok and ag_ok
            record("E3 reads: inbox thread + leads qualified + activity row + agenda", ok,
                   f"inbox={items_ok} leads={lead_ok} act={act_ok} ag={ag_ok}")

            # E4. Reload durability: fresh app instance (new process cache/session).
            app2 = build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter())
            with TestClient(app2) as fresh:
                h1 = fresh.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu10-e4a"})
                r2 = fresh.get(INBOX_ROUTE, headers={"X-Correlation-Id": "wu10-e4b"})
                h2 = fresh.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu10-e4c"})
                dash = h1.json()
                inbox2 = r2.json()
                ok = (h1.headers.get("x-cache-status") == "miss"
                      and dash["counts"]["unread_messages"] == 2  # seed 1 + simulated 1
                      and any(CANONICAL_BODY in json.dumps(i, ensure_ascii=False) for i in inbox2["items"])
                      and h2.headers.get("x-cache-status") == "hit")
            record("E4 reload durable: fresh client sees persisted effects", ok,
                   f"unread={dash['counts']['unread_messages']}")

            # E5. Switch B: zero A data anywhere in B's payloads.
            r_sel = client.post(SELECT_ROUTE, json={"requestedAccountId": ACCOUNT_B},
                                headers={"X-Correlation-Id": "wu10-e5a"})
            dumps: list[str] = []
            for route in (DASHBOARD_ROUTE, INBOX_ROUTE, LEADS_ROUTE, AGENDA_ROUTE, ACTIVITY_ROUTE):
                rr = client.get(route, headers={"X-Correlation-Id": f"wu10-e5b{route}"})
                dumps.append(json.dumps(rr.json()))
            b_all = " ".join(dumps)
            ok = (r_sel.status_code == 200
                  and r_sel.json().get("client_account_id") == ACCOUNT_B
                  and CANONICAL_BODY not in b_all
                  and res.get("event_id", "##") not in b_all
                  and "Camila Torres" not in b_all)  # A's contact never appears for B
            record("E5 switch B: full isolation, A effects absent from every B read", ok,
                   f"leak={'yes' if CANONICAL_BODY in b_all else 'no'}")

            # E6. Switch back A: durable effects still visible (spec scenario).
            client.post(SELECT_ROUTE, json={"requestedAccountId": ACCOUNT_A},
                        headers={"X-Correlation-Id": "wu10-e6a"})
            r_back = client.get(INBOX_ROUTE, headers={"X-Correlation-Id": "wu10-e6b"})
            body_back = json.dumps(r_back.json(), ensure_ascii=False)
            ok = r_back.status_code == 200 and CANONICAL_BODY in body_back
            record("E6 switch back A: simulated message persisted across switches", ok, "")

            # E7. Replay same event: one effect, identical canonical result.
            r_rep = client.post(SIMULATE_PATH, json={},
                                headers={"X-Correlation-Id": "wu10-e7"})
            rep = r_rep.json()
            msgs = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE external_event_id = %s",
                (res.get("event_id", ""),)))
            ok = (r_rep.status_code == 200 and rep.get("replayed") is True
                  and rep.get("result") == res and msgs == 1)
            record("E7 replay: double simulate = one effect + same result", ok, f"msgs={msgs}")

            # E8. Idempotency key reuse with different payload -> 409 conflict.
            key = f"wu10-conflict-{uuid4().hex[:8]}"
            c1 = client.post(SIMULATE_PATH, json={"idempotencyKey": key,
                                                  "body": "Primera cosa"},
                             headers={"X-Correlation-Id": "wu10-e8a"})
            c2 = client.post(SIMULATE_PATH, json={"idempotencyKey": key,
                                                  "body": "Segunda cosa distinta"},
                             headers={"X-Correlation-Id": "wu10-e8b"})
            ok = (c1.status_code == 200 and c1.json().get("replayed") is False
                  and envelope_ok(c2, 409, "wu10-e8b", "tenant_idempotency_conflict"))
            record("E8 conflict: same key different payload = 409 envelope, no mutation", ok,
                   f"{c1.status_code}/{c2.status_code}")

            # E9. Cross-account inbound denial via the accepted WU5 command route:
            # session A, conversation of B -> 403 + redacted denial audit + zero B mutation.
            unread_b_before = asyncio.run(db.count(
                "SELECT unread_count AS n FROM conversations WHERE id = %s",
                (SEED_CONVERSATION_B,)))
            app_cmd = build_mvp_wu5_application(cfg, ACCOUNT_A, DemoIdentityAdapter())
            ev_intrusion = f"wu10-cross-{uuid4().hex[:8]}"
            with TestClient(app_cmd) as cmd_client:
                d = cmd_client.post(INBOUND_COMMAND_ROUTE,
                                    json=wu5_inbound_payload(
                                        ev_intrusion, SEED_CONVERSATION_B, ACCOUNT_A,
                                        "intrusion across accounts"),
                                    headers={"X-Correlation-Id": "wu10-e9"})
            denial = asyncio.run(db.one(
                "SELECT outcome, resource_id, metadata->>'reason' AS reason "
                "FROM audit_records WHERE outcome='denied' "
                "ORDER BY created_at DESC, id DESC LIMIT 1"))
            msgs_intrusion = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE external_event_id = %s",
                (ev_intrusion,)))
            unread_b_after = asyncio.run(db.count(
                "SELECT unread_count AS n FROM conversations WHERE id = %s",
                (SEED_CONVERSATION_B,)))
            ok = (envelope_ok(d, 403, "wu10-e9", "tenant_access_denied")
                  and SEED_CONVERSATION_B not in d.text
                  and denial is not None and denial["reason"] == "conversation_not_found_in_scope"
                  and denial["resource_id"] is None
                  and msgs_intrusion == 0 and unread_b_after == unread_b_before)
            record("E9 cross-account denial: 403 envelope + redacted denial audit + zero B change",
                   ok, json.dumps(denial, default=str)[:120])

            # E10. Reset blocked by pending simulator effects (409 + count).
            r_block = client.post(RESET_ROUTE, headers={"X-Correlation-Id": "wu10-e10"})
            conv = asyncio.run(db.one("SELECT id FROM conversations WHERE id = %s",
                                      (A_CONVERSATION,)))
            ok = (r_block.status_code == 409
                  and r_block.json()["error"]["code"] == "demo_effects_pending"
                  and r_block.json()["error"]["correlation_id"] == "wu10-e10"
                  and r_block.headers.get("x-correlation-id") == "wu10-e10"
                  and r_block.json().get("pending_effects", 0) >= 8 and conv is not None)
            record("E10 reset blocked: 409 demo_effects_pending with count, seed untouched", ok,
                   f"pending={r_block.json().get('pending_effects')}")

            # E11. clear_effects reset -> baseline -> plain reset -> re-seed.
            r_force = client.post(f"{RESET_ROUTE}?clear_effects=true",
                                  headers={"X-Correlation-Id": "wu10-e11a"})
            state = asyncio.run(db.one("""
                SELECT (SELECT count(*) FROM messages WHERE demo_seed_key IS NULL) AS unmarked_msgs,
                       (SELECT count(*) FROM messages WHERE demo_seed_key IS NOT NULL) AS seed_msgs,
                       (SELECT stage_id FROM leads WHERE id = %s) AS lead_stage
            """, (SEED_LEAD_A,)))
            r_plain = client.post(RESET_ROUTE, headers={"X-Correlation-Id": "wu10-e11b"})
            r_seed = client.post(SEED_ROUTE, headers={"X-Correlation-Id": "wu10-e11c"})
            dash_final = client.get(DASHBOARD_ROUTE,
                                    headers={"X-Correlation-Id": "wu10-e11d"}).json()
            ok = (r_force.status_code == 200 and r_force.json().get("reset") is True
                  and r_force.json().get("cleared_effects", 0) >= 8
                  and state["unmarked_msgs"] == 0 and state["seed_msgs"] == 2
                  and str(state["lead_stage"]) == SEED_STAGE_A
                  and r_plain.status_code == 200 and r_seed.status_code == 200
                  and r_seed.json().get("seeded") is True
                  and dash_final["counts"]["leads_by_status"] == {"new": 1}
                  and dash_final["counts"]["unread_messages"] == 1)
            record("E11 force-reset -> plain reset -> re-seed: deterministic baseline restored",
                   ok, json.dumps(state, default=str)[:120])

            # E12. Route discipline: unknown 404 + wrong-method 405 envelopes.
            r_404 = client.get(f"{DEMO_PREFIX}/nope", headers={"X-Correlation-Id": "wu10-e12a"})
            r_405 = client.get(SIMULATE_PATH, headers={"X-Correlation-Id": "wu10-e12b"})
            ok = (envelope_ok(r_404, 404, "wu10-e12a", "route_not_found")
                  and envelope_ok(r_405, 405, "wu10-e12b", "method_not_allowed"))
            record("E12 404/405 envelope discipline on demo surfaces", ok,
                   f"{r_404.status_code}/{r_405.status_code}")
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_mvp_e2e():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"MVP-CRM WU10 E2E SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
