"""MVP Commercial Demo WU9: dev-only simulator endpoint + blocked-reset resolution.

Runs against production_runtime_mvp_wu9 (disposable database: migrations
001-004 + deterministic mvp_seed). Never touches earlier accepted bases.

Covers: canonical payload through the labeled /v1/demo/simulate surface over
composition WU8 (selector-governed session), full durable effect (message +
lead update + stage transition + follow-up + audit + outbox) verified with
direct SQL oracles, two clicks one effect + same result (fallback and explicit
key), replay vs conflict vs cross-key redelivery, denial envelopes with
consistent correlation and zero mutation, scoped-cache invalidation after the
command, session switch isolation, the fail-closed reset block reported as
409 demo_effects_pending, and the tested force path (?clear_effects=true)
restoring the deterministic seed baseline without touching demo markers.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.composition_wu8_mvp import (
    DEMO_PREFIX,
    RESET_ROUTE,
    SELECT_ROUTE,
    build_mvp_wu8_application,
)
from api.identity.demo_identity import (
    AbsentDemoIdentityAdapter,
    DemoIdentityAdapter,
)
from api.routes.simulator_wu9 import SIMULATE_PATH

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_SEEDED = "10000000-0000-0000-0000-000000000001"
A_CONVERSATION = "66000000-0000-0000-0000-000000000001"
SEED_LEAD_A = "65000000-0000-0000-0000-000000000001"
SEED_STAGE_A = "64000000-0000-0000-0000-000000000001"

DASHBOARD_ROUTE = f"{DEMO_PREFIX}/dashboard"
CANONICAL_BODY = "Simulador entrante: me gustaría visitar la propiedad."


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


SEED_CONVERSATION_A = A_CONVERSATION
SEED_CONVERSATION_B = "66000000-0000-0000-0000-000000000002"

PRECLEAN_STATEMENTS: tuple[tuple[str, tuple], ...] = (
    ("DELETE FROM follow_ups WHERE demo_seed_key IS NULL", ()),
    ("DELETE FROM messages WHERE demo_seed_key IS NULL", ()),
    ("UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000001', "
     "status='new', updated_at = now() WHERE demo_seed_key = 'demo-a-lead-001'", ()),
    ("UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000002', "
     "status='qualified', updated_at = now() WHERE demo_seed_key = 'demo-b-lead-001'", ()),
    ("DELETE FROM leads WHERE demo_seed_key IS NULL", ()),
    ("DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL", ()),
    ("DELETE FROM conversations WHERE demo_seed_key IS NULL", ()),
    ("DELETE FROM contacts WHERE demo_seed_key IS NULL", ()),
    ("UPDATE conversations SET unread_count = 1, updated_at = created_at WHERE id = %s",
     (SEED_CONVERSATION_A,)),
    ("UPDATE conversations SET unread_count = 0, updated_at = created_at WHERE id = %s",
     (SEED_CONVERSATION_B,)),
    ("DELETE FROM command_idempotency", ()),
    ("DELETE FROM audit_records", ()),
    ("DELETE FROM outbox_events", ()),
)


async def preclean(db: Sql) -> None:
    """Restore the deterministic seed baseline between runs (same rules as the
    WU7 harness clear_unmarked + the WU9 unmarked simulator stage fixture)."""
    for stmt, params in PRECLEAN_STATEMENTS:
        await db.q(stmt, params)


def envelope_ok(response, status: int, correlation: str, code: str) -> bool:
    if response.status_code != status:
        return False
    body = response.json()
    err = body.get("error") or {}
    return (err.get("code") == code
            and err.get("correlation_id") == correlation
            and response.headers.get("x-correlation-id") == correlation
            and isinstance(err.get("message"), str) and bool(err["message"]))


def _simulate(client: TestClient, correlation: str, **body):
    return client.post(SIMULATE_PATH, json=body,
                       headers={"X-Correlation-Id": correlation})


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))

        app = build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter())
        app_noauth = build_mvp_wu8_application(cfg, ACCOUNT_A, AbsentDemoIdentityAdapter())
        with TestClient(app) as client, TestClient(app_noauth) as client_anon:

            baseline_msgs = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages"))

            # S1. One click: full durable effect verified against direct SQL.
            r1 = _simulate(client, "wu9-s1")
            b1 = r1.json() if r1.status_code == 200 else {}
            res = b1.get("result", {})
            event_id = res.get("event_id", "")
            msg = asyncio.run(db.one(
                "SELECT id, direction, body, external_event_id, conversation_id "
                "FROM messages WHERE external_event_id = %s", (event_id,)))
            lead = asyncio.run(db.one(
                "SELECT stage_id, status FROM leads WHERE id = %s", (SEED_LEAD_A,)))
            fu = asyncio.run(db.one(
                "SELECT id, status, lead_id FROM follow_ups WHERE demo_seed_key IS NULL"))
            audit = asyncio.run(db.one(
                "SELECT outcome, action FROM audit_records "
                "WHERE client_account_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
                (ACCOUNT_A,)))
            outbox = asyncio.run(db.count(
                "SELECT count(*) AS n FROM outbox_events WHERE event_type = 'InboundMessageProcessed'"))
            sim_stage = asyncio.run(db.one(
                "SELECT id FROM pipeline_stages WHERE pipeline_id = "
                "'63000000-0000-0000-0000-000000000001' AND position = 1"))
            ok = (r1.status_code == 200 and b1.get("accepted") is True
                  and b1.get("replayed") is False
                  and b1.get("environment") == "dev-only-simulator"
                  and b1.get("correlation_id") == "wu9-s1"
                  and r1.headers.get("x-correlation-id") == "wu9-s1"
                  and msg is not None and msg["direction"] == "inbound"
                  and msg["body"] == CANONICAL_BODY
                  and str(msg["conversation_id"]) == A_CONVERSATION
                  and res.get("lead_created") is False
                  and lead is not None and sim_stage is not None
                  and str(lead["stage_id"]) == str(sim_stage["id"])
                  and lead["status"] == "qualified"
                  and res.get("current_stage_id") == str(sim_stage["id"])
                  and res.get("previous_stage_id") == SEED_STAGE_A
                  and fu is not None and fu["status"] == "pending"
                  and str(fu["lead_id"]) == SEED_LEAD_A
                  and res.get("follow_up_id") == str(fu["id"])
                  and audit is not None and audit["outcome"] == "success"
                  and audit["action"] == "crm.inbound.process"
                  and outbox == 1)
            record("S1 click: message+lead update+transition+follow-up+audit+outbox durable (SQL oracles)",
                   ok, json.dumps({"msg": bool(msg), "stage": bool(sim_stage),
                                   "lead": lead, "fu": bool(fu), "outbox": outbox}, default=str)[:180])

            # S2. Second click (same payload): replay, one effect, same result.
            r2 = _simulate(client, "wu9-s2")
            b2 = r2.json() if r2.status_code == 200 else {}
            msg_rows = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE external_event_id = %s", (event_id,)))
            total_msgs = asyncio.run(db.count("SELECT count(*) AS n FROM messages"))
            success_audits = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE outcome='success'"))
            ok = (r2.status_code == 200 and b2.get("replayed") is True
                  and b2.get("result") == res
                  and b2.get("correlation_id") == "wu9-s2"
                  and r2.headers.get("x-correlation-id") == "wu9-s2"
                  and msg_rows == 1 and total_msgs == baseline_msgs + 1
                  and success_audits == 1)
            record("S2 two clicks same payload = one effect + identical result", ok,
                   f"msg_rows={msg_rows} total={total_msgs} aud={success_audits}")

            # S3. Explicit idempotency key: two clicks same key = replay;
            # same key + different payload = 409 conflict without mutation.
            r3a = _simulate(client, "wu9-s3a", body="Visita el sabado",
                            idempotencyKey="wu9-explicit-key")
            r3b = _simulate(client, "wu9-s3b", body="Visita el sabado",
                            idempotencyKey="wu9-explicit-key")
            r3c = _simulate(client, "wu9-s3c", body="Otra cosa distinta",
                            idempotencyKey="wu9-explicit-key")
            b3a, b3b = r3a.json(), r3b.json()
            ok = (r3a.status_code == 200 and r3a.json().get("replayed") is False
                  and r3b.status_code == 200 and b3b.get("replayed") is True
                  and b3b.get("result") == b3a.get("result")
                  and envelope_ok(r3c, 409, "wu9-s3c", "tenant_idempotency_conflict"))
            record("S3 explicit key: replay same result; conflict on reused key + new payload",
                   ok, f"{r3a.status_code}/{r3b.status_code}/{r3c.status_code}")

            # S4. Same event redelivered under a NEW key: denial 403, zero mutation.
            msg_before = asyncio.run(db.count("SELECT count(*) AS n FROM messages"))
            # replay event s3a (same canonical body + ts) but force a fresh key:
            # eventId derives from body+ts -> same event, different key -> dedup denial
            r4 = _simulate(client, "wu9-s4", body="Visita el sabado",
                           receivedAt="2026-09-05T12:00:00.000Z",
                           idempotencyKey=f"redeliver-{uuid4().hex[:8]}")
            msg_after = asyncio.run(db.count("SELECT count(*) AS n FROM messages"))
            ok = envelope_ok(r4, 403, "wu9-s4", "tenant_access_denied") and msg_after == msg_before
            record("S4 redelivery same event under new key: 403 denial, zero mutation", ok,
                   f"{r4.status_code} msgs {msg_before}->{msg_after}")

            # S5. Malformed payload override -> 400 envelope before any effect.
            r5 = _simulate(client, "wu9-s5", body="   ")
            r5b = _simulate(client, "wu9-s5b", receivedAt="nope")
            ok = (r5.status_code == 200 and  # blank body falls back to canonical default
                  r5b.status_code == 400 and
                  envelope_ok(r5b, 400, "wu9-s5b", "invalid_payload"))
            record("S5 body fallback canonical / invalid receivedAt -> 400 envelope", ok,
                   f"{r5.status_code}/{r5b.status_code}")

            # S6. Cache invalidation + reads reflect the effect: dashboard
            # after simulate must be a MISS (scope dropped) and inbox unread grew.
            # warm the cache first (S1 invalidated; fill now)
            client.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu9-s6warm"})
            r6 = _simulate(client, "wu9-s6", body="Tercer evento distinto",
                           receivedAt="2026-09-05T13:00:00.000Z")
            h_dash = client.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu9-s6b"})
            dash = h_dash.json()
            unread_sql = asyncio.run(db.count(
                "SELECT unread_count AS n FROM conversations WHERE id = %s", (A_CONVERSATION,)))
            ok = (r6.status_code == 200
                  and h_dash.headers.get("x-cache-status") == "miss"
                  and dash["counts"]["unread_messages"] == unread_sql
                  and unread_sql >= 3)  # seed 1 + two simulated events on A
            record("S6 simulate invalidates scoped cache; dashboard unread matches SQL", ok,
                   f"cache={h_dash.headers.get('x-cache-status')} unread={unread_sql}")

            # S7. Session switch: simulate applies to the SELECTED account only.
            r_sel = client.post(SELECT_ROUTE, json={"requestedAccountId": ACCOUNT_B},
                                headers={"X-Correlation-Id": "wu9-s7sel"})
            r7 = _simulate(client, "wu9-s7", body="Mensaje para B",
                           receivedAt="2026-09-05T14:00:00.000Z")
            b_msgs = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s", (ACCOUNT_B,)))
            a_msgs = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s", (ACCOUNT_A,)))
            ok = (r_sel.status_code == 200 and r7.status_code == 200
                  and r7.json()["result"]["conversation_id"] ==
                  "66000000-0000-0000-0000-000000000002"
                  and b_msgs == 2 and a_msgs == 4)  # A: seed1 + s1 + s3a + s6; B: seed1 + s7
            record("S7 switch to B: simulate targets session account, A untouched", ok,
                   f"A={a_msgs} B={b_msgs}")

            # S8. No identity -> 401 envelope on the simulate surface.
            r8 = client_anon.post(SIMULATE_PATH, json={},
                                  headers={"X-Correlation-Id": "wu9-s8"})
            record("S8 absent identity: 401 envelope, consistent correlation",
                   envelope_ok(r8, 401, "wu9-s8", "authentication_required"),
                   f"{r8.status_code}")

            # S9. Blocked reset reported explicitly (fail-closed kept, no mutation).
            r9 = client.post(RESET_ROUTE, headers={"X-Correlation-Id": "wu9-s9"})
            conv = asyncio.run(db.one("SELECT id FROM conversations WHERE id = %s", (A_CONVERSATION,)))
            ok = (r9.status_code == 409
                  and r9.json().get("error", {}).get("code") == "demo_effects_pending"
                  and r9.json().get("error", {}).get("correlation_id") == "wu9-s9"
                  and r9.headers.get("x-correlation-id") == "wu9-s9"
                  and r9.json().get("pending_effects", 0) >= 10
                  and conv is not None)
            record("S9 reset with pending simulator effects: 409 demo_effects_pending + count", ok,
                   f"{r9.status_code} pending={r9.json().get('pending_effects')}")

            # S10. Force path clears effects and restores the seed baseline.
            r10 = client.post(f"{RESET_ROUTE}?clear_effects=true",
                              headers={"X-Correlation-Id": "wu9-s10"})
            b10 = r10.json() if r10.status_code == 200 else {}
            counts = asyncio.run(db.one("""
                SELECT (SELECT count(*) FROM messages WHERE demo_seed_key IS NULL) AS unmarked_msgs,
                       (SELECT count(*) FROM pipeline_stages WHERE demo_seed_key IS NULL) AS unmarked_stages,
                       (SELECT count(*) FROM command_idempotency) AS idem,
                       (SELECT count(*) FROM audit_records) AS aud,
                       (SELECT count(*) FROM messages WHERE demo_seed_key IS NOT NULL) AS seed_msgs,
                       (SELECT count(*) FROM conversations WHERE demo_seed_key IS NOT NULL) AS seed_convs,
                       (SELECT count(*) FROM leads WHERE demo_seed_key IS NOT NULL) AS seed_leads,
                       (SELECT stage_id FROM leads WHERE id = %s) AS lead_stage
            """, (SEED_LEAD_A,)))
            h_dash2 = client.get(DASHBOARD_ROUTE, headers={"X-Correlation-Id": "wu9-s10b"})
            dash2 = h_dash2.json()
            ok = (r10.status_code == 200 and b10.get("reset") is True
                  and isinstance(b10.get("cleared_effects"), int) and b10["cleared_effects"] >= 10
                  and counts["unmarked_msgs"] == 0 and counts["unmarked_stages"] == 0
                  and counts["idem"] == 0 and counts["aud"] == 0
                  and counts["seed_msgs"] == 2 and counts["seed_convs"] == 2
                  and counts["seed_leads"] == 2
                  and str(counts["lead_stage"]) == SEED_STAGE_A
                  and h_dash2.headers.get("x-cache-status") == "miss"
                  and dash2["account_id"] == ACCOUNT_B
                  and dash2["counts"]["unread_messages"] == 0  # B seeded baseline
                  and dash2["counts"]["leads_total"] == 1)
            record("S10 force reset: effects cleared, seed markers intact, baseline restored", ok,
                   json.dumps({k: str(v) for k, v in (counts or {}).items()}, default=str)[:200])

            # S11. After the clean baseline a plain reset passes without 409.
            r11 = client.post(RESET_ROUTE, headers={"X-Correlation-Id": "wu9-s11"})
            record("S11 plain reset on clean baseline: 200 (no pending effects)",
                   r11.status_code == 200 and r11.json().get("reset") is True,
                   f"{r11.status_code}")

            # S12. Unknown route under /v1/demo still shares the envelope (no drift).
            r12 = client.get(SIMULATE_PATH, headers={"X-Correlation-Id": "wu9-s12"})
            ok = envelope_ok(r12, 405, "wu9-s12", "method_not_allowed")
            record("S12 GET on simulate route: 405 envelope (route registered, method strict)",
                   ok, f"{r12.status_code}")
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_simulator():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"MVP-CRM WU9 SIMULATOR SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
