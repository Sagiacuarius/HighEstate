"""Configuration (change: configuration, WU17) real-PostgreSQL harness.

Runs against production_runtime_mvp_wu17 (disposable: runner 001-010 +
mvp_seed.sql + mvp_seed_005.sql + mvp_seed_008.sql + mvp_seed_009.sql +
mvp_seed_010.sql) with the StubModelProvider (no network, no API key, no
langchain import). Effects are generated EXCLUSIVELY through the real agent
router (`PUT/GET /agent/calendar-config`, `/agent/custom-fields`,
`/agent/leads/{id}/custom-values`, `/agent/contacts...`, `/agent/lead-fields`)
plus the WU13 availability read — never by hand-inserting rows.

Scenarios map to the design's Testing Strategy T1-T7 (T8 = Phase 9 regression
sweep of the WU14/WU15/WU16 harnesses on their own bases, run as separate
commands — the sibling harnesses assert their own RUNTIME_DB_NAME and refuse
to run here):
T1 timezone round-trip (seeded BA -> New_York shifts the same instant's local
   hour, UI availability == agent free_slots, invalid IANA 400, restore BA)
T2 custom-field definition create/edit/delete + value round-trip
T3 allowlist surface (seeded {stage_id,status}; custom-field name rejected by
   the write path, undefined name rejected, fixed round-trip intact)
T4 value coercion/validation (number/date/select in-choices/select-outside/
   undefined field)
T5 contact tags add/remove/dedupe/case-normalize + `?tag=` filter
T6 forged/ghost/foreign ids non-enumerating on every new route
T7 the stub ran everything offline (`langchain_openai` never imported)

Dates are computed RELATIVE to now (now + N days), never absolute — the WU16
date-aging lesson (a hardcoded 2026-09-* date fell outside the free_slots
window once the system clock moved past it).

Idempotency note: console writes derive `console:{account}:{entity}:{key}`;
preclean wipes command_idempotency + custom-field rows each run, so stable
names/keys re-apply deterministically. Value upserts carry no client key so
each distinct (lead, field, value) triple is its own idempotent write.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_agent_console import load_calendar_config
from api.adapters.postgres_agent_drafts import free_slots
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.composition_wu8_mvp import build_mvp_wu8_application
from api.domain.calendar_event import (
    BUSINESS_DAYS,
    BUSINESS_START_HOUR,
    BUSINESS_TIMEZONE,
    business_slot_starts,
)
from api.identity.demo_identity import AbsentDemoIdentityAdapter, DemoIdentityAdapter
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    Membership,
    TenantContext,
    User,
)

ROOT = Path(__file__).resolve().parent.parent
SEED_FILES = [ROOT / "infra" / "migrations" / "validation" / "mvp_seed.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_005.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_008.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_009.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_010.sql"]

ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
A_CONTACT = "61000000-0000-0000-0000-000000000001"
B_CONTACT = "61000000-0000-0000-0000-000000000002"
A_LEAD = "65000000-0000-0000-0000-000000000001"
B_LEAD = "65000000-0000-0000-0000-000000000002"
GHOST = "0000dead-0000-4000-8000-000000000000"
NEW_YORK = "America/New_York"

AGENCY = "00000000-0000-0000-0000-000000000001"
USER = "10000000-0000-0000-0000-000000000001"
PERMS = frozenset({"client_account:read", "client_account:write"})

CAL_CONFIG = "/v1/demo/agent/calendar-config"
CUSTOM_FIELDS = "/v1/demo/agent/custom-fields"
LEAD_FIELDS = "/v1/demo/agent/lead-fields"
AVAILABILITY = "/v1/demo/calendar/availability"


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=tag,
    )


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
    """Deterministic baseline: mirrors the WU16 preclean (008/009 owned rows
    kept via DROP-order care for RESTRICT FKs), then wipes the 010 custom-field
    tables and re-seeds everything (010 converges the two calendar rows back to
    BUSINESS_TIMEZONE). Every run starts from identical defaults."""
    for stmt in (
        "DELETE FROM calendar_events",
        "DELETE FROM agent_drafts",
        "DELETE FROM agent_policy_matrix",
        "DELETE FROM agent_settings",
        "DELETE FROM agent_prompts",
        "DELETE FROM agent_field_allowlist",
        "DELETE FROM agent_calendar_config",
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


def _delete_body(client, route, body, correlation):
    return client.request("DELETE", route, json=body,
                          headers={"X-Correlation-Id": correlation})


def _custom_values_url(lead_id: str) -> str:
    return f"/v1/demo/agent/leads/{lead_id}/custom-values"


def _tags_url(contact_id: str) -> str:
    return f"/v1/demo/agent/contacts/{contact_id}/tags"


def _next_working_day(now: datetime) -> datetime.date:
    day = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date() + timedelta(days=1)
    while day.weekday() not in BUSINESS_DAYS:
        day += timedelta(days=1)
    return day


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    assert cfg.get("dbname") == "production_runtime_mvp_wu17", (
        f"refusing to run on {cfg.get('dbname')}")
    assert not (sys.modules.get("langchain_openai")), "stub must stay import-free"
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        wu8_a = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter()))

        with wu8_a as a:
            now = datetime.now(timezone.utc)

            # --- T1 timezone round-trip ------------------------------------
            g1 = _get(a, CAL_CONFIG, "wu17-t1-get")
            sc1 = console_ok(g1, "wu17-t1-get", "calendar_config")
            ok = (sc1 is not None
                  and sc1["calendar_config"].get("timezone") == BUSINESS_TIMEZONE)
            record("T1 seeded timezone == BUSINESS_TIMEZONE", ok,
                   f"tz={sc1['calendar_config'].get('timezone') if sc1 else None}")

            bad1 = _put(a, CAL_CONFIG, {"timezone": "Not/AZone"}, "wu17-t1-badtz")
            record("T1 invalid IANA timezone fails closed 400",
                   envelope_ok(bad1, 400, "wu17-t1-badtz", "invalid_command"),
                   f"status={bad1.status_code}")

            pt1 = _put(a, CAL_CONFIG, {"timezone": NEW_YORK}, "wu17-t1-ny")
            sct = console_ok(pt1, "wu17-t1-ny", "calendar_config")
            ok = (sct is not None
                  and sct["calendar_config"].get("timezone") == NEW_YORK)
            record("T1 PUT timezone round-trips to America/New_York", ok,
                   f"tz={sct['calendar_config'].get('timezone') if sct else None}")

            window_to = now + timedelta(days=3)
            from_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            to_iso = window_to.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            avail = _get(a, f"{AVAILABILITY}?from={from_iso}&to={to_iso}",
                         "wu17-t1-avail")
            ui = (avail.json().get("items") or []) if avail.status_code == 200 else []
            ui_starts = {datetime.fromisoformat(it["starts_at"].replace("Z", "+00:00"))
                         for it in ui}
            ctx_a = _ctx(ACCOUNT_A, "wu17-t1")
            config = asyncio.run(load_calendar_config(ctx_a, runner))
            agent_slots = asyncio.run(free_slots(
                ctx_a, runner, from_dt=now, to_dt=window_to, calendar=config))
            agent_starts = {s for s, _ in agent_slots}

            day = _next_working_day(now)
            ny_hour = business_slot_starts(day)[0].astimezone(
                ZoneInfo(NEW_YORK)).hour
            record("T1 same instant maps to a different local hour",
                   ny_hour != BUSINESS_START_HOUR, f"NY hour={ny_hour}")
            record("T1 UI availability == agent free_slots (seam)",
                   bool(ui_starts) and ui_starts == agent_starts,
                   f"ui={len(ui_starts)} agent={len(agent_starts)}")

            rt1 = _put(a, CAL_CONFIG, {"timezone": BUSINESS_TIMEZONE},
                       "wu17-t1-restore")
            scr = console_ok(rt1, "wu17-t1-restore", "calendar_config")
            record("T1 restore to BUSINESS_TIMEZONE",
                   scr is not None and scr["calendar_config"].get("timezone")
                   == BUSINESS_TIMEZONE,
                   f"tz={scr['calendar_config'].get('timezone') if scr else None}")

            # --- T2 custom-field definition CRUD + value round-trip --------
            badtype = _post(a, CUSTOM_FIELDS,
                            {"name": "tipo-raro", "type": "bool",
                             "idempotencyKey": "wu17-t2-badtype"},
                            "wu17-t2-badtype")
            record("T2 unknown type rejected 400",
                   envelope_ok(badtype, 400, "wu17-t2-badtype",
                               "invalid_command"),
                   f"status={badtype.status_code}")

            c2 = _post(a, CUSTOM_FIELDS,
                       {"name": "interes-demo", "type": "select",
                        "choices": ["bajo", "medio", "alto"],
                        "idempotencyKey": "wu17-t2-select"},
                       "wu17-t2-create")
            pc2 = console_ok(c2, "wu17-t2-create", "custom_field")
            field_id = pc2["custom_field"].get("resource_id") if pc2 else None
            ok = (pc2 is not None and pc2.get("replayed") is False
                  and pc2["custom_field"].get("name") == "interes-demo"
                  and pc2["custom_field"].get("type") == "select"
                  and pc2["custom_field"].get("choices") == ["bajo", "medio", "alto"]
                  and field_id is not None)
            record("T2 create select definition round-trips", ok,
                   f"type={pc2['custom_field'].get('type') if pc2 else None}")

            gl2 = _get(a, CUSTOM_FIELDS, "wu17-t2-list")
            lc2 = console_ok(gl2, "wu17-t2-list", "custom_fields")
            names2 = {it["name"] for it in lc2["custom_fields"]["items"]} if lc2 else set()
            record("T2 list includes the created definition",
                   "interes-demo" in names2, f"count={len(names2)}")

            up2 = _put(a, f"{CUSTOM_FIELDS}/{field_id}",
                       {"name": "interes-v2-demo", "idempotencyKey": "wu17-t2-upd"},
                       "wu17-t2-update")
            pu2 = console_ok(up2, "wu17-t2-update", "custom_field")
            ok = (pu2 is not None
                  and pu2["custom_field"].get("name") == "interes-v2-demo")
            record("T2 edit renames the definition", ok,
                   f"name={pu2['custom_field'].get('name') if pu2 else None}")

            sv2 = _put(a, _custom_values_url(A_LEAD),
                       {"values": {"interes-v2-demo": "medio"}}, "wu17-t2-setval")
            pv2 = console_ok(sv2, "wu17-t2-setval", "custom_values")
            vals2 = pv2["custom_values"]["values"] if pv2 else []
            by_name2 = {v["name"]: v for v in vals2}
            ok = (pv2 is not None and "interes-v2-demo" in by_name2
                  and by_name2["interes-v2-demo"]["value"] == "medio"
                  and by_name2["interes-v2-demo"]["type"] == "select")
            record("T2 value round-trip (select value written + read)", ok,
                   f"values={[v['name'] for v in vals2]}")

            del2 = _delete(a, f"{CUSTOM_FIELDS}/{field_id}", "wu17-t2-delete")
            pd2 = console_ok(del2, "wu17-t2-delete", "custom_field")
            gl2b = _get(a, CUSTOM_FIELDS, "wu17-t2-list2")
            lc2b = console_ok(gl2b, "wu17-t2-list2", "custom_fields")
            names2b = {it["name"] for it in lc2b["custom_fields"]["items"]} if lc2b else set()
            gv2b = _get(a, _custom_values_url(A_LEAD), "wu17-t2-val2")
            pv2b = console_ok(gv2b, "wu17-t2-val2", "custom_values")
            vals2b = [v["name"] for v in (pv2b["custom_values"]["values"] if pv2b else [])]
            ok = (pd2 is not None and "interes-v2-demo" not in names2b
                  and "interes-v2-demo" not in vals2b)
            record("T2 delete clears definition + its values", ok,
                   f"remaining={sorted(names2b)}")

            # --- T3 allowlist surface --------------------------------------
            g3 = _get(a, LEAD_FIELDS, "wu17-t3-get")
            sg3 = console_ok(g3, "wu17-t3-get", "allowlist")
            ok = (sg3 is not None
                  and sg3["allowlist"].get("allowed") == ["stage_id", "status"])
            record("T3 seeded allowlist is stage_id + status", ok,
                   f"allowed={sg3['allowlist'].get('allowed') if sg3 else None}")

            rt3 = _put(a, LEAD_FIELDS,
                       {"allowed": ["stage_id", "status"],
                        "idempotencyKey": "wu17-t3-roundtrip"},
                       "wu17-t3-roundtrip")
            ok = console_ok(rt3, "wu17-t3-roundtrip", "allowlist") is not None
            record("T3 fixed allowlist round-trips", ok, "")

            c3 = _post(a, CUSTOM_FIELDS,
                       {"name": "presupuesto-demo", "type": "number",
                        "idempotencyKey": "wu17-t3-cf"},
                       "wu17-t3-cf")
            pc3 = console_ok(c3, "wu17-t3-cf", "custom_field")
            custom3 = pc3["custom_field"].get("name") if pc3 else None
            allow_custom = _put(a, LEAD_FIELDS,
                                {"allowed": ["stage_id", "status", custom3]},
                                "wu17-t3-custom")
            record("T3 allowlist custom-field name rejected by write path (D1)",
                   envelope_ok(allow_custom, 400, "wu17-t3-custom",
                               "invalid_command"),
                   f"status={allow_custom.status_code}")

            undef3 = _put(a, LEAD_FIELDS, {"allowed": ["campo-inexistente"]},
                          "wu17-t3-undef")
            record("T3 undefined allowlist name rejected 400",
                   envelope_ok(undef3, 400, "wu17-t3-undef", "invalid_command"),
                   f"status={undef3.status_code}")

            # --- T4 value coercion/validation matrix -----------------------
            cn4 = _post(a, CUSTOM_FIELDS,
                        {"name": "cierre-demo", "type": "date",
                         "idempotencyKey": "wu17-t4-date"},
                        "wu17-t4-date")
            cs4 = _post(a, CUSTOM_FIELDS,
                        {"name": "interes4-demo", "type": "select",
                         "choices": ["bajo", "medio", "alto"],
                         "idempotencyKey": "wu17-t4-select"},
                        "wu17-t4-select")
            pc4s = console_ok(cs4, "wu17-t4-select", "custom_field")

            def _value(name, value, corr):
                return _put(a, _custom_values_url(A_LEAD),
                            {"values": {name: value}}, corr)

            n4 = _value("presupuesto-demo", 195000, "wu17-t4-num")
            pn4 = console_ok(n4, "wu17-t4-num", "custom_values")
            vnum = {v["name"]: v for v in (pn4["custom_values"]["values"] if pn4 else [])}
            record("T4 number coerced to canonical string",
                   pn4 is not None and vnum.get("presupuesto-demo", {}).get("value")
                   == "195000",
                   f"value={vnum.get('presupuesto-demo', {}).get('value')}")

            badn4 = _value("presupuesto-demo", "abc", "wu17-t4-numbad")
            record("T4 non-numeric number value rejected 400",
                   envelope_ok(badn4, 400, "wu17-t4-numbad", "invalid_command"),
                   f"status={badn4.status_code}")

            dval = (now + timedelta(days=5)).date().isoformat()
            d4 = _value("cierre-demo", dval, "wu17-t4-dateval")
            pd4 = console_ok(d4, "wu17-t4-dateval", "custom_values")
            vdate = {v["name"]: v for v in (pd4["custom_values"]["values"] if pd4 else [])}
            record("T4 date coerced to ISO-8601",
                   pd4 is not None and vdate.get("cierre-demo", {}).get("value")
                   == dval,
                   f"value={vdate.get('cierre-demo', {}).get('value')}")

            badd4 = _value("cierre-demo", "no-es-fecha", "wu17-t4-datebad")
            record("T4 invalid date value rejected 400",
                   envelope_ok(badd4, 400, "wu17-t4-datebad", "invalid_command"),
                   f"status={badd4.status_code}")

            s4 = _value("interes4-demo", "medio", "wu17-t4-selin")
            ps4 = console_ok(s4, "wu17-t4-selin", "custom_values")
            vsel = {v["name"]: v for v in (ps4["custom_values"]["values"] if ps4 else [])}
            record("T4 select value inside choices accepted",
                   ps4 is not None and vsel.get("interes4-demo", {}).get("value")
                   == "medio",
                   f"value={vsel.get('interes4-demo', {}).get('value')}")

            so4 = _value("interes4-demo", "maximo", "wu17-t4-selout")
            record("T4 select value outside choices rejected 400",
                   envelope_ok(so4, 400, "wu17-t4-selout", "invalid_command"),
                   f"status={so4.status_code}")

            und4 = _value("campo-inexistente", "x", "wu17-t4-undef")
            record("T4 undefined custom-field name rejected 400",
                   envelope_ok(und4, 400, "wu17-t4-undef", "invalid_command"),
                   f"status={und4.status_code}")

            # --- T5 contact tags -------------------------------------------
            t5a = _post(a, _tags_url(A_CONTACT), {"tag": "VIP",
                                                  "idempotencyKey": "wu17-t5-add"},
                        "wu17-t5-add")
            pt5a = console_ok(t5a, "wu17-t5-add", "tags")
            tags5a = pt5a["tags"]["tags"] if pt5a else []
            record("T5 add lowercases (VIP -> vip)",
                   pt5a is not None and "vip" in tags5a and "VIP" not in tags5a,
                   f"tags={tags5a}")

            t5b = _post(a, _tags_url(A_CONTACT), {"tag": "  Vip ",
                                                  "idempotencyKey": "wu17-t5-readd"},
                        "wu17-t5-readd")
            pt5b = console_ok(t5b, "wu17-t5-readd", "tags")
            tags5b = pt5b["tags"]["tags"] if pt5b else []
            record("T5 idempotent re-add dedupes",
                   pt5b is not None and tags5b == tags5a, f"tags={tags5b}")

            t5c = _post(a, _tags_url(A_CONTACT), {"tag": "Premium",
                                                  "idempotencyKey": "wu17-t5-prem"},
                        "wu17-t5-prem")
            pt5c = console_ok(t5c, "wu17-t5-prem", "tags")
            tags5c = pt5c["tags"]["tags"] if pt5c else []
            record("T5 distinct tag appends without duplicates",
                   pt5c is not None and "premium" in tags5c and tags5c.count("vip") == 1,
                   f"tags={tags5c}")

            f5 = _get(a, "/v1/demo/agent/contacts?tag=vip", "wu17-t5-filter")
            pf5 = console_ok(f5, "wu17-t5-filter", "contacts")
            ids5 = {c["id"] for c in (pf5["contacts"]["items"] if pf5 else [])}
            record("T5 ?tag= filter returns the tagged contact",
                   pf5 is not None and A_CONTACT in ids5, f"hits={sorted(ids5)}")

            r5 = _delete_body(a, _tags_url(A_CONTACT), {"tag": "VIP",
                                                        "idempotencyKey": "wu17-t5-rm"},
                              "wu17-t5-rm")
            pr5 = console_ok(r5, "wu17-t5-rm", "tags")
            tags5r = pr5["tags"]["tags"] if pr5 else []
            record("T5 remove case-normalizes (VIP removes vip)",
                   pr5 is not None and "vip" not in tags5r, f"tags={tags5r}")

            f5b = _get(a, "/v1/demo/agent/contacts?tag=vip", "wu17-t5-filter2")
            pf5b = console_ok(f5b, "wu17-t5-filter2", "contacts")
            ids5b = {c["id"] for c in (pf5b["contacts"]["items"] if pf5b else [])}
            record("T5 filter empty after removal", pf5b is not None and ids5b == set(),
                   f"hits={sorted(ids5b)}")

            # --- T6 forged/ghost/foreign ids non-enumerating ---------------
            ghostu = _put(a, f"{CUSTOM_FIELDS}/{GHOST}",
                          {"name": "x"}, "wu17-t6-ghostupd")
            ghostd = _delete(a, f"{CUSTOM_FIELDS}/{GHOST}", "wu17-t6-ghostdel")
            ghostg = _get(a, _custom_values_url(GHOST), "wu17-t6-ghostget")
            ghostp = _put(a, _custom_values_url(GHOST),
                          {"values": {"presupuesto-demo": 1}}, "wu17-t6-ghostput")
            ghostt = _post(a, _tags_url(GHOST), {"tag": "x"}, "wu17-t6-ghosttag")
            crossv = _get(a, _custom_values_url(B_LEAD), "wu17-t6-crosslead")
            crosst = _post(a, _tags_url(B_CONTACT), {"tag": "x"}, "wu17-t6-crosstag")
            ok = (envelope_ok(ghostu, 404, "wu17-t6-ghostupd", "resource_not_found")
                  and envelope_ok(ghostd, 404, "wu17-t6-ghostdel", "resource_not_found")
                  and envelope_ok(ghostg, 404, "wu17-t6-ghostget", "resource_not_found")
                  and envelope_ok(ghostp, 404, "wu17-t6-ghostput", "resource_not_found")
                  and envelope_ok(ghostt, 404, "wu17-t6-ghosttag", "resource_not_found")
                  and envelope_ok(crossv, 404, "wu17-t6-crosslead", "resource_not_found")
                  and envelope_ok(crosst, 404, "wu17-t6-crosstag", "resource_not_found"))
            record("T6 forged/ghost/foreign ids non-enumerating (404)", ok,
                   f"ghost={ghostu.status_code} cross={crossv.status_code}")

            # --- T7 stub offline -------------------------------------------
            record("T7 stub offline (no langchain import)",
                   "langchain_openai" not in sys.modules, "")

            # --- envelope probes -------------------------------------------
            none_app = TestClient(build_mvp_wu8_application(
                cfg, ACCOUNT_A, AbsentDemoIdentityAdapter()))
            with none_app as napp:
                r401 = _get(napp, CUSTOM_FIELDS, "wu17-401")
                record("401 without identity on custom-fields",
                       envelope_ok(r401, 401, "wu17-401",
                                   "authentication_required"),
                       f"status={r401.status_code}")
            r404 = _get(a, "/v1/demo/agent/nope", "wu17-404")
            r405 = _delete(a, CUSTOM_FIELDS, "wu17-405")
            badkey = _post(a, CUSTOM_FIELDS,
                           {"name": "x", "type": "text", "zzz": 1},
                           "wu17-badkey")
            record("404 unknown agent route", envelope_ok(
                r404, 404, "wu17-404", "route_not_found"),
                f"status={r404.status_code}")
            record("405 wrong method on custom-fields", envelope_ok(
                r405, 405, "wu17-405", "method_not_allowed"),
                f"status={r405.status_code}")
            record("unknown custom-field key fails closed 400", envelope_ok(
                badkey, 400, "wu17-badkey", "invalid_command"),
                f"status={badkey.status_code}")

            # End-of-run reconverge: leave the base in the pure seeded shape.
            asyncio.run(preclean(db))
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_configuration_wu17():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"CONFIGURATION WU17 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
