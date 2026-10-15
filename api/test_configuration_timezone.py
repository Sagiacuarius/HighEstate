"""Configuration timezone seam units (change: configuration, WU17).

Phase 2: `load_calendar_config` reads the 010 `timezone` column (fallback to
BUSINESS_TIMEZONE when NULL/absent), `UpdateCalendarConfigCommand` validates
IANA timezones (invalid -> fail closed), and a non-BA timezone shifts the same
instant's local hour identically for the UI availability read and the agent
free_slots path. Account B's timezone is restored in try/finally so the wu17
base is left clean.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from api.adapters.postgres_agent_console import load_calendar_config
from api.adapters.postgres_agent_drafts import free_slots
from api.adapters.postgres_calendar import CalendarEventRepository
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.application.agent_commands_wu16 import UpdateCalendarConfigCommand
from api.application.crm_commands_wu5 import CrmCommandValidationError
from api.domain.calendar_event import BUSINESS_TIMEZONE, business_slot_starts
from api.queries.calendar_reads_wu13 import CalendarReadService
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

NEW_YORK = "America/New_York"
MONDAY = datetime(2026, 9, 21, 0, 0, 0, tzinfo=timezone.utc)
TUESDAY = datetime(2026, 9, 22, 0, 0, 0, tzinfo=timezone.utc)


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=tag,
    )


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    async def _run() -> None:
        cfg = load_database_configuration()
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        reads = CalendarReadService(runner, events=CalendarEventRepository(runner))
        cmd = UpdateCalendarConfigCommand(dict(cfg))
        tag = str(uuid4())

        # Z1 seeded timezone round-trips == BUSINESS_TIMEZONE.
        seeded = await load_calendar_config(_ctx(ACCOUNT_A, "z1"), runner)
        record("Z1 seeded timezone == BUSINESS_TIMEZONE",
               seeded.tz_name == BUSINESS_TIMEZONE, f"tz={seeded.tz_name!r}")

        # Z2 NULL timezone falls back to the constant.
        try:
            await runner.execute(
                "UPDATE agent_calendar_config SET timezone = NULL "
                "WHERE client_account_id = %s", (ACCOUNT_B,))
            nulled = await load_calendar_config(_ctx(ACCOUNT_B, "z2"), runner)
            record("Z2 NULL timezone falls back to BUSINESS_TIMEZONE",
                   nulled.tz_name == BUSINESS_TIMEZONE, f"tz={nulled.tz_name!r}")
        finally:
            await runner.execute(
                "UPDATE agent_calendar_config SET timezone = %s "
                "WHERE client_account_id = %s",
                (BUSINESS_TIMEZONE, ACCOUNT_B))

        # Z3 invalid IANA timezone fails closed before any SQL.
        invalid = False
        try:
            await cmd.execute_update(_ctx(ACCOUNT_B, "z3"), timezone="Not/AZone",
                                     idempotency_key=f"tz-invalid-{tag}")
        except CrmCommandValidationError:
            invalid = True
        record("Z3 invalid IANA timezone rejected", invalid, "")

        # Z4 non-BA timezone shifts the local hour AND UI == agent.
        try:
            await cmd.execute_update(_ctx(ACCOUNT_B, "z4"), timezone=NEW_YORK,
                                     idempotency_key=f"tz-ny-{tag}")
            config = await load_calendar_config(_ctx(ACCOUNT_B, "z4"), runner)
            record("Z4 command timezone round-trips",
                   config.tz_name == NEW_YORK, f"tz={config.tz_name!r}")

            ba_start = business_slot_starts(datetime(2026, 9, 21).date())[0]
            ny_hour = ba_start.astimezone(ZoneInfo(NEW_YORK)).hour
            record("Z4 same instant's local hour shifts away from 09:00",
                   ny_hour != 9, f"NY local hour={ny_hour}")

            avail = await reads.availability(
                _ctx(ACCOUNT_B, "z4"),
                {"from": MONDAY.isoformat(), "to": TUESDAY.isoformat()})
            ui_starts = {
                datetime.fromisoformat(item["starts_at"].replace("Z", "+00:00"))
                for item in avail["items"]
            }
            agent = await free_slots(
                _ctx(ACCOUNT_B, "z4"), runner,
                from_dt=MONDAY, to_dt=TUESDAY, calendar=config)
            agent_starts = {s for s, _ in agent}
            record("Z4 UI availability == agent free_slots (CRITICAL seam)",
                   ui_starts == agent_starts and bool(ui_starts),
                   f"ui={len(ui_starts)} agent={len(agent_starts)}")
        finally:
            await runner.execute(
                "UPDATE agent_calendar_config SET timezone = %s "
                "WHERE client_account_id = %s",
                (BUSINESS_TIMEZONE, ACCOUNT_B))

    asyncio.run(_run())
    return results


def test_configuration_timezone():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"CONFIGURATION-TIMEZONE UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
