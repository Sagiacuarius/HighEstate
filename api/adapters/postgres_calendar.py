"""Tenant-scoped PostgreSQL repository for the calendar / agenda (WU13).

Same mandatory patterns as the accepted adapters: `_require(context)` guard,
`client_account_id` predicate on every statement from the SERVER context, and
translation of sqlstates into non-enumerating domain errors. The overlap SQL is
a module constant so the read service (runner) and the atomic commands (cursor)
share one definition of "overlap".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.adapters.postgres_commercial_crm import (
    CrmNotFoundError,
    CrossTenantAccessDenied,
    _require,
    _translate,
)
from api.adapters.postgres_core import PostgresRunner
from api.domain.calendar_event import CalendarEvent

_EVENT_COLUMNS = """
    id, client_account_id, title, description, starts_at, ends_at,
    contact_id, lead_id, property_id, status, source, notes,
    created_at, updated_at
"""

# Overlap predicate (half-open): [starts_at, ends_at) vs [%s, %s).
# Non-cancelled events only; cancelled slots are free. Shared by the read
# service (runner) and the atomic commands (cursor): one definition.
OVERLAP_SQL = f"""
SELECT id, starts_at, ends_at FROM calendar_events
WHERE client_account_id = %s
  AND status <> 'cancelled'
  AND starts_at < %s
  AND ends_at > %s
"""


def _record(row: dict[str, Any]) -> CalendarEvent:
    return CalendarEvent(
        id=str(row["id"]),
        client_account_id=str(row["client_account_id"]),
        title=row["title"],
        description=row.get("description"),
        starts_at=row.get("starts_at"),
        ends_at=row.get("ends_at"),
        contact_id=str(row["contact_id"]) if row.get("contact_id") else None,
        lead_id=str(row["lead_id"]) if row.get("lead_id") else None,
        property_id=str(row["property_id"]) if row.get("property_id") else None,
        status=row["status"],
        source=row["source"],
        notes=row.get("notes"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class CalendarEventRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def get(self, context, event_id: str) -> CalendarEvent | None:
        account_id = _require(context)
        rows = await self._runner.query(
            f"SELECT {_EVENT_COLUMNS} FROM calendar_events "
            "WHERE id = %s AND client_account_id = %s",
            (event_id, account_id),
        )
        return _record(rows[0]) if rows else None

    async def list_in_range(self, context, *, from_dt: datetime, to_dt: datetime,
                            include_cancelled: bool = False) -> list[CalendarEvent]:
        account_id = _require(context)
        sql = (
            f"SELECT {_EVENT_COLUMNS} FROM calendar_events "
            "WHERE client_account_id = %s AND starts_at < %s AND ends_at > %s"
        )
        params: list[Any] = [account_id, to_dt, from_dt]
        if not include_cancelled:
            sql += " AND status <> 'cancelled'"
        sql += " ORDER BY starts_at, id"
        rows = await self._runner.query(sql, tuple(params))
        return [_record(r) for r in rows]

    async def find_overlaps(self, context, *, start: datetime, end: datetime,
                            exclude_id: str | None = None) -> list[dict[str, Any]]:
        """Non-cancelled events in scope overlapping [start, end), optionally
        excluding one id (for update/reschedule validation)."""
        account_id = _require(context)
        sql = OVERLAP_SQL
        params: list[Any] = [account_id, end, start]
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        rows = await self._runner.query(sql, tuple(params))
        return list(rows)
