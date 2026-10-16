"""Agent custom field writes (change: agent-custom-field-writes, WU18) harness.

Runs against production_runtime_mvp_wu18 (disposable: runner 001-011 +
mvp_seed.sql + mvp_seed_005.sql + mvp_seed_008.sql + mvp_seed_009.sql +
mvp_seed_010.sql + mvp_seed_011.sql) with the default StubModelProvider (no
network, no API key, no langchain import). Every effect is generated
EXCLUSIVELY through the real agent router (signal-bindings CRUD, custom-fields
CRUD, lead-fields allowlist, analyze auto/manual, state, approve, drafts) --
never by hand-inserting rows. The one catalog fixture (a Palermo apartment,
account A) is cloned from the seeded PROP1 row in setup so search scenarios
resolve city+budget; preclean removes it every run.

Scenarios map to the design's Testing Strategy C1-C9 (task Phase 7 T1-T8):
T1 binding CRUD per account (create/list/upsert/delete; unknown signal 400;
ghost/foreign field 404 non-enumerating; cross-account invisible; deleting the
bound definition cascades the binding away)
T2 deterministic extraction (each of the 7 signals resolves from crafted
bodies/criteria with byte-exact values; byte-deterministic re-run replays
identical values; unresolvable emits no action, never guessed)
T3 AUTO end-to-end (bound + allowlisted + resolved signal auto-executes
set_custom_field_value through SetLeadCustomFieldValueCommand: value row
written, idempotent re-run upserts without duplicates, select-outside-choices
fails truthfully, number/date coercion enforced)
T4 allowlist gate (manual-mode draft approved after the custom name is removed
denies lead_field_not_allowed recorded truthfully in execution_result while the
saga finalizes; seeded stage_id+status transition still succeeds; undefined
custom name rejected 400 -- D4 closed)
T5 forged/foreign/ghost ids non-enumerating 404 on drafts + custom fields +
cross-account binding write
T6 no-binding implies no action (account B with zero bindings emits nothing
and writes nothing -- zero behavior change on the WU17 base)
T7 stub runs T1-T6 offline (no API key, langchain_openai never imported)
T8 401 without identity + 404 unknown route + 405 wrong method probes

Booking dates are RELATIVE to now (the WU14/WU15/WU16 date-aging lesson:
hardcoded 2026-09-* dates fall outside free_slots' [now, now+7d] window once
the clock moves past them). The fecha_objetivo signal carries a relative
Tuesday ISO date; no scenario books a visit, so no event-window lookup is
needed.

Idempotency note (WU14 A17 lesson): the analyze fallback key hashes
account|conversation|body, so every scenario needing fresh analysis uses a
DISTINCT body -- no body is ever reused across scenarios. Console writes carry
explicit idempotencyKey values (same payload without one replays).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.composition_wu8_mvp import build_mvp_wu8_application
from api.domain.calendar_event import BUSINESS_TIMEZONE
from api.identity.demo_identity import AbsentDemoIdentityAdapter, DemoIdentityAdapter

ROOT = Path(__file__).resolve().parent.parent
SEED_FILES = [ROOT / "infra" / "migrations" / "validation" / "mvp_seed.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_005.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_008.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_009.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_010.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_011.sql"]

ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
A_LEAD = "65000000-0000-0000-0000-000000000001"
B_LEAD = "65000000-0000-0000-0000-000000000002"
A_PROP_1 = "62000000-0000-0000-0000-000000000001"
PALERMO_PROP = "62000000-0000-0000-0000-000000000009"
CALIFICADO_STAGE = "64000000-0000-0000-0000-000000000003"
GHOST = "0000dead-0000-4000-8000-000000000000"

PHONE_BODY_VALUE = "+5491112345678"
EMAIL_VALUE = "sofiagarcia@example.com"

AGENT_ANALYZE = "/v1/demo/agent/analyze"
AGENT_DRAFTS = "/v1/demo/agent/drafts"
AGENT_STATE = "/v1/demo/agent/state"
AGENT_RESUME = "/v1/demo/agent/handoff/resume"
SETTINGS = "/v1/demo/agent/settings"
LEAD_FIELDS = "/v1/demo/agent/lead-fields"
CUSTOM_FIELDS = "/v1/demo/agent/custom-fields"
SIGNAL_BINDINGS = "/v1/demo/agent/signal-bindings"


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
    """Deterministic baseline: mirrors the WU17 preclean (008/009/010 owned
    rows kept via DROP-order care for RESTRICT FKs), plus the 011 binding
    table wiped before the custom-field definitions it references (CASCADE
    would clear them anyway; explicit order keeps the intent readable).
    run_seed re-inserts the owned 008/009/010/011 markers, so every run starts
    from identical defaults. With fixture=False (end-of-run reconverge) the
    Palermo scenario fixture is not re-cloned, leaving the pure seeded shape.
    Conversations are reset to auto (a manual pin from T4 would otherwise leak
    into the next run)."""
    for stmt in (
        "DELETE FROM calendar_events",
        "DELETE FROM agent_drafts",
        "DELETE FROM agent_policy_matrix",
        "DELETE FROM agent_settings",
        "DELETE FROM agent_prompts",
        "DELETE FROM agent_field_allowlist",
        "DELETE FROM agent_calendar_config",
        "DELETE FROM agent_signal_bindings",
        "DELETE FROM lead_custom_field_values",
        "DELETE FROM custom_field_definitions",
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
        "location, bedrooms, 195000, currency, availability, 'WU18-PALERMO', "
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


def _custom_value(client, lead_id, correlation):
    return _get(client, f"/v1/demo/agent/leads/{lead_id}/custom-values",
                correlation)


def _saga_params(draft: dict, entry_type: str):
    """(params, outcome, reason) per execution_result entry of one type."""
    actions = draft.get("proposed_actions") or []
    out = []
    for entry in (draft.get("execution_result") or []):
        if entry.get("type") != entry_type:
            continue
        params = {}
        index = entry.get("index")
        if isinstance(index, int) and 0 <= index < len(actions):
            params = (actions[index] or {}).get("params") or {}
        out.append((params, entry.get("outcome"), entry.get("reason")))
    return out


def _proposed_values(draft: dict):
    """{field_name: value} over proposed set_custom_field_value actions."""
    found = {}
    for action in (draft.get("proposed_actions") or []):
        if (action or {}).get("type") != "set_custom_field_value":
            continue
        params = (action or {}).get("params") or {}
        if params.get("field_name") and params.get("value") is not None:
            found[params["field_name"]] = params["value"]
    return found


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    assert cfg.get("dbname") == "production_runtime_mvp_wu18", (
        f"refusing to run on {cfg.get('dbname')}")
    assert not (sys.modules.get("langchain_openai")), "stub must stay import-free"
    # Relative dates (WU14/WU15/WU16 date-aging lesson): the fecha_objetivo
    # signal carries next Tuesday's ISO date, computed inside the booking
    # window horizon so validation/formatting stays realistic. Monday is the
    # next Monday strictly after today; Tuesday strictly after Monday.
    _now = __import__("datetime").datetime.now(timezone.utc)
    _tz = ZoneInfo(BUSINESS_TIMEZONE)
    _today = _now.astimezone(_tz).date()

    def _next_day(predicate):
        for n in range(1, 8):
            candidate = _today + timedelta(days=n)
            if predicate(candidate.weekday()):
                return candidate
        raise AssertionError("no matching day within the 7-day window")

    _mon = _next_day(lambda w: w == 0)

    _tue = None
    for n in range(1, 15):
        candidate = _mon + timedelta(days=n)
        if candidate.weekday() == 1:
            _tue = candidate
            break
    assert _tue is not None
    tue_iso = _tue.isoformat()
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))
        models: set[str] = set()
        wu8_a = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter()))
        wu8_b = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_B, DemoIdentityAdapter()))

        def _make_field(client, name, ftype, correlation, choices=None):
            body: dict = {"name": name, "type": ftype,
                          "idempotencyKey": correlation}
            if choices is not None:
                body["choices"] = choices
            resp = _post(client, CUSTOM_FIELDS, body, correlation)
            parsed = console_ok(resp, correlation, "custom_field")
            if parsed is None:
                return None, resp
            return parsed["custom_field"].get("resource_id"), resp

        def _bind(client, signal_key, field_id, correlation):
            return _post(client, SIGNAL_BINDINGS,
                         {"signal_key": signal_key,
                          "field_definition_id": field_id,
                          "idempotencyKey": correlation}, correlation)

        def _bindings_list(client, correlation):
            resp = _get(client, SIGNAL_BINDINGS, correlation)
            parsed = console_ok(resp, correlation, "signal_bindings")
            if parsed is None:
                return None, resp
            return parsed["signal_bindings"], resp

        with wu8_a as a, wu8_b as b:
            for _client, _corr in ((a, "wu18-pin-a"), (b, "wu18-pin-b")):
                _st = _get(_client, AGENT_STATE, _corr)
                assert _st.status_code == 200, (
                    f"auto pin failed: {_st.status_code} {_st.text[:200]}")
                assert _st.json().get("agent_mode") == "auto"

            # T1. Binding CRUD.
            f_tel, _r = _make_field(a, "telefono_contacto", "text",
                                    "wu18-f-tel")
            record("T1 create text field telefono_contacto returns 200",
                   f_tel is not None, f"id={str(f_tel)[:8] if f_tel else None}")
            b1 = _bind(a, "phone", f_tel, "wu18-t1-bind")
            sb1 = console_ok(b1, "wu18-t1-bind", "signal_binding")
            lst1, _ = _bindings_list(a, "wu18-t1-list")
            ok = (sb1 is not None
                  and sb1["signal_binding"].get("signal_key") == "phone"
                  and sb1["signal_binding"].get("field_name") == "telefono_contacto"
                  and sb1["signal_binding"].get("field_type") == "text"
                  and lst1 is not None and lst1.get("count") == 1
                  and lst1["items"][0].get("signal_key") == "phone")
            record("T1 bind phone->telefono + list shows it", ok,
                   f"count={lst1.get('count') if lst1 else None}")
            f_mail, _r = _make_field(a, "correo_contacto", "text",
                                      "wu18-f-mail")
            b1u = _bind(a, "phone", f_mail, "wu18-t1-upsert")
            lst1u, _ = _bindings_list(a, "wu18-t1-list2")
            phones = ([i for i in (lst1u.get("items") or [])
                       if i.get("signal_key") == "phone"] if lst1u else [])
            ok = (console_ok(b1u, "wu18-t1-upsert", "signal_binding") is not None
                  and len(phones) == 1
                  and phones[0].get("field_name") == "correo_contacto")
            record("T1 rebind phone->correo upserts (one row per key)", ok,
                   f"field={phones[0].get('field_name') if phones else None}")
            bad_sig = _post(a, SIGNAL_BINDINGS,
                            {"signal_key": "fax", "field_definition_id": f_tel},
                            "wu18-t1-badsig")
            del_sig = _delete(a, f"{SIGNAL_BINDINGS}/fax", "wu18-t1-delsig")
            ok = (envelope_ok(bad_sig, 400, "wu18-t1-badsig", "invalid_command")
                  and envelope_ok(del_sig, 400, "wu18-t1-delsig",
                                   "invalid_command"))
            record("T1 unknown signal_key rejected 400 (POST+DELETE)", ok,
                   f"post={bad_sig.status_code} delete={del_sig.status_code}")
            ghost_f = _post(a, SIGNAL_BINDINGS,
                            {"signal_key": "phone",
                             "field_definition_id": GHOST}, "wu18-t1-ghost")
            f_b, _r = _make_field(b, "campo_b", "text", "wu18-f-b")
            foreign_f = _bind(a, "phone", f_b, "wu18-t1-foreign")
            ok = (envelope_ok(ghost_f, 404, "wu18-t1-ghost",
                              "resource_not_found")
                  and envelope_ok(foreign_f, 404, "wu18-t1-foreign",
                                   "resource_not_found"))
            record("T1 ghost + foreign field_definition_id 404 non-enumerating",
                   ok, f"ghost={ghost_f.status_code} foreign={foreign_f.status_code}")
            lst_b, _ = _bindings_list(b, "wu18-t1-cross")
            ok = (lst_b is not None and lst_b.get("count") == 0
                  and lst_b.get("items") == [])
            record("T1 cross-account invisible (B list empty)", ok,
                   f"count={lst_b.get('count') if lst_b else None}")
            del_missing = _delete(a, f"{SIGNAL_BINDINGS}/email",
                                  "wu18-t1-delmissing")
            ok = envelope_ok(del_missing, 404, "wu18-t1-delmissing",
                             "resource_not_found")
            record("T1 delete of never-bound key 404", ok,
                   f"status={del_missing.status_code}")
            del1 = _delete(a, f"{SIGNAL_BINDINGS}/phone", "wu18-t1-del")
            dj1 = console_ok(del1, "wu18-t1-del", "signal_binding")
            lst1d, _ = _bindings_list(a, "wu18-t1-list3")
            phones_d = ([i for i in (lst1d.get("items") or [])
                         if i.get("signal_key") == "phone"] if lst1d else [])
            ok = (dj1 is not None and dj1["signal_binding"].get("deleted") is True
                  and phones_d == [])
            record("T1 delete binding returns deleted:true, list drops it",
                   ok, f"deleted={dj1['signal_binding'].get('deleted') if dj1 else None}")
            f_casc, _r = _make_field(a, "campo_cascada", "text",
                                      "wu18-f-casc")
            _bind(a, "email", f_casc, "wu18-t1-cascbind")
            pre_casc, _ = _bindings_list(a, "wu18-t1-cascpre")
            del_casc = _delete(a, f"{CUSTOM_FIELDS}/{f_casc}",
                               "wu18-t1-cascfield")
            post_casc, _ = _bindings_list(a, "wu18-t1-cascpost")
            mails = ([i for i in (post_casc.get("items") or [])
                      if i.get("signal_key") == "email"] if post_casc else [])
            ok = (pre_casc is not None
                  and any(i.get("signal_key") == "email"
                          for i in (pre_casc.get("items") or []))
                  and del_casc.status_code == 200 and mails == [])
            record("T1 deleting bound definition cascades binding away", ok,
                   f"email_bindings={len(mails)}")

            # T2. Deterministic extraction: 7 bindings + allowlisted names.
            f_pres, _r = _make_field(a, "presupuesto_texto", "text",
                                     "wu18-f-pres")
            f_barrio, _r = _make_field(a, "barrio_texto", "text",
                                       "wu18-f-barrio")
            f_tipo, _r = _make_field(a, "tipo_texto", "text", "wu18-f-tipo")
            f_fecha, _r = _make_field(a, "fecha_texto", "text",
                                      "wu18-f-fecha")
            f_canal, _r = _make_field(a, "canal_texto", "text",
                                      "wu18-f-canal")
            f_monto, _r = _make_field(a, "monto_numero", "number",
                                      "wu18-f-monto")
            f_fecha2, _r = _make_field(a, "fecha_calendario", "date",
                                       "wu18-f-fecha2")
            f_csel, _r = _make_field(a, "canal_select", "select",
                                     "wu18-f-csel", choices=["email"])
            for sig, fid, corr in (
                    ("phone", f_tel, "wu18-t2-bphone"),
                    ("email", f_mail, "wu18-t2-bemail"),
                    ("presupuesto_max", f_pres, "wu18-t2-bpres"),
                    ("barrio", f_barrio, "wu18-t2-bbarrio"),
                    ("tipo_propiedad", f_tipo, "wu18-t2-btipo"),
                    ("fecha_objetivo", f_fecha, "wu18-t2-bfecha"),
                    ("medio_de_contacto", f_canal, "wu18-t2-bcanal")):
                _bind(a, sig, fid, corr)
            allowed7 = ["stage_id", "status", "telefono_contacto",
                        "correo_contacto", "presupuesto_texto", "barrio_texto",
                        "tipo_texto", "fecha_texto", "canal_texto"]
            al7 = _put(a, LEAD_FIELDS, {"allowed": allowed7,
                                        "idempotencyKey": "k18-allow7"},
                       "wu18-t2-allow")
            row7 = asyncio.run(db.one(
                "SELECT field_definition_id FROM agent_field_allowlist "
                "WHERE client_account_id = %s AND column_name = %s",
                (ACCOUNT_A, "telefono_contacto")))
            ok = (console_ok(al7, "wu18-t2-allow", "allowlist") is not None
                  and row7 is not None
                  and row7["field_definition_id"] is not None)
            record("T2 allowlist accepts defined custom names (D4, stores ref)",
                   ok, f"ref={str(row7['field_definition_id'])[:8] if row7 and row7['field_definition_id'] else None}")
            an_phone = _analyze(
                a, "busco un depto en Palermo hasta 200000, mi teléfono es "
                   "+54 9 11 1234-5678, por favor", "wu18-t2-phone")
            dp = (an_phone.json().get("draft") or {})
            models.add(dp.get("model_name"))
            pv = _proposed_values(dp)
            ok = (an_phone.status_code == 200
                  and pv.get("telefono_contacto") == PHONE_BODY_VALUE
                  and pv.get("canal_texto") == "phone")
            record("T2 phone + medio=phone resolve byte-exact from body", ok,
                   f"phone={pv.get('telefono_contacto')} canal={pv.get('canal_texto')}")
            an_mail = _analyze(
                a, "busco un depto en Palermo hasta 200000, mi correo es "
                   "sofiagarcia@example.com, genial", "wu18-t2-mail")
            dm = (an_mail.json().get("draft") or {})
            models.add(dm.get("model_name"))
            mv = _proposed_values(dm)
            ok = (an_mail.status_code == 200
                  and mv.get("correo_contacto") == EMAIL_VALUE
                  and mv.get("canal_texto") == "email")
            record("T2 email + medio=email resolve byte-exact from body", ok,
                   f"email={mv.get('correo_contacto')} canal={mv.get('canal_texto')}")
            an_crit = _analyze(
                a, "busco un depto en Palermo hasta 200000, perfecto",
                "wu18-t2-crit")
            dc = (an_crit.json().get("draft") or {})
            models.add(dc.get("model_name"))
            cv = _proposed_values(dc)
            ok = (an_crit.status_code == 200
                  and cv.get("presupuesto_texto") == "200000"
                  and cv.get("barrio_texto") == "Palermo"
                  and cv.get("tipo_texto") == "apartment")
            record("T2 presupuesto/barrio/tipo resolve from criteria", ok,
                   f"pres={cv.get('presupuesto_texto')} barrio={cv.get('barrio_texto')}")
            an_fecha = _analyze(
                a, f"busco un depto en Palermo para el {tue_iso}, excelente",
                "wu18-t2-fecha")
            df = (an_fecha.json().get("draft") or {})
            models.add(df.get("model_name"))
            fv = _proposed_values(df)
            ok = (an_fecha.status_code == 200
                  and fv.get("fecha_texto") == tue_iso)
            record("T2 fecha_objetivo resolves relative Tuesday ISO", ok,
                   f"fecha={fv.get('fecha_texto')}")
            an_replay = _analyze(
                a, "busco un depto en Palermo hasta 200000, mi teléfono es "
                   "+54 9 11 1234-5678, por favor", "wu18-t2-replay")
            jr = an_replay.json() if an_replay.status_code == 200 else {}
            dr = (jr.get("draft") or {})
            models.add(dr.get("model_name"))
            ok = (an_replay.status_code == 200 and jr.get("replayed") is True
                  and _proposed_values(dr).get("telefono_contacto")
                  == PHONE_BODY_VALUE)
            record("T2 byte-deterministic re-run replays identical values",
                   ok, f"replayed={jr.get('replayed')}")
            an_absent = _analyze(a, "¿Cuánto tarda la escritura?",
                                 "wu18-t2-absent")
            da = (an_absent.json().get("draft") or {})
            models.add(da.get("model_name"))
            ok = (an_absent.status_code == 200
                  and _proposed_values(da) == {})
            record("T2 unresolvable emits no action (never guessed)", ok,
                   f"actions={len(da.get('proposed_actions') or [])}")

            # T3. AUTO end-to-end through SetLeadCustomFieldValueCommand.
            an_auto = _analyze(
                a, "busco un depto en Palermo hasta 200000, mi teléfono es "
                   "+54 9 11 1234-5678, gracias", "wu18-t3-auto")
            ja = an_auto.json() if an_auto.status_code == 200 else {}
            dauto = (ja.get("draft") or {})
            models.add(dauto.get("model_name"))
            tel_entries = [e for e in _saga_params(dauto, "set_custom_field_value")
                           if e[0].get("field_name") == "telefono_contacto"]
            row_auto = asyncio.run(db.one(
                "SELECT value FROM lead_custom_field_values "
                "WHERE client_account_id = %s AND lead_id = %s AND field_id = %s",
                (ACCOUNT_A, A_LEAD, f_tel)))
            ok = (an_auto.status_code == 200 and ja.get("auto_executed") is True
                  and dauto.get("status") == "executed"
                  and len(tel_entries) == 1 and tel_entries[0][1] == "success"
                  and row_auto is not None
                  and row_auto["value"] == PHONE_BODY_VALUE)
            record("T3 auto executes write, value row holds phone", ok,
                   f"outcome={tel_entries[0][1] if tel_entries else None}")
            an_auto2 = _analyze(
                a, "busco un depto en Palermo hasta 200000, mi teléfono es "
                   "+54 9 11 1234-5678, gracias", "wu18-t3-replay")
            j2 = an_auto2.json() if an_auto2.status_code == 200 else {}
            n_rows = asyncio.run(db.count(
                "SELECT count(*) AS n FROM lead_custom_field_values "
                "WHERE client_account_id = %s AND lead_id = %s AND field_id = %s",
                (ACCOUNT_A, A_LEAD, f_tel)))
            ok = (an_auto2.status_code == 200 and j2.get("replayed") is True
                  and n_rows == 1)
            record("T3 idempotent re-run upserts, no duplicate row", ok,
                   f"rows={n_rows}")
            allowed_sel = allowed7 + ["canal_select"]
            _put(a, LEAD_FIELDS, {"allowed": allowed_sel,
                                  "idempotencyKey": "k18-allowsel"},
                 "wu18-t3-allowsel")
            _bind(a, "medio_de_contacto", f_csel, "wu18-t3-bsel")
            an_sel = _analyze(
                a, "busco un depto en Palermo hasta 200000, llamame al "
                   "+54 9 11 1234-5678, te lo agradezco", "wu18-t3-sel")
            ds = (an_sel.json().get("draft") or {}) if an_sel.status_code == 200 else {}
            models.add(ds.get("model_name"))
            sel_entries = [e for e in _saga_params(ds, "set_custom_field_value")
                           if e[0].get("field_name") == "canal_select"]
            ok = (an_sel.status_code == 200 and len(sel_entries) == 1
                  and sel_entries[0][1] == "invalid"
                  and "choices" in str(sel_entries[0][2] or ""))
            record("T3 select outside choices fails truthfully (invalid)", ok,
                   f"outcome={sel_entries[0][1] if sel_entries else None}")
            allowed_nd = allowed_sel + ["monto_numero", "fecha_calendario"]
            _put(a, LEAD_FIELDS, {"allowed": allowed_nd,
                                  "idempotencyKey": "k18-allownd"},
                 "wu18-t3-allownd")
            _bind(a, "presupuesto_max", f_monto, "wu18-t3-bmonto")
            _bind(a, "fecha_objetivo", f_fecha2, "wu18-t3-bfecha2")
            an_nd = _analyze(
                a, f"busco un depto en Palermo hasta 200000 para el {tue_iso}, "
                   "muy amable", "wu18-t3-numdate")
            dn = (an_nd.json().get("draft") or {}) if an_nd.status_code == 200 else {}
            models.add(dn.get("model_name"))
            row_monto = asyncio.run(db.one(
                "SELECT value FROM lead_custom_field_values "
                "WHERE client_account_id = %s AND lead_id = %s AND field_id = %s",
                (ACCOUNT_A, A_LEAD, f_monto)))
            row_fecha = asyncio.run(db.one(
                "SELECT value FROM lead_custom_field_values "
                "WHERE client_account_id = %s AND lead_id = %s AND field_id = %s",
                (ACCOUNT_A, A_LEAD, f_fecha2)))
            ok = (an_nd.status_code == 200
                  and row_monto is not None and row_monto["value"] == "200000"
                  and row_fecha is not None and row_fecha["value"] == tue_iso)
            record("T3 number/date coercion enforced (single authority)", ok,
                   f"monto={row_monto['value'] if row_monto else None}")

            # T4. Allowlist gate + D4 close.
            an_tr = _analyze(a, "busco un depto en Palermo hasta 200000, "
                                "saludos cordiales", "wu18-t4-trans")
            jt = an_tr.json() if an_tr.status_code == 200 else {}
            dt = (jt.get("draft") or {})
            models.add(dt.get("model_name"))
            tr_entries = [e for e in _saga_params(dt, "transition_lead")]
            lead_stage = asyncio.run(db.one(
                "SELECT stage_id FROM leads WHERE id = %s", (A_LEAD,)))
            ok = (an_tr.status_code == 200
                  and any(e[1] == "success" for e in tr_entries)
                  and lead_stage is not None
                  and str(lead_stage["stage_id"]) == CALIFICADO_STAGE)
            record("T4 seeded stage_id+status transition still succeeds (C6)",
                   ok, f"stage={str(lead_stage['stage_id'])[:8] if lead_stage else None}")
            bad_allow = _put(a, LEAD_FIELDS, {"allowed": ["stage_id",
                                                          "campo_fantasma"]},
                             "wu18-t4-badname")
            ok = envelope_ok(bad_allow, 400, "wu18-t4-badname",
                             "invalid_command")
            record("T4 undefined custom name rejected 400", ok,
                   f"status={bad_allow.status_code}")
            pin_m = _set_mode(a, "manual", "wu18-t4-pin")
            assert pin_m.status_code == 200
            an_man = _analyze(
                a, "busco un depto en Palermo hasta 200000, mi teléfono es "
                   "+54 9 11 1234-5678, quedo atento", "wu18-t4-manual")
            jm = an_man.json() if an_man.status_code == 200 else {}
            dman = (jm.get("draft") or {})
            models.add(dman.get("model_name"))
            ok_manual = (an_man.status_code == 200
                         and jm.get("auto_executed") is False
                         and dman.get("status") == "proposed"
                         and "telefono_contacto" in _proposed_values(dman))
            pre_val = asyncio.run(db.one(
                "SELECT value FROM lead_custom_field_values "
                "WHERE client_account_id = %s AND lead_id = %s AND field_id = %s",
                (ACCOUNT_A, A_LEAD, f_tel)))
            narrow = _put(a, LEAD_FIELDS, {"allowed": ["stage_id", "status"],
                                           "idempotencyKey": "k18-narrow"},
                          "wu18-t4-narrow")
            assert console_ok(narrow, "wu18-t4-narrow", "allowlist") is not None
            ap = _post(a, f"{AGENT_DRAFTS}/{dman.get('draft_id')}/approve",
                       {}, "wu18-t4-approve")
            jap = ap.json() if ap.status_code == 200 else {}
            dap = (jap.get("draft") or {})
            deny = [e for e in _saga_params(dap, "set_custom_field_value")
                    if e[0].get("field_name") == "telefono_contacto"]
            post_val = asyncio.run(db.one(
                "SELECT value FROM lead_custom_field_values "
                "WHERE client_account_id = %s AND lead_id = %s AND field_id = %s",
                (ACCOUNT_A, A_LEAD, f_tel)))
            ok = (ok_manual and ap.status_code == 200
                  and dap.get("status") == "executed" and len(deny) == 1
                  and deny[0][1] == "denied"
                  and deny[0][2] == "lead_field_not_allowed"
                  and (post_val or {}).get("value") == (pre_val or {}).get("value"))
            record("T4 narrowed allowlist denies write, saga finalizes", ok,
                   f"denial={deny[0][1:] if deny else None}")
            restore = _put(a, LEAD_FIELDS, {"allowed": allowed_nd,
                                            "idempotencyKey": "k18-restore"},
                           "wu18-t4-restore")
            unpin = _set_mode(a, "auto", "wu18-t4-unpin")
            ok = (console_ok(restore, "wu18-t4-restore", "allowlist") is not None
                  and unpin.status_code == 200)
            record("T4 allowlist + auto mode restored", ok, "")

            # T5. Forged / foreign / ghost ids non-enumerating.
            cross_d = _get(b, f"{AGENT_DRAFTS}/{dauto.get('draft_id')}",
                           "wu18-t5-crossd")
            ghost_d = _get(a, f"{AGENT_DRAFTS}/{GHOST}", "wu18-t5-ghostd")
            ghost_cf = _put(a, f"{CUSTOM_FIELDS}/{GHOST}",
                            {"name": "fantasma"}, "wu18-t5-ghostcf")
            ghost_cfd = _delete(a, f"{CUSTOM_FIELDS}/{GHOST}",
                                "wu18-t5-ghostcfd")
            ghost_cv = _custom_value(a, GHOST, "wu18-t5-ghostcv")
            cross_bind = _bind(b, "phone", f_tel, "wu18-t5-crossbind")
            ok = (envelope_ok(cross_d, 404, "wu18-t5-crossd",
                              "resource_not_found")
                  and envelope_ok(ghost_d, 404, "wu18-t5-ghostd",
                                   "resource_not_found")
                  and envelope_ok(ghost_cf, 404, "wu18-t5-ghostcf",
                                   "resource_not_found")
                  and envelope_ok(ghost_cfd, 404, "wu18-t5-ghostcfd",
                                   "resource_not_found")
                  and envelope_ok(ghost_cv, 404, "wu18-t5-ghostcv",
                                   "resource_not_found")
                  and envelope_ok(cross_bind, 404, "wu18-t5-crossbind",
                                   "resource_not_found"))
            record("T5 forged/foreign/ghost ids non-enumerating (404)", ok,
                   f"cross={cross_d.status_code} ghost={ghost_d.status_code}")

            # T6. Zero bindings implies zero behavior change (account B).
            an_b = _analyze(
                b, "busco un depto en Palermo hasta 200000, mi teléfono es "
                   "+54 9 11 1234-5678, un abrazo", "wu18-t6-b")
            jb = an_b.json() if an_b.status_code == 200 else {}
            dbb = (jb.get("draft") or {})
            models.add(dbb.get("model_name"))
            n_b = asyncio.run(db.count(
                "SELECT count(*) AS n FROM lead_custom_field_values "
                "WHERE client_account_id = %s AND lead_id = %s",
                (ACCOUNT_B, B_LEAD)))
            ok = (an_b.status_code == 200
                  and _proposed_values(dbb) == {} and n_b == 0)
            record("T6 no-binding implies no action, nothing written (B)", ok,
                   f"values={n_b}")

            # T7. The stub ran everything offline.
            ok = ("langchain_openai" not in sys.modules
                  and models == {"stub-keyword-v1"})
            record("T7 stub offline (no langchain import, stub provider)",
                   ok, f"models={models}")

            # T8. Envelope probes on console + state/resume surface.
            none_app = TestClient(build_mvp_wu8_application(
                cfg, ACCOUNT_A, AbsentDemoIdentityAdapter()))
            with none_app as napp:
                r401s = _put(napp, SETTINGS, {"global_auto": True},
                             "wu18-401-s")
                r401r = _post(napp, AGENT_RESUME, {}, "wu18-401-r")
                r401b = _get(napp, SIGNAL_BINDINGS, "wu18-401-b")
                record("T8 401 without identity (settings+resume+bindings)",
                       envelope_ok(r401s, 401, "wu18-401-s",
                                   "authentication_required")
                       and envelope_ok(r401r, 401, "wu18-401-r",
                                       "authentication_required")
                       and envelope_ok(r401b, 401, "wu18-401-b",
                                       "authentication_required"),
                       f"settings={r401s.status_code} resume={r401r.status_code}")
            r404 = _get(a, "/v1/demo/agent/nope", "wu18-404")
            r405 = _post(a, SETTINGS, {}, "wu18-405")
            r405s = a.delete(AGENT_STATE,
                             headers={"X-Correlation-Id": "wu18-405s"})
            record("T8 404 unknown route + 405 wrong method", envelope_ok(
                r404, 404, "wu18-404", "route_not_found")
                and envelope_ok(r405, 405, "wu18-405", "method_not_allowed")
                and envelope_ok(r405s, 405, "wu18-405s",
                                "method_not_allowed"),
                f"r404={r404.status_code} r405={r405.status_code}")
            # End-of-run reconverge: leave the base in the pure seeded shape.
            asyncio.run(preclean(db, fixture=False))
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_agent_custom_field_writes():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT CUSTOM FIELD WRITES WU18 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
