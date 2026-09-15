"""Calendar / Agenda (change: calendar-agenda, WU13) real-PostgreSQL harness.

Runs against production_runtime_mvp_wu13 (disposable: runner 001-006 +
mvp_seed.sql + mvp_seed_005.sql). Never touches earlier accepted bases.

Events are created exclusively by RUNNING the accepted WU13 command surfaces
(create/update/cancel on the WU8 app) — never by hand-inserting rows. Seed
contacts/leads/properties (005) provide the optional references.

Covers: tenant-scoped create + cross-account invisible; validation 400
(malformed times/enums/order); update incl. reschedule; overlap deny;
soft-cancel (no physical delete, audited); idempotent replay (create/update/
cancel); range reads; availability oracle incl. end-overlap + cancelled-frees;
audit visibility of calendar.* actions; A/B isolation; accepted WU6 agenda
unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.composition_wu8_mvp import build_mvp_wu8_application
from api.identity.demo_identity import AbsentDemoIdentityAdapter, DemoIdentityAdapter
from api.routes.audit_screen_wu12 import AUDIT_PATH

ROOT = Path(__file__).resolve().parent.parent
SEED_FILES = [ROOT / "infra" / "migrations" / "validation" / "mvp_seed.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_005.sql"]

ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
A_CONTACT = "61000000-0000-0000-0000-000000000001"
A_LEAD = "65000000-0000-0000-0000-000000000001"
A_PROP_1 = "62000000-0000-0000-0000-000000000001"
GHOST = "0000dead-0000-4000-8000-000000000000"

CAL_EVENTS = "/v1/demo/calendar/events"
CAL_AVAIL = "/v1/demo/calendar/availability"
AGENDA_PATH = "/v1/demo/agenda"

# Fixed deterministic times (UTC). 2026-09-14 is a Monday.
TUE_1400 = "2026-09-15T14:00:00.000Z"
TUE_1500 = "2026-09-15T15:00:00.000Z"
TUE_1600 = "2026-09-15T16:00:00.000Z"
TUE_1700 = "2026-09-15T17:00:00.000Z"
TUE_1630 = "2026-09-15T16:30:00.000Z"
TUE_1730 = "2026-09-15T17:30:00.000Z"
TUE_1800 = "2026-09-15T18:00:00.000Z"
TUE_1900 = "2026-09-15T19:00:00.000Z"
WED_1400 = "2026-09-16T14:00:00.000Z"
WED_1500 = "2026-09-16T15:00:00.000Z"
MON_0000 = "2026-09-14T00:00:00.000Z"
MON_2359 = "2026-09-14T23:59:59.000Z"
MON_1300 = "2026-09-14T13:00:00.000Z"  # = BA 10:00
MON_1400 = "2026-09-14T14:00:00.000Z"  # = BA 11:00


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
    """Deterministic baseline: WU12 rules + wipe all calendar events (the seed
    never creates events; every event on this base comes from commands)."""
    for stmt in (
        "DELETE FROM calendar_events",
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


def _put(client, url, body, correlation):
    return client.put(url, json=body,
                      headers={"X-Correlation-Id": correlation})


def _delete(client, url, correlation, key=None):
    url = f"{url}?idempotencyKey={key}" if key else url
    return client.delete(url, headers={"X-Correlation-Id": correlation})


def _events(client, correlation, frm, to, include_cancelled=False):
    qs = f"from={frm}&to={to}"
    if include_cancelled:
        qs += "&include_cancelled=true"
    return _get(client, f"{CAL_EVENTS}?{qs}", correlation)


def _avail(client, correlation, frm, to):
    return _get(client, f"{CAL_AVAIL}?from={frm}&to={to}", correlation)


def _audit(client, correlation, **filters):
    qs = "&".join(f"{k}={v}" for k, v in filters.items() if v is not None)
    url = f"{AUDIT_PATH}?{qs}" if qs else AUDIT_PATH
    return _get(client, url, correlation)


def oracle_free_slots(day: date, busy: list[tuple[datetime, datetime]]) -> list[str]:
    """Independent time-walk: BA Mon-Sat 09:00-17:00 starts, drop overlaps."""
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    if day.weekday() not in (0, 1, 2, 3, 4, 5):
        return []
    free: list[str] = []
    for hour in range(9, 18):
        start_local = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = start_utc + timedelta(minutes=60)
        if any(start_utc < b_end and b_start < end_utc for b_start, b_end in busy):
            continue
        free.append(start_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"))
    return free


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
            # C1. Tenant-scoped create + cross-account invisible.
            c1 = _post(a, CAL_EVENTS, {
                "title": "Visita · Palermo", "startsAt": TUE_1400, "endsAt": TUE_1500,
                "contactId": A_CONTACT, "leadId": A_LEAD, "propertyId": A_PROP_1,
                "idempotencyKey": "wu13-c1",
            }, "wu13-c1")
            c1j = c1.json() if c1.status_code == 200 else {}
            c1_id = ((c1j.get("result") or {}).get("resource_id"))
            ok = (c1.status_code == 200 and c1j.get("accepted") is True
                  and c1j.get("correlation_id") == "wu13-c1" and bool(c1_id))
            record("C1 create tenant-scoped event (A)", ok,
                   f"status={c1.status_code} id={str(c1_id)[:8] if c1_id else None}")
            la = _events(a, "wu13-c1-list-a", TUE_1400, TUE_1500)
            lb = _events(b, "wu13-c1-list-b", TUE_1400, TUE_1500)
            ok = (la.status_code == 200 and len(la.json()["items"]) == 1
                  and lb.status_code == 200 and len(lb.json()["items"]) == 0)
            record("C1 cross-account invisible (B empty)", ok,
                   f"a={len(la.json().get('items', []))} b={len(lb.json().get('items', []))}")

            # C2. Validation 400 (malformed ISO, missing tz, empty title, bad source, bad uuid).
            bad_cases = [
                ({"title": "X", "startsAt": "not-a-date", "endsAt": TUE_1500,
                  "idempotencyKey": "wu13-c2a"}, "bad iso"),
                ({"title": "X", "startsAt": "2026-09-15T14:00:00", "endsAt": TUE_1500,
                  "idempotencyKey": "wu13-c2b"}, "missing tz"),
                ({"title": "   ", "startsAt": TUE_1400, "endsAt": TUE_1500,
                  "idempotencyKey": "wu13-c2c"}, "empty title"),
                ({"title": "X", "startsAt": TUE_1400, "endsAt": TUE_1500,
                  "source": "carrier-pigeon", "idempotencyKey": "wu13-c2d"}, "bad source"),
                ({"title": "X", "startsAt": TUE_1400, "endsAt": TUE_1500,
                  "contactId": "nope", "idempotencyKey": "wu13-c2e"}, "bad uuid"),
                ({"title": "X", "startsAt": TUE_1400, "endsAt": TUE_1500,
                  "extra": "no", "idempotencyKey": "wu13-c2f"}, "unknown key"),
            ]
            c2_ok = True
            for idx, (body, label) in enumerate(bad_cases):
                r = _post(a, CAL_EVENTS, body, f"wu13-c2-{idx}")
                if not envelope_ok(r, 400, f"wu13-c2-{idx}", "invalid_command"):
                    c2_ok = False
                    break
            record("C2 malformed inputs fail closed (400 invalid_command)", c2_ok, f"cases={len(bad_cases)}")

            # C5. starts_at >= ends_at rejected (validation, before SQL).
            r5 = _post(a, CAL_EVENTS, {
                "title": "X", "startsAt": TUE_1500, "endsAt": TUE_1400,
                "idempotencyKey": "wu13-c5",
            }, "wu13-c5")
            record("C5 inverted time order rejected (400)", envelope_ok(
                r5, 400, "wu13-c5", "invalid_command"), f"status={r5.status_code}")

            # C3. Update fields incl. reschedule.
            u3 = _put(a, f"{CAL_EVENTS}/{c1_id}", {
                "title": "Visita reprogramada · Palermo",
                "startsAt": TUE_1600, "endsAt": TUE_1700,
                "idempotencyKey": "wu13-c3",
            }, "wu13-c3")
            u3j = u3.json() if u3.status_code == 200 else {}
            ok = (u3.status_code == 200
                  and (u3j.get("result") or {}).get("starts_at", "").startswith("2026-09-15T16:00"))
            record("C3 update reschedules event", ok, f"status={u3.status_code}")

            # C4. Overlap denied; non-overlap accepted.
            ov = _post(a, CAL_EVENTS, {
                "title": "Solapa", "startsAt": TUE_1630, "endsAt": TUE_1730,
                "idempotencyKey": "wu13-c4-overlap",
            }, "wu13-c4-overlap")
            ok = envelope_ok(ov, 403, "wu13-c4-overlap", "tenant_access_denied")
            record("C4 overlapping create denied (403)", ok, f"status={ov.status_code}")
            free = _post(a, CAL_EVENTS, {
                "title": "No solapa", "startsAt": TUE_1800, "endsAt": TUE_1900,
                "idempotencyKey": "wu13-c4-free",
            }, "wu13-c4-free")
            record("C4 non-overlapping create accepted", free.status_code == 200,
                   f"status={free.status_code}")

            # C6. Cancel is soft (row stays, status cancelled, audited).
            d6 = _delete(a, f"{CAL_EVENTS}/{c1_id}", "wu13-c6", key="wu13-c6-key")
            d6j = d6.json() if d6.status_code == 200 else {}
            ok = (d6.status_code == 200
                  and (d6j.get("result") or {}).get("status") == "cancelled")
            record("C6 cancel soft-deletes (status cancelled)", ok, f"status={d6.status_code}")
            row = asyncio.run(db.one(
                "SELECT status FROM calendar_events WHERE id = %s", (c1_id,)))
            inc = _events(a, "wu13-c6-inc", TUE_1400, TUE_1900, include_cancelled=True)
            exc = _events(a, "wu13-c6-exc", TUE_1400, TUE_1900)
            ok = (row is not None and row["status"] == "cancelled"
                  and any(i["event_id"] == c1_id for i in inc.json()["items"])
                  and not any(i["event_id"] == c1_id for i in exc.json()["items"]))
            record("C6 cancelled row persists but default list hides it", ok, "")

            # C7. Idempotent replay (create/update/cancel, zero extra effects).
            r7a = _post(a, CAL_EVENTS, {
                "title": "Replay", "startsAt": WED_1400, "endsAt": WED_1500,
                "idempotencyKey": "wu13-replay-1",
            }, "wu13-r7a")
            r7b = _post(a, CAL_EVENTS, {
                "title": "Replay", "startsAt": WED_1400, "endsAt": WED_1500,
                "idempotencyKey": "wu13-replay-1",
            }, "wu13-r7b")
            id_a = ((r7a.json().get("result") or {}).get("resource_id"))
            id_b = ((r7b.json().get("result") or {}).get("resource_id"))
            n = asyncio.run(db.count(
                "SELECT count(*) AS n FROM calendar_events WHERE client_account_id = %s "
                "AND title = 'Replay'", (ACCOUNT_A,)))
            ok = (r7a.status_code == 200 and r7b.status_code == 200
                  and r7b.json().get("replayed") is True and id_a == id_b and n == 1)
            record("C7 create replay returns stored result, one row", ok, f"n={n}")
            u7a = _put(a, f"{CAL_EVENTS}/{id_a}", {"notes": "hola",
                       "idempotencyKey": "wu13-replay-u"}, "wu13-r7c")
            u7b = _put(a, f"{CAL_EVENTS}/{id_a}", {"notes": "hola",
                       "idempotencyKey": "wu13-replay-u"}, "wu13-r7d")
            ok = (u7a.status_code == 200 and u7b.status_code == 200
                  and u7b.json().get("replayed") is True)
            record("C7 update replay returns stored result", ok, "")
            d7a = _delete(a, f"{CAL_EVENTS}/{id_a}", "wu13-r7e", key="wu13-replay-d")
            d7b = _delete(a, f"{CAL_EVENTS}/{id_a}", "wu13-r7f", key="wu13-replay-d")
            ok = (d7a.status_code == 200 and d7b.status_code == 200
                  and d7b.json().get("replayed") is True)
            record("C7 cancel replay returns stored result", ok, "")

            # C8. Range scoping: in-range returned, out-of-range excluded.
            rng = _events(a, "wu13-c8", "2026-09-15T00:00:00.000Z", "2026-09-15T23:59:59.000Z")
            items = rng.json().get("items", []) if rng.status_code == 200 else []
            ok = (rng.status_code == 200 and len(items) >= 1
                  and all("2026-09-15" in i["starts_at"] for i in items))
            record("C8 range read returns only intersecting events", ok, f"count={len(items)}")

            # C9. Availability oracle (Monday 2026-09-14): one 13:00-14:00Z event
            # (= BA 10:00-11:00) removes exactly that slot; cancel frees it.
            ev9 = _post(a, CAL_EVENTS, {
                "title": "Bloquea 10hs", "startsAt": MON_1300, "endsAt": MON_1400,
                "idempotencyKey": "wu13-c9",
            }, "wu13-c9")
            ev9_id = ((ev9.json().get("result") or {}).get("resource_id"))
            av = _avail(a, "wu13-c9-av", MON_0000, MON_2359)
            avj = av.json() if av.status_code == 200 else {}
            got = sorted(i["starts_at"] for i in avj.get("items", []))
            busy = [(datetime(2026, 9, 14, 13, 0, tzinfo=timezone.utc),
                     datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc))]
            want = sorted(oracle_free_slots(date(2026, 9, 14), busy))
            ok = (av.status_code == 200 and got == want and len(got) == 8
                  and "2026-09-14T13:00:00.000Z" not in got)
            record("C9 availability matches oracle (8/9 slots, 13:00Z busy)", ok,
                   f"got={len(got)} want={len(want)}")
            _delete(a, f"{CAL_EVENTS}/{ev9_id}", "wu13-c9-free", key="wu13-c9-free-key")
            av2 = _avail(a, "wu13-c9-av2", MON_0000, MON_2359)
            got2 = sorted(i["starts_at"] for i in av2.json().get("items", []))
            want2 = sorted(oracle_free_slots(date(2026, 9, 14), []))
            record("C9 cancelled event frees its slot (9/9)", av2.status_code == 200
                   and got2 == want2, f"got={len(got2)}")

            # C10. calendar.* audits visible in the accepted audit screen.
            au = _audit(a, "wu13-c10", action="crm.calendar.create")
            auj = au.json() if au.status_code == 200 else {}
            ok = (au.status_code == 200 and len(auj.get("items", [])) >= 1
                  and all(i["action"] == "crm.calendar.create" for i in auj["items"]))
            record("C10 calendar.create audits visible in audit screen", ok,
                   f"count={len(auj.get('items', []))}")

            # C11. A/B isolation on writes.
            b_ev = _post(b, CAL_EVENTS, {
                "title": "De B", "startsAt": WED_1400, "endsAt": WED_1500,
                "idempotencyKey": "wu13-c11",
            }, "wu13-c11")
            b_id = ((b_ev.json().get("result") or {}).get("resource_id"))
            la11 = _events(a, "wu13-c11-a", WED_1400, WED_1500)
            ok = (b_ev.status_code == 200
                  and not any(i["event_id"] == b_id for i in la11.json().get("items", [])))
            record("C11 B event invisible to A", ok, "")
            ghost_upd = _put(b, f"{CAL_EVENTS}/{c1_id}", {"notes": "x",
                              "idempotencyKey": "wu13-c11-g"}, "wu13-c11-g")
            record("C11 B cannot touch A event (403)", envelope_ok(
                ghost_upd, 403, "wu13-c11-g", "tenant_access_denied"),
                f"status={ghost_upd.status_code}")

            # C12. Accepted WU6 agenda read unchanged (calendar events do not leak in).
            ag = _get(a, AGENDA_PATH, "wu13-c12")
            agj = ag.json() if ag.status_code == 200 else {}
            ok = (ag.status_code == 200 and "counts" in agj and "items" in agj
                  and not any("calendar" in k for k in agj.keys()))
            record("C12 accepted agenda read unchanged", ok, f"status={ag.status_code}")

            # 401/403/404/405 envelope probes.
            none_app = TestClient(build_mvp_wu8_application(
                cfg, ACCOUNT_A, AbsentDemoIdentityAdapter()))
            with none_app as napp:
                r401 = _get(napp, f"{CAL_EVENTS}?from={TUE_1400}&to={TUE_1500}", "wu13-401")
                record("401 without identity", envelope_ok(
                    r401, 401, "wu13-401", "authentication_required"),
                    f"status={r401.status_code}")
            r404 = _get(a, "/v1/demo/calendar/nope", "wu13-404")
            record("404 unknown route", envelope_ok(
                r404, 404, "wu13-404", "route_not_found"), f"status={r404.status_code}")
            r405 = a.post(CAL_AVAIL, headers={"X-Correlation-Id": "wu13-405"})
            record("405 wrong method", envelope_ok(
                r405, 405, "wu13-405", "method_not_allowed"), f"status={r405.status_code}")
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_calendar_wu13():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"CALENDAR WU13 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
