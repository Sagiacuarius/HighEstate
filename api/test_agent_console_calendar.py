"""Console calendar seam units (change: agent-console, WU16).

Phase 5: edited hours/days/slot alter slot outcomes through the same pure
business_slot_starts both the UI and agent paths share (twin oracle over
business_slot_starts + overlaps, no DB), and the per-account loader falls
back to the module constants when the row is missing (delete+restore on
account B only, try/finally — the wu16 base is left clean).
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import date, datetime, timedelta, timezone

from api.adapters.postgres_agent_console import (
    CalendarConfig,
    default_calendar_config,
    load_calendar_config,
)
from api.adapters.postgres_agent_drafts import _resolve_calendar, free_slots
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.domain.calendar_event import (
    BUSINESS_DAYS,
    BUSINESS_END_HOUR,
    BUSINESS_START_HOUR,
    BUSINESS_TIMEZONE,
    SLOT_MINUTES,
    business_slot_starts,
    overlaps,
)
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    Membership,
    TenantContext,
    User,
)

AGENCY = "00000000-0000-0000-0000-000000000001"
USER = "10000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
PERMS = frozenset({"client_account:read", "client_account:write"})

MONDAY = date(2026, 9, 21)
SUNDAY = date(2026, 9, 20)


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=tag,
    )


def _twin_free(starts: list[datetime], busy: list[tuple[datetime, datetime]],
               slot: timedelta) -> list[tuple[datetime, datetime]]:
    """Pure twin of the free_slots window/overlap math (UTC instants)."""
    out = []
    for start in starts:
        end = start + slot
        if not any(overlaps(start, end, b_start, b_end) for b_start, b_end in busy):
            out.append((start, end))
    return out


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    # C1 defaults preserve the accepted shape (Mon 9 starts, Sun empty).
    default_starts = business_slot_starts(MONDAY)
    record("C1 default Monday yields 9 hourly starts",
           len(default_starts) == 9
           and default_starts[0].hour == BUSINESS_START_HOUR
           and default_starts[-1].hour == BUSINESS_END_HOUR - 1, f"n={len(default_starts)}")
    record("C1 default Sunday empty", business_slot_starts(SUNDAY) == [], "")

    # C2 edited config alters outcomes through the same pure function.
    custom = {"start_hour": 10, "end_hour": 16, "slot_minutes": 30,
              "working_days": (0, 1, 2, 3, 4)}
    edited = business_slot_starts(MONDAY, start_hour=10, end_hour=16,
                                  slot_minutes=30, working_days=(0, 1, 2, 3, 4))
    record("C2 edited window/slot yields 12 half-hour starts",
           len(edited) == 12 and edited[0].hour == 10 and edited[-1].hour == 15
           and edited[-1].minute == 30, f"n={len(edited)}")
    record("C2 edited config differs from default", edited != default_starts, "")
    record("C2 weekdays-only drops Sunday AND Saturday",
           business_slot_starts(SUNDAY, **custom) == []
           and business_slot_starts(date(2026, 9, 19), **custom) == [], "")
    record("C2 full-week config opens Sunday",
           len(business_slot_starts(SUNDAY, working_days=(0, 1, 2, 3, 4, 5, 6))) == 9, "")

    # C3 twin oracle: busy overlap removes exactly the overlapping starts.
    utc = [s.astimezone(timezone.utc) for s in edited]
    slot = timedelta(minutes=30)
    free_all = _twin_free(utc, [], slot)
    busy_one = [(utc[2], utc[2] + slot)]
    free_busy = _twin_free(utc, busy_one, slot)
    record("C3 empty busy keeps every start", free_all == [(s, s + slot) for s in utc], "")
    record("C3 one busy event removes exactly one start",
           len(free_busy) == len(free_all) - 1 and (utc[2], utc[2] + slot) not in free_busy, "")

    # C4 seam surface: free_slots takes calendar=None defaulting to constants.
    params = inspect.signature(free_slots).parameters
    record("C4 free_slots has calendar kwarg defaulting None",
           "calendar" in params and params["calendar"].default is None, "")
    sig = inspect.signature(business_slot_starts).parameters
    record("C4 business_slot_starts defaults equal module constants",
           sig["tz_name"].default == BUSINESS_TIMEZONE
           and sig["start_hour"].default == BUSINESS_START_HOUR
           and sig["end_hour"].default == BUSINESS_END_HOUR
           and sig["slot_minutes"].default == SLOT_MINUTES
           and tuple(sig["working_days"].default) == tuple(BUSINESS_DAYS), "")
    resolved_none = _resolve_calendar(None)
    record("C4 resolve(None) == constants",
           resolved_none[0] == BUSINESS_TIMEZONE and resolved_none[1] == BUSINESS_START_HOUR
           and resolved_none[2] == BUSINESS_END_HOUR and resolved_none[3] == SLOT_MINUTES
           and tuple(resolved_none[4]) == tuple(BUSINESS_DAYS),
           f"resolved={resolved_none}")
    resolved_cfg = _resolve_calendar(CalendarConfig("X", 10, 16, 30, (0, 1, 2, 3, 4), "Lbl"))
    record("C4 resolve(CalendarConfig) passes through",
           resolved_cfg == ("X", 10, 16, 30, (0, 1, 2, 3, 4)), f"resolved={resolved_cfg}")
    resolved_row = _resolve_calendar({"window_start": "10:00", "window_end": "16:00",
                                      "slot_minutes": 30, "working_days": [0, 1, 2, 3, 4]})
    record("C4 resolve(raw row) parses HH:MM windows",
           resolved_row[1:] == (10, 16, 30, (0, 1, 2, 3, 4)), f"resolved={resolved_row}")

    async def _run() -> None:
        cfg = load_database_configuration()
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        seeded = await load_calendar_config(_ctx(ACCOUNT_A, "c5"), runner)
        record("C5 seeded config mirrors BUSINESS_* constants",
               seeded.start_hour == BUSINESS_START_HOUR
               and seeded.end_hour == BUSINESS_END_HOUR
               and seeded.slot_minutes == SLOT_MINUTES
               and tuple(seeded.working_days) == tuple(BUSINESS_DAYS)
               and seeded.tz_name == BUSINESS_TIMEZONE, f"seeded={seeded}")
        record("C5 seeded destination label round-trips",
               seeded.destination_label == "Agenda de la cuenta",
               f"label={seeded.destination_label!r}")
        saved = await runner.query(
            "SELECT * FROM agent_calendar_config WHERE client_account_id = %s", (ACCOUNT_B,))
        assert saved, "seed calendar row missing for B"
        try:
            await runner.execute(
                "DELETE FROM agent_calendar_config WHERE client_account_id = %s", (ACCOUNT_B,))
            fallback = await load_calendar_config(_ctx(ACCOUNT_B, "c6"), runner)
            record("C6 missing row falls back to constants",
                   fallback == default_calendar_config(), f"fallback={fallback}")
        finally:
            row = saved[0]
            await runner.execute(
                "INSERT INTO agent_calendar_config (id, client_account_id, window_start, "
                "window_end, slot_minutes, working_days, destination_label, demo_seed_key) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (row["id"], row["client_account_id"], row["window_start"], row["window_end"],
                 row["slot_minutes"], row["working_days"], row["destination_label"],
                 row["demo_seed_key"]))
        rows = await runner.query("SELECT count(*) AS n FROM agent_calendar_config", ())
        record("C6 calendar rows restored to 2", int(rows[0]["n"]) == 2, "")

    asyncio.run(_run())
    return results


def test_agent_console_calendar():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT-CONSOLE-CALENDAR UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
