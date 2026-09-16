"""Agent console (change: agent-console, WU16) real-PostgreSQL harness.

Runs against production_runtime_mvp_wu16 (disposable: runner 001-009 +
mvp_seed.sql + mvp_seed_005.sql + mvp_seed_008.sql + mvp_seed_009.sql) with
the StubModelProvider (no network, no API key, no langchain import). The 008
seed flips the two demo conversations to `auto`; setup re-pins `auto`
explicitly via SQL + the dev-only state endpoint (a previous WU16 run ends
paused by the K10 replay cycle, and the state endpoint 409s while paused).

Scenarios map to the design's Testing Strategy K1-K10:
K1 settings round-trip per account + tenant isolation; seeded defaults equal
the code constants (zero behavior change at install)
K2 prompt versioning (saves create versions + audit, skeleton-key payload
fails closed, seeded v1 preview byte-identical to build_system_prompt(),
revert inserts a new version)
K3 KB CRUD round-trip + self-test hit/miss, cross-account invisible,
deactivated article no longer matches
K4 calendar override (10-16h / 30-min / weekdays-only) alters booking
outcomes with destination label round-trip; invalid windows fail closed 400
K5 allowlist (`status` removed -> `transition_lead` denied with
`lead_field_not_allowed`; seeded `stage_id`+`status` succeeds via
execute_transition; unknown field rejected)
K6 kill-switch flip re-gates calendar writes to ESCALATE; `global_auto=FALSE`
suppresses auto dispatch while `policy_decision` still records; a narrowed
matrix row is honored action by action
K7 forged account / foreign / ghost ids non-enumerating on console routes
K8 stub runs K1-K7 offline (no API key, `langchain_openai` never imported)
K9 WU15 autonomy intact on the wu16 base (zero-click FAQ analyze ->
executed; manual-mode conversation one-click approve -> executed)
K10 monotonic lifecycle (approve on executed -> 409; resume when none open
-> 409; second resume replays the stored outcome with zero new effects) +
idempotent console replays (`console:{account}:{entity}:{key}` keys, second
write replayed with zero extra effects)

Drafts/events/decisions are created EXCLUSIVELY by RUNNING the accepted
command surfaces (analyze/approve/state/resume + console routes) — never by
hand-inserting rows. The one catalog fixture (a Palermo apartment, account A)
is cloned from the seeded PROP1 row in setup so the K5 transition scenario
has a resolvable city+budget target; preclean removes it every run.

Idempotency note (WU14 A17 lesson): analyze fallback keys hash
account|conversation|body, so every scenario needing fresh analysis uses a
DISTINCT body — no body is ever reused across scenarios.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.application.agent_persona import AGENT_TONE, build_system_prompt
from api.application.agent_policy import (
    AUTO_ACTION_TYPES,
    AUTO_EVENT_CREATION,
    ESCALATION_MECHANICS,
    GATED_ACTION_TYPES,
)
from api.composition_wu8_mvp import build_mvp_wu8_application
from api.domain.calendar_event import (
    BUSINESS_DAYS,
    BUSINESS_END_HOUR,
    BUSINESS_START_HOUR,
    SLOT_MINUTES,
)
from api.identity.demo_identity import AbsentDemoIdentityAdapter, DemoIdentityAdapter

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
B_CONV = "66000000-0000-0000-0000-000000000002"
A_PROP_1 = "62000000-0000-0000-0000-000000000001"
PALERMO_PROP = "62000000-0000-0000-0000-000000000009"
NUEVO_STAGE = "64000000-0000-0000-0000-000000000001"
CALIFICADO_STAGE = "64000000-0000-0000-0000-000000000003"
SEED_ART_001 = "6f000000-0000-0000-0000-000000000001"
GHOST = "0000dead-0000-4000-8000-000000000000"

SENTINEL = str(uuid5(NAMESPACE_URL, "agent-policy"))

AGENT_ANALYZE = "/v1/demo/agent/analyze"
AGENT_DRAFTS = "/v1/demo/agent/drafts"
AGENT_STATE = "/v1/demo/agent/state"
AGENT_RESUME = "/v1/demo/agent/handoff/resume"
SETTINGS = "/v1/demo/agent/settings"
PROMPTS = "/v1/demo/agent/prompts"
KB_ARTICLES = "/v1/demo/agent/kb/articles"
KB_SELFTEST = "/v1/demo/agent/kb/self-test"
CAL_CONFIG = "/v1/demo/agent/calendar-config"
LEAD_FIELDS = "/v1/demo/agent/lead-fields"


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


async def preclean(db: Sql, *, fixture: bool = True) -> None:
    """Deterministic baseline: mirrors the WU15 preclean (008 tables and
    Calificado stages kept via the same DROP-order care for RESTRICT FKs),
    then wipes the 009 console tables + KB fully — run_seed below re-inserts
    the owned 008/009 markers, so every run starts from identical defaults.
    Also resets agent_mode to the seeded `auto` (the K10 replay cycle ends
    paused, and the state endpoint 409s while paused). With fixture=False
    (end-of-run reconverge) the Palermo scenario fixture is not re-cloned,
    so the base is left in the pure seeded shape for the sibling console
    units, which count exact corpus sizes on this same base."""
    for stmt in (
        "DELETE FROM calendar_events",
        "DELETE FROM agent_drafts",
        "DELETE FROM agent_policy_matrix",
        "DELETE FROM agent_settings",
        "DELETE FROM agent_prompts",
        "DELETE FROM agent_field_allowlist",
        "DELETE FROM agent_calendar_config",
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
    await db.q(
        "UPDATE conversations SET agent_mode = 'auto', updated_at = created_at "
        "WHERE id IN ('66000000-0000-0000-0000-000000000001', "
        "'66000000-0000-0000-0000-000000000002')")
    if not fixture:
        return
    await db.q(
        "INSERT INTO properties (id, client_account_id, demo_seed_key, title, "
        "operation_type, location, bedrooms, price, currency, availability, "
        "reference_code, property_type, address, neighborhood, city, "
        "expenses_amount, accepts_swap, covered_area_m2, total_area_m2, rooms, "
        "bathrooms, parking_spaces, property_age_years, construction_status, "
        "capture_source, responsible_agent_id, status, price_amount, price_currency) "
        "SELECT %s, client_account_id, NULL, 'Depto en Palermo', operation_type, "
        "location, bedrooms, 195000, currency, availability, 'WU16-PALERMO', "
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


def console_ok(response, correlation: str, resource: str):
    if response.status_code != 200:
        return None
    body = response.json()
    if (body.get("accepted") is not True
            or body.get("correlation_id") != correlation
            or response.headers.get("x-correlation-id") != correlation
            or resource not in body):
        return None
    return body


def _get(client, url, correlation):
    return client.get(url, headers={"X-Correlation-Id": correlation})


def _post(client, route, body, correlation):
    return client.post(route, json=body,
                       headers={"X-Correlation-Id": correlation})


def _put(client, route, body, correlation):
    return client.put(route, json=body,
                      headers={"X-Correlation-Id": correlation})


def _delete(client, route, correlation):
    return client.delete(route, headers={"X-Correlation-Id": correlation})


def _analyze(client, body, correlation, idempotency_key=None):
    payload = {"body": body}
    if idempotency_key:
        payload["idempotencyKey"] = idempotency_key
    return _post(client, AGENT_ANALYZE, payload, correlation)


def _set_mode(client, mode, correlation):
    return _post(client, AGENT_STATE, {"mode": mode}, correlation)


def _resume(client, correlation):
    return _post(client, AGENT_RESUME, {}, correlation)


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    assert cfg.get("dbname") == "production_runtime_mvp_wu16", (
        f"refusing to run on {cfg.get('dbname')}")
    assert not (sys.modules.get("langchain_openai")), "stub must stay import-free"
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))
        models: set[str] = set()
        wu8_a = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter()))
        wu8_b = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_B, DemoIdentityAdapter()))

        with wu8_a as a, wu8_b as b:
            # Setup: preclean SQL already reset both canonical conversations
            # to `auto`. Assert via GET only — a POST pin here would consume
            # the `agent-mode:{conv}:auto` idempotency key that K9's unpin
            # needs fresh (same key + same hash replays without applying).
            for _client, _corr in ((a, "wu16-pin-a"), (b, "wu16-pin-b")):
                _st = _get(_client, AGENT_STATE, _corr)
                assert _st.status_code == 200, (
                    f"auto pin failed: {_st.status_code} {_st.text[:200]}")
                assert _st.json().get("agent_mode") == "auto"

            # K1. Settings round-trip + seeded defaults == code constants.
            g1 = _get(a, SETTINGS, "wu16-k1-get")
            s1 = console_ok(g1, "wu16-k1-get", "settings")
            matrix1 = ({e["action_type"]: e["verdict"]
                        for e in (s1["settings"].get("matrix") or [])}
                       if s1 else {})
            expected = ({t: "auto" for t in AUTO_ACTION_TYPES}
                        | {"handoff_to_human": "escalate"})
            ok = (s1 is not None
                  and s1["settings"].get("global_auto") is True
                  and s1["settings"].get("calendar_auto_enabled") is True
                  and bool(AUTO_EVENT_CREATION) is True
                  and matrix1 == expected
                  and set(matrix1) == (set(AUTO_ACTION_TYPES)
                                        | {"handoff_to_human"})
                  and GATED_ACTION_TYPES <= AUTO_ACTION_TYPES)
            record("K1 seeded settings equal code constants (zero change)",
                   ok, f"matrix_rows={len(matrix1)} auto={AUTO_EVENT_CREATION}")
            p1 = _put(a, SETTINGS, {"global_auto": False,
                                     "idempotencyKey": "k1-off"},
                        "wu16-k1-put")
            s1p = console_ok(p1, "wu16-k1-put", "settings")
            g1b = _get(b, SETTINGS, "wu16-k1-iso")
            s1b = console_ok(g1b, "wu16-k1-iso", "settings")
            ok = (s1p is not None and s1p.get("replayed") is False
                  and s1p["settings"].get("global_auto") is False
                  and s1b is not None
                  and s1b["settings"].get("global_auto") is True)
            record("K1 settings round-trip per account + tenant isolated",
                   ok, f"a_global={s1p['settings'].get('global_auto') if s1p else None}")
            r1 = _put(a, SETTINGS, {"global_auto": True,
                                     "idempotencyKey": "k1-on"},
                        "wu16-k1-restore")
            bad1 = _put(a, SETTINGS, {"global_auto": True, "zzz": 1},
                        "wu16-k1-badkey")
            ok = (console_ok(r1, "wu16-k1-restore", "settings") is not None
                  and envelope_ok(bad1, 400, "wu16-k1-badkey",
                                  "invalid_command"))
            record("K1 restore + unknown settings key fails closed 400",
                   ok, f"badkey={bad1.status_code}")
            au1 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id = %s "
                "AND action = 'crm.agent.settings.update'", ("wu16-k1-put",)))
            record("K1 settings write audited", au1 == 1, f"audits={au1}")

            # K2. Prompt versioning + skeleton immutability.
            pv = _get(a, f"{PROMPTS}/composed-preview", "wu16-k2-preview")
            pr = console_ok(pv, "wu16-k2-preview", "preview")
            ok = (pr is not None and pr["preview"].get("version") == 1
                  and pr["preview"].get("composed_text")
                  == build_system_prompt())
            record("K2 seeded v1 preview byte-identical to build_system_prompt()",
                   ok, f"version={pr['preview'].get('version') if pr else None}")
            tone2 = "cálido, directo y profesional"
            instr2 = "Responder siempre con amabilidad y precisión."
            c2 = _post(a, PROMPTS, {"tone": tone2, "instructions": instr2,
                                "idempotencyKey": "k2-v2"}, "wu16-k2-create")
            pc2 = console_ok(c2, "wu16-k2-create", "prompt")
            act2 = _get(a, f"{PROMPTS}/active", "wu16-k2-active")
            pa2 = console_ok(act2, "wu16-k2-active", "prompt")
            ok = (pc2 is not None and pc2.get("replayed") is False
                  and pc2["prompt"].get("version") == 2
                  and pc2["prompt"].get("tone") == tone2
                  and pa2 is not None and pa2["prompt"].get("version") == 2
                  and (pa2["prompt"].get("composed_snapshot") or "")
                  .find(tone2) >= 0
                  and (pa2["prompt"].get("composed_snapshot") or "")
                  .find(instr2) >= 0)
            record("K2 prompt save creates v2 + flips active", ok,
                   f"version={pc2['prompt'].get('version') if pc2 else None}")
            sk2 = _post(a, PROMPTS,
                        {"tone": tone2, "skeleton": "hacked"}, "wu16-k2-skel")
            ok = envelope_ok(sk2, 400, "wu16-k2-skel", "invalid_command")
            record("K2 skeleton-key payload fails closed 400", ok,
                   f"status={sk2.status_code}")
            rv2 = _post(a, f"{PROMPTS}/1/revert", {"idempotencyKey": "k2-rv"},
                          "wu16-k2-revert")
            pr2 = console_ok(rv2, "wu16-k2-revert", "prompt")
            act2b = _get(a, f"{PROMPTS}/active", "wu16-k2-active2")
            pa2b = console_ok(act2b, "wu16-k2-active2", "prompt")
            ok = (pr2 is not None and pr2["prompt"].get("version") == 3
                  and pr2["prompt"].get("reverted_from") == 1
                  and pr2["prompt"].get("tone") == AGENT_TONE
                  and pa2b is not None and pa2b["prompt"].get("version") == 3)
            record("K2 revert inserts v3 copying v1 (never UPDATE)", ok,
                   f"version={pr2['prompt'].get('version') if pr2 else None}")
            au2 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id IN "
                "(%s, %s) AND action = 'crm.agent.prompt.version'",
                ("wu16-k2-create", "wu16-k2-revert")))
            record("K2 prompt saves audited", au2 == 2, f"audits={au2}")

            # K3. KB CRUD + self-test hit/miss + deactivation + isolation.
            kb_q = "¿Aceptan trueque de bicicletas como seña?"
            kb_kw = ["trueque", "bicicletas", "seña", "tango"]
            kb_a = "Sí, tomamos bicicletas como parte de la seña con tasación previa."
            k3c = _post(a, KB_ARTICLES,
                        {"category": "visitas", "question": kb_q,
                         "keywords": kb_kw, "answer": kb_a,
                         "idempotencyKey": "k3-art"}, "wu16-k3-create")
            ac3 = console_ok(k3c, "wu16-k3-create", "article")
            art_id = (ac3["article"].get("resource_id") if ac3 else None)
            k3u = (_put(a, f"{KB_ARTICLES}/{art_id}",
                        {"category": "visitas", "question": kb_q,
                         "keywords": kb_kw,
                         "answer": kb_a + " Consultanos por los detalles.",
                         "idempotencyKey": "k3-upd"},
                        "wu16-k3-update") if art_id else None)
            ok = (ac3 is not None and ac3.get("replayed") is False
                  and art_id is not None
                  and k3u is not None and k3u.status_code == 200)
            record("K3 KB create + update round-trip", ok,
                   f"id={str(art_id)[:8] if art_id else None}")
            hit3 = _post(a, KB_SELFTEST,
                         {"question": "¿Qué incluyen las expensas del "
                                      "departamento de Palermo?"},
                         "wu16-k3-hit")
            ph3 = console_ok(hit3, "wu16-k3-hit", "probe")
            ok = (ph3 is not None and ph3["probe"].get("match") is True
                  and (ph3["probe"].get("article") or {}).get("id")
                  == SEED_ART_001
                  and (ph3["probe"].get("article") or {}).get("category")
                  == "expensas")
            record("K3 self-test hit returns the seeded article", ok,
                   f"article={str((ph3['probe'].get('article') or {}).get('id'))[:8] if ph3 else None}")
            own3 = _post(a, KB_SELFTEST, {"question": kb_q}, "wu16-k3-own")
            po3 = console_ok(own3, "wu16-k3-own", "probe")
            miss3 = _post(a, KB_SELFTEST,
                          {"question": "¿cómo es el trámite notarial?"},
                          "wu16-k3-miss")
            pm3 = console_ok(miss3, "wu16-k3-miss", "probe")
            ok = (po3 is not None and po3["probe"].get("match") is True
                  and (po3["probe"].get("article") or {}).get("id") == art_id
                  and pm3 is not None and pm3["probe"].get("match") is False)
            record("K3 self-test own-article hit + unrelated miss", ok,
                   f"own={po3['probe'].get('match') if po3 else None}")
            d3 = _delete(a, f"{KB_ARTICLES}/{art_id}", "wu16-k3-deact")
            row3 = asyncio.run(db.one(
                "SELECT deactivated_at FROM knowledge_base_articles "
                "WHERE id = %s", (art_id,)))
            gone3 = _post(a, KB_SELFTEST, {"question": kb_q},
                          "wu16-k3-gone")
            pg3 = console_ok(gone3, "wu16-k3-gone", "probe")
            cross3 = _post(b, KB_SELFTEST, {"question": kb_q},
                           "wu16-k3-cross")
            pc3 = console_ok(cross3, "wu16-k3-cross", "probe")
            ok = (d3.status_code == 200 and row3 is not None
                  and row3["deactivated_at"] is not None
                  and pg3 is not None and pg3["probe"].get("match") is False
                  and pc3 is not None and pc3["probe"].get("match") is False)
            record("K3 deactivated invisible + cross-account invisible", ok,
                   f"deactivated={row3['deactivated_at'] is not None if row3 else None}")
            badcat = _post(a, KB_ARTICLES,
                           {"category": "cocheras", "question": kb_q,
                            "keywords": kb_kw, "answer": kb_a},
                           "wu16-k3-badcat")
            ghostu = _put(a, f"{KB_ARTICLES}/{GHOST}",
                          {"answer": "fantasma"}, "wu16-k3-ghost")
            ghostd = _delete(a, f"{KB_ARTICLES}/{GHOST}", "wu16-k3-ghostd")
            ok = (envelope_ok(badcat, 400, "wu16-k3-badcat", "invalid_command")
                  and envelope_ok(ghostu, 404, "wu16-k3-ghost",
                                  "resource_not_found")
                  and envelope_ok(ghostd, 404, "wu16-k3-ghostd",
                                  "resource_not_found"))
            record("K3 bad category 400 + ghost KB ids 404", ok,
                   f"badcat={badcat.status_code}")

            # K4. Calendar override changes the booking window.
            g4 = _get(a, CAL_CONFIG, "wu16-k4-get")
            sc4 = console_ok(g4, "wu16-k4-get", "calendar_config")
            ok = (sc4 is not None
                  and sc4["calendar_config"].get("window_start") == "09:00"
                  and sc4["calendar_config"].get("window_end") == "18:00"
                  and sc4["calendar_config"].get("slot_minutes")
                  == SLOT_MINUTES
                  and sc4["calendar_config"].get("working_days")
                  == sorted(BUSINESS_DAYS))
            record("K4 seeded calendar mirrors BUSINESS_* constants", ok,
                   f"cfg={sc4['calendar_config'] if sc4 else None}")
            o4 = _put(a, CAL_CONFIG,
                      {"window_start": "10:00", "window_end": "16:00",
                       "slot_minutes": 30, "working_days": [0, 1, 2, 3, 4],
                       "destination_label": "Agenda demo",
                       "idempotencyKey": "k4-override"}, "wu16-k4-override")
            so4 = console_ok(o4, "wu16-k4-override", "calendar_config")
            ok = (so4 is not None
                  and so4["calendar_config"].get("slot_minutes") == 30
                  and so4["calendar_config"].get("working_days")
                  == [0, 1, 2, 3, 4]
                  and so4["calendar_config"].get("destination_label")
                  == "Agenda demo")
            record("K4 calendar override round-trips (10-16h/30m/wd-only)",
                   ok, f"slot={so4['calendar_config'].get('slot_minutes') if so4 else None}")
            sat4 = _analyze(a, "quiero coordinar una visita el 2026-09-19 "
                               "a las 10:00, por favor", "wu16-k4-sat")
            ds4 = (sat4.json().get("draft") or {})
            ok = (sat4.status_code == 200
                  and ds4.get("intent") == "schedule_visit"
                  and ds4.get("status") == "proposed"
                  and ds4.get("policy_decision") == "escalate")
            record("K4 Saturday booking escalates under weekdays-only", ok,
                   f"policy={ds4.get('policy_decision')}")
            half4 = _analyze(a, "quiero coordinar una visita el 2026-09-21 "
                                "a las 10:30, por favor", "wu16-k4-half")
            dh4 = (half4.json().get("draft") or {})
            models.add(dh4.get("model_name"))
            ev4 = asyncio.run(db.one(
                "SELECT source FROM calendar_events WHERE client_account_id = %s "
                "AND starts_at = %s AND status = 'scheduled'",
                (ACCOUNT_A, datetime(2026, 9, 21, 13, 30,
                                     tzinfo=timezone.utc))))
            ok = (half4.status_code == 200
                  and (half4.json().get("auto_executed") is True)
                  and dh4.get("status") == "executed"
                  and ev4 is not None and ev4["source"] == "agent")
            record("K4 half-hour slot books under 30-min override", ok,
                   f"event={ev4 is not None}")
            badwin = _put(a, CAL_CONFIG,
                          {"window_start": "18:00", "window_end": "09:00"},
                          "wu16-k4-badwin")
            halfwin = _put(a, CAL_CONFIG, {"window_start": "10:00"},
                           "wu16-k4-halfwin")
            ok = (envelope_ok(badwin, 400, "wu16-k4-badwin",
                              "invalid_command")
                  and envelope_ok(halfwin, 400, "wu16-k4-halfwin",
                                  "invalid_command"))
            record("K4 invalid windows fail closed 400", ok,
                   f"bad={badwin.status_code} half={halfwin.status_code}")
            back4 = _put(a, CAL_CONFIG,
                         {"window_start": "09:00", "window_end": "18:00",
                          "slot_minutes": 60,
                          "working_days": [0, 1, 2, 3, 4, 5],
                          "destination_label": "Agenda de la cuenta",
                          "idempotencyKey": "k4-restore"},
                         "wu16-k4-restore")
            ok = (console_ok(back4, "wu16-k4-restore", "calendar_config")
                  is not None)
            record("K4 calendar restored to BUSINESS_* defaults", ok, "")

            # K5. Allowlist allow + deny (dual-column gate).
            g5 = _get(a, LEAD_FIELDS, "wu16-k5-get")
            sg5 = console_ok(g5, "wu16-k5-get", "allowlist")
            ok = (sg5 is not None
                  and sg5["allowlist"].get("allowed") == ["stage_id", "status"])
            record("K5 seeded allowlist is stage_id + status", ok,
                   f"allowed={sg5['allowlist'].get('allowed') if sg5 else None}")
            narrow5 = _put(a, LEAD_FIELDS, {"allowed": ["stage_id"],
                                            "idempotencyKey": "k5-narrow"},
                           "wu16-k5-narrow")
            ok = (console_ok(narrow5, "wu16-k5-narrow", "allowlist")
                  is not None)
            record("K5 allowlist narrowed to stage_id only", ok, "")
            deny5 = _analyze(a, "busco un depto en Palermo hasta 200000, "
                                "gracias", "wu16-k5-deny")
            dj5 = deny5.json() if deny5.status_code == 200 else {}
            dd5 = (dj5.get("draft") or {})
            models.add(dd5.get("model_name"))
            res5 = ({r.get("type"): (r.get("outcome"), r.get("reason"))
                    for r in (dd5.get("execution_result") or [])})
            lead5 = asyncio.run(db.one(
                "SELECT stage_id FROM leads WHERE id = %s", (A_LEAD,)))
            ok = (deny5.status_code == 200 and dj5.get("auto_executed") is True
                  and dd5.get("status") == "executed"
                  and res5.get("register_interest", (None,))[0] == "success"
                  and res5.get("transition_lead") == (
                      "denied", "lead_field_not_allowed")
                  and lead5 is not None
                  and str(lead5["stage_id"]) == NUEVO_STAGE)
            record("K5 transition denied truthfully, saga finalizes", ok,
                   f"transition={res5.get('transition_lead')}")
            wide5 = _put(a, LEAD_FIELDS,
                         {"allowed": ["stage_id", "status"],
                          "idempotencyKey": "k5-wide"},
                         "wu16-k5-restore")
            ok5 = (console_ok(wide5, "wu16-k5-restore", "allowlist")
                   is not None)
            allow5 = _analyze(a, "busco un depto en Palermo hasta 200000, "
                                 "por favor", "wu16-k5-allow")
            aj5 = allow5.json() if allow5.status_code == 200 else {}
            ad5 = (aj5.get("draft") or {})
            models.add(ad5.get("model_name"))
            res5b = ({r.get("type"): r.get("outcome")
                      for r in (ad5.get("execution_result") or [])})
            lead5b = asyncio.run(db.one(
                "SELECT stage_id FROM leads WHERE id = %s", (A_LEAD,)))
            ok = (ok5 and allow5.status_code == 200
                  and res5b.get("transition_lead") == "success"
                  and lead5b is not None
                  and str(lead5b["stage_id"]) == CALIFICADO_STAGE)
            record("K5 restored allowlist transitions via execute_transition",
                   ok, f"stage={str(lead5b['stage_id'])[:8] if lead5b else None}")
            badcol = _put(a, LEAD_FIELDS, {"allowed": ["email"]},
                          "wu16-k5-badcol")
            ok = envelope_ok(badcol, 400, "wu16-k5-badcol",
                             "invalid_command")
            record("K5 unknown allowlist column rejected 400", ok,
                   f"status={badcol.status_code}")

            # K6. Kill-switch + global_auto + narrowed matrix honored.
            kill6 = _put(a, SETTINGS, {"calendar_auto_enabled": False,
                                       "idempotencyKey": "k6-kill"},
                         "wu16-k6-kill")
            assert console_ok(kill6, "wu16-k6-kill", "settings") is not None
            sch6 = _analyze(a, "quiero coordinar una visita el 2026-09-21 "
                               "a las 11:00, por favor", "wu16-k6-sch")
            dk6 = (sch6.json().get("draft") or {})
            models.add(dk6.get("model_name"))
            ev6 = asyncio.run(db.one(
                "SELECT id FROM calendar_events WHERE client_account_id = %s "
                "AND starts_at = %s AND status = 'scheduled'",
                (ACCOUNT_A, datetime(2026, 9, 21, 14, 0,
                                     tzinfo=timezone.utc))))
            ok = (sch6.status_code == 200
                  and dk6.get("intent") == "schedule_visit"
                  and dk6.get("status") == "proposed"
                  and dk6.get("policy_decision") == "escalate"
                  and ev6 is None)
            record("K6 kill-switch re-gates calendar writes to ESCALATE",
                   ok, f"policy={dk6.get('policy_decision')}")
            unk6 = _put(a, SETTINGS, {"calendar_auto_enabled": True,
                                      "idempotencyKey": "k6-unkill"},
                          "wu16-k6-unkill")
            book6 = _analyze(a, "quiero coordinar una visita el 2026-09-22 "
                                "a las 10:00, por favor", "wu16-k6-book")
            bk6 = (book6.json().get("draft") or {})
            models.add(bk6.get("model_name"))
            ev6b = asyncio.run(db.one(
                "SELECT source FROM calendar_events WHERE client_account_id = %s "
                "AND starts_at = %s AND status = 'scheduled'",
                (ACCOUNT_A, datetime(2026, 9, 22, 13, 0,
                                     tzinfo=timezone.utc))))
            ok = (console_ok(unk6, "wu16-k6-unkill", "settings") is not None
                  and book6.status_code == 200
                  and bk6.get("status") == "executed"
                  and ev6b is not None and ev6b["source"] == "agent")
            record("K6 kill-switch restore re-enables auto booking", ok,
                   f"event={ev6b is not None}")
            off6 = _put(a, SETTINGS, {"global_auto": False,
                                      "idempotencyKey": "k6-off"},
                          "wu16-k6-off")
            assert console_ok(off6, "wu16-k6-off", "settings") is not None
            faq6 = _analyze(a, "¿Cuánto tarda la escritura?",
                            "wu16-k6-faq")
            fj6 = faq6.json() if faq6.status_code == 200 else {}
            df6 = (fj6.get("draft") or {})
            models.add(df6.get("model_name"))
            out6 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND direction = 'outbound'",
                (ACCOUNT_A, A_CONV)))
            ok = (faq6.status_code == 200
                  and fj6.get("auto_executed") is False
                  and df6.get("status") == "proposed"
                  and df6.get("policy_decision") == "auto"
                  and out6 == 0)
            record("K6 global_auto=FALSE suppresses dispatch, decision kept",
                   ok, f"policy={df6.get('policy_decision')} outbound={out6}")
            on6 = _put(a, SETTINGS, {"global_auto": True,
                                     "idempotencyKey": "k6-on"},
                         "wu16-k6-on")
            assert console_ok(on6, "wu16-k6-on", "settings") is not None
            nar6 = _put(a, SETTINGS,
                        {"matrix": [{"action_type": "send_reply",
                                     "verdict": "escalate"}],
                         "idempotencyKey": "k6-narrow"},
                        "wu16-k6-narrow")
            assert console_ok(nar6, "wu16-k6-narrow", "settings") is not None
            mat6 = _analyze(a, "¿Qué gastos tiene la escritura?",
                            "wu16-k6-matrix")
            mj6 = mat6.json() if mat6.status_code == 200 else {}
            dm6 = (mj6.get("draft") or {})
            models.add(dm6.get("model_name"))
            types6 = sorted(r.get("type")
                            for r in (dm6.get("execution_result") or []))
            out6b = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND direction = 'outbound'",
                (ACCOUNT_A, A_CONV)))
            ok = (mat6.status_code == 200
                  and "send_reply" not in types6
                  and "create_follow_up" in types6
                  and out6b == 0)
            record("K6 narrowed matrix honored (reply withheld, follow-up kept)",
                   ok, f"dispatched={types6}")
            wid6 = _put(a, SETTINGS,
                        {"matrix": [{"action_type": "send_reply",
                                     "verdict": "auto"}],
                         "idempotencyKey": "k6-widen"},
                        "wu16-k6-widen")
            ok = (console_ok(wid6, "wu16-k6-widen", "settings") is not None)
            record("K6 matrix restored to auto", ok, "")

            # K9. WU15 autonomy intact on the wu16 base.
            faq9 = _analyze(a, "¿Qué incluyen las expensas del edificio?",
                            "wu16-k9-faq")
            fj9 = faq9.json() if faq9.status_code == 200 else {}
            df9 = (fj9.get("draft") or {})
            models.add(df9.get("model_name"))
            t9 = [x.get("type") for x in df9.get("proposed_actions", [])]
            ok = (faq9.status_code == 200 and fj9.get("accepted") is True
                  and fj9.get("replayed") is False
                  and fj9.get("auto_executed") is True
                  and df9.get("status") == "executed"
                  and df9.get("policy_decision") == "auto"
                  and df9.get("execution_mode") == "agent_policy"
                  and t9 == ["send_reply", "create_follow_up"])
            record("K9 zero-click FAQ executed via agent_policy", ok,
                   f"policy={df9.get('policy_decision')} types={t9}")
            pin9 = _set_mode(a, "manual", "wu16-k9-pin")
            assert pin9.status_code == 200
            man9 = _analyze(a, "¿Aceptan mascotas en las propiedades?",
                            "wu16-k9-manual")
            mj9 = man9.json() if man9.status_code == 200 else {}
            dm9 = (mj9.get("draft") or {})
            models.add(dm9.get("model_name"))
            ok_manual = (man9.status_code == 200
                         and mj9.get("auto_executed") is False
                         and dm9.get("status") == "proposed"
                         and dm9.get("policy_decision") == "auto")
            ap9 = _post(a, f"{AGENT_DRAFTS}/{dm9.get('draft_id')}/approve",
                        {}, "wu16-k9-approve")
            esegu9 = ((ap9.json().get("draft") or {}).get("status")
                      if ap9.status_code == 200 else None)
            out9 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM messages WHERE client_account_id = %s "
                "AND conversation_id = %s AND direction = 'outbound'",
                (ACCOUNT_A, A_CONV)))
            unpin9 = _set_mode(a, "auto", "wu16-k9-unpin")
            ok = (ok_manual and ap9.status_code == 200
                  and esegu9 == "executed" and out9 >= 1
                  and unpin9.status_code == 200)
            record("K9 manual one-click approve executes + replies", ok,
                   f"status={esegu9} outbound={out9}")

            # K10. Monotonic lifecycle + idempotent replays.
            ap10a = _post(a, f"{AGENT_DRAFTS}/{df9.get('draft_id')}/approve",
                          {}, "wu16-k10-executed")
            ok = envelope_ok(ap10a, 409, "wu16-k10-executed",
                             "draft_not_proposed")
            record("K10 approve on executed -> 409", ok,
                   f"status={ap10a.status_code}")
            r409 = _resume(a, "wu16-k10-none")
            ok = envelope_ok(r409, 409, "wu16-k10-none",
                             "agent_handoff_not_open")
            record("K10 resume when none open -> 409", ok,
                   f"status={r409.status_code}")
            h10a = _analyze(a, "quiero hablar con un asesor humano, "
                               "por favor", "wu16-k10-h1")
            dh10a = (h10a.json().get("draft") or {})
            models.add(dh10a.get("model_name"))
            st10 = _get(a, AGENT_STATE, "wu16-k10-paused").json()
            rs10a = _resume(a, "wu16-k10-r1")
            ok = (h10a.status_code == 200
                  and dh10a.get("intent") == "handoff"
                  and dh10a.get("status") == "executed"
                  and st10.get("agent_mode") == "paused"
                  and rs10a.status_code == 200
                  and rs10a.json().get("agent_mode") == "auto")
            record("K10 handoff pauses + resume restores auto", ok,
                   f"mode={st10.get('agent_mode')}")
            h10b = _analyze(a, "quiero hablar con un asesor humano, "
                               "gracias", "wu16-k10-h2")
            dh10b = (h10b.json().get("draft") or {})
            models.add(dh10b.get("model_name"))
            rs10b = _resume(a, "wu16-k10-r2")
            n_r1 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id = %s "
                "AND action = 'crm.agent.mode'", ("wu16-k10-r1",)))
            n_r2 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id = %s "
                "AND action = 'crm.agent.mode'", ("wu16-k10-r2",)))
            st10b = _get(a, AGENT_STATE, "wu16-k10-still").json()
            ok = (h10b.status_code == 200 and rs10b.status_code == 200
                  and rs10b.json().get("agent_mode") == "auto"
                  and n_r1 >= 1 and n_r2 == 0
                  and st10b.get("agent_mode") == "paused")
            record("K10 second resume replays (zero new effects)", ok,
                   f"audits_r1={n_r1} audits_r2={n_r2}")
            rp_settings = _put(a, SETTINGS,
                               {"global_auto": True,
                                "idempotencyKey": "k10-settings"},
                               "wu16-k10-idem")
            rp_settings2 = _put(a, SETTINGS,
                                {"global_auto": True,
                                 "idempotencyKey": "k10-settings"},
                                "wu16-k10-idem")
            rj1 = console_ok(rp_settings, "wu16-k10-idem", "settings")
            rj2 = console_ok(rp_settings2, "wu16-k10-idem", "settings")
            n_idem = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id = %s "
                "AND action = 'crm.agent.settings.update'",
                ("wu16-k10-idem",)))
            ok = (rj1 is not None and rj1.get("replayed") is False
                  and rj2 is not None and rj2.get("replayed") is True
                  and n_idem == 1)
            record("K10 settings replay: zero extra effects", ok,
                   f"replayed={rj2.get('replayed') if rj2 else None}")
            rp_prompt = _post(a, PROMPTS,
                              {"tone": "claro y breve", "instructions": "x",
                               "idempotencyKey": "k10-prompt"},
                              "wu16-k10-pidem")
            rp_prompt2 = _post(a, PROMPTS,
                               {"tone": "claro y breve", "instructions": "x",
                                "idempotencyKey": "k10-prompt"},
                               "wu16-k10-pidem")
            pj1 = console_ok(rp_prompt, "wu16-k10-pidem", "prompt")
            pj2 = console_ok(rp_prompt2, "wu16-k10-pidem", "prompt")
            n_prompts = asyncio.run(db.count(
                "SELECT count(*) AS n FROM agent_prompts "
                "WHERE client_account_id = %s", (ACCOUNT_A,)))
            ok = (pj1 is not None and pj2 is not None
                  and pj2.get("replayed") is True
                  and pj1["prompt"].get("version")
                  == pj2["prompt"].get("version")
                  and n_prompts == 4)
            record("K10 prompt replay: zero extra versions", ok,
                   f"versions={n_prompts}")
            rp_kb = _post(a, KB_ARTICLES,
                          {"category": "visitas", "question": kb_q,
                           "keywords": kb_kw, "answer": kb_a,
                           "idempotencyKey": "k10-kb"},
                          "wu16-k10-kidem")
            rp_kb2 = _post(a, KB_ARTICLES,
                           {"category": "visitas", "question": kb_q,
                            "keywords": kb_kw, "answer": kb_a,
                            "idempotencyKey": "k10-kb"},
                           "wu16-k10-kidem")
            kj1 = console_ok(rp_kb, "wu16-k10-kidem", "article")
            kj2 = console_ok(rp_kb2, "wu16-k10-kidem", "article")
            n_kb = asyncio.run(db.count(
                "SELECT count(*) AS n FROM knowledge_base_articles "
                "WHERE client_account_id = %s AND demo_seed_key IS NULL",
                (ACCOUNT_A,)))
            ok = (kj1 is not None and kj2 is not None
                  and kj2.get("replayed") is True
                  and n_kb == 2)
            record("K10 KB replay: zero extra articles", ok,
                   f"extra={n_kb}")
            rp_cal = _put(a, CAL_CONFIG, {"slot_minutes": 60,
                                          "idempotencyKey": "k10-cal"},
                          "wu16-k10-cidem")
            rp_cal2 = _put(a, CAL_CONFIG, {"slot_minutes": 60,
                                           "idempotencyKey": "k10-cal"},
                           "wu16-k10-cidem")
            cj1 = console_ok(rp_cal, "wu16-k10-cidem", "calendar_config")
            cj2 = console_ok(rp_cal2, "wu16-k10-cidem", "calendar_config")
            n_cal = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id = %s "
                "AND action = 'crm.agent.calendar.update'",
                ("wu16-k10-cidem",)))
            ok = (cj1 is not None and cj2 is not None
                  and cj2.get("replayed") is True and n_cal == 1)
            record("K10 calendar replay: zero extra effects", ok,
                   f"replayed={cj2.get('replayed') if cj2 else None}")
            rp_al = _put(a, LEAD_FIELDS,
                         {"allowed": ["stage_id", "status"],
                          "idempotencyKey": "k10-allow"},
                         "wu16-k10-aidem")
            rp_al2 = _put(a, LEAD_FIELDS,
                          {"allowed": ["stage_id", "status"],
                           "idempotencyKey": "k10-allow"},
                          "wu16-k10-aidem")
            aj1 = console_ok(rp_al, "wu16-k10-aidem", "allowlist")
            aj2 = console_ok(rp_al2, "wu16-k10-aidem", "allowlist")
            n_al = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE correlation_id = %s "
                "AND action = 'crm.agent.allowlist.update'",
                ("wu16-k10-aidem",)))
            ok = (aj1 is not None and aj2 is not None
                  and aj2.get("replayed") is True and n_al == 1)
            record("K10 allowlist replay: zero extra effects", ok,
                   f"replayed={aj2.get('replayed') if aj2 else None}")

            # K7. Forged / foreign / ghost ids non-enumerating.
            cross7 = _put(b, f"{KB_ARTICLES}/{art_id}",
                          {"answer": "intruso"}, "wu16-k7-cross")
            ghostp7 = _get(a, f"{PROMPTS}/9999", "wu16-k7-ghostp")
            ghosta7 = _post(a, f"{PROMPTS}/9999/revert", {},
                            "wu16-k7-ghostr")
            crossd7 = _get(b, f"{AGENT_DRAFTS}/{df9.get('draft_id')}",
                           "wu16-k7-crossd")
            ghostd7 = _get(a, f"{AGENT_DRAFTS}/{GHOST}", "wu16-k7-ghostd")
            ok = (envelope_ok(cross7, 404, "wu16-k7-cross",
                              "resource_not_found")
                  and envelope_ok(ghostp7, 404, "wu16-k7-ghostp",
                                  "resource_not_found")
                  and envelope_ok(ghosta7, 404, "wu16-k7-ghostr",
                                  "resource_not_found")
                  and envelope_ok(crossd7, 404, "wu16-k7-crossd",
                                  "resource_not_found")
                  and envelope_ok(ghostd7, 404, "wu16-k7-ghostd",
                                  "resource_not_found"))
            record("K7 forged/foreign/ghost ids non-enumerating (404)", ok,
                   f"cross={cross7.status_code} ghost={ghostp7.status_code}")

            # K8. The stub ran everything offline.
            ok = ("langchain_openai" not in sys.modules
                  and models == {"stub-keyword-v1"})
            record("K8 stub offline (no langchain import, stub provider)",
                   ok, f"models={models}")

            # Envelope probes on console + state/resume surface.
            none_app = TestClient(build_mvp_wu8_application(
                cfg, ACCOUNT_A, AbsentDemoIdentityAdapter()))
            with none_app as napp:
                r401s = _put(napp, SETTINGS, {"global_auto": True},
                             "wu16-401-s")
                r401r = _post(napp, AGENT_RESUME, {}, "wu16-401-r")
                record("401 without identity (settings+resume)", envelope_ok(
                    r401s, 401, "wu16-401-s", "authentication_required")
                    and envelope_ok(r401r, 401, "wu16-401-r",
                                    "authentication_required"),
                    f"settings={r401s.status_code} resume={r401r.status_code}")
            r404 = _get(a, "/v1/demo/agent/nope", "wu16-404")
            r405 = _post(a, SETTINGS, {}, "wu16-405")
            r405s = a.delete(AGENT_STATE,
                             headers={"X-Correlation-Id": "wu16-405s"})
            record("404 unknown agent route", envelope_ok(
                r404, 404, "wu16-404", "route_not_found"),
                f"status={r404.status_code}")
            record("405 wrong method on console+state", envelope_ok(
                r405, 405, "wu16-405", "method_not_allowed")
                and envelope_ok(r405s, 405, "wu16-405s",
                                "method_not_allowed"),
                f"settings={r405.status_code} state={r405s.status_code}")
            # End-of-run reconverge: leave the base in the pure seeded shape
            # (sibling console units count exact corpus sizes on this base).
            asyncio.run(preclean(db, fixture=False))
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_agent_console_wu16():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT CONSOLE WU16 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
