"""Versioned tenant-scoped READ services for the calendar / agenda (WU13).

Same pattern as the accepted WU6/WU11 reads: psycopg stays in the SQL adapter,
the service is provider-neutral, every list carries the mandatory
client_account_id predicate derived from the server context. Availability is a
pure deterministic function over business hours and non-cancelled events (the
harness validates it against an independent time-walk oracle).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from api.adapters.postgres_calendar import CalendarEventRepository
from api.domain.calendar_event import (
    BUSINESS_TIMEZONE,
    SLOT_MINUTES,
    CalendarEvent,
    business_slot_starts,
    overlaps,
)
from api.queries.crm_reads import CrmReadValidation
from api.tenant_boundary import TenantContext

MAX_RANGE_DAYS = 62


def _iso(value: datetime | None) -> str | None:
    return (value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z") if value else None)


def _parse_moment(raw: Any, label: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise CrmReadValidation(f"{label} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise CrmReadValidation(f"{label} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise CrmReadValidation(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_range(query: dict[str, Any]) -> tuple[datetime, datetime]:
    from_dt = _parse_moment(query.get("from"), "from")
    to_dt = _parse_moment(query.get("to"), "to")
    if not from_dt < to_dt:
        raise CrmReadValidation("from must be before to")
    if (to_dt - from_dt).days > MAX_RANGE_DAYS:
        raise CrmReadValidation(f"range must span at most {MAX_RANGE_DAYS} days")
    return from_dt, to_dt


def calendar_event_view(record: CalendarEvent) -> dict[str, Any]:
    return {
        "event_id": record.id,
        "title": record.title,
        "description": record.description,
        "starts_at": _iso(record.starts_at),
        "ends_at": _iso(record.ends_at),
        "contact_id": record.contact_id,
        "lead_id": record.lead_id,
        "property_id": record.property_id,
        "status": record.status,
        "source": record.source,
        "notes": record.notes,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }


class CalendarReadService:
    def __init__(self, runner, *, events: CalendarEventRepository):
        self._runner = runner
        self._events = events

    async def _server_meta(self, context: TenantContext) -> dict[str, Any]:
        rows = await self._runner.query("SELECT now() AS now")
        now = rows[0]["now"].astimezone(timezone.utc)
        return {
            "account_id": context.client_account.id,
            "as_of": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "correlation_id": context.correlation_id,
        }

    async def list_events(self, context: TenantContext,
                          query: dict[str, Any]) -> dict[str, Any]:
        from_dt, to_dt = _parse_range(query)
        include_cancelled = str(query.get("include_cancelled", "")).strip().lower() in ("1", "true")
        records = await self._events.list_in_range(
            context, from_dt=from_dt, to_dt=to_dt, include_cancelled=include_cancelled)
        items = [calendar_event_view(r) for r in records]
        meta = await self._server_meta(context)
        return {"items": items, "count": len(items),
                "from": _iso(from_dt), "to": _iso(to_dt),
                "include_cancelled": include_cancelled, **meta}

    async def availability(self, context: TenantContext,
                           query: dict[str, Any]) -> dict[str, Any]:
        from_dt, to_dt = _parse_range(query)
        busy = await self._events.list_in_range(
            context, from_dt=from_dt, to_dt=to_dt, include_cancelled=False)
        busy_ranges = [(r.starts_at, r.ends_at) for r in busy
                       if r.starts_at is not None and r.ends_at is not None]
        tz = ZoneInfo(BUSINESS_TIMEZONE)
        step = timedelta(minutes=SLOT_MINUTES)
        first_day = from_dt.astimezone(tz).date()
        last_day = to_dt.astimezone(tz).date()
        slots: list[dict[str, str | None]] = []
        day: date = first_day
        while day <= last_day:
            for start_local in business_slot_starts(day):
                start_utc = start_local.astimezone(timezone.utc)
                end_utc = start_utc + step
                # slot must sit fully inside the requested window
                if not (from_dt <= start_utc and end_utc <= to_dt):
                    continue
                if any(overlaps(start_utc, end_utc, b_start, b_end)
                       for b_start, b_end in busy_ranges):
                    continue
                slots.append({"starts_at": _iso(start_utc), "ends_at": _iso(end_utc)})
            day += timedelta(days=1)
        meta = await self._server_meta(context)
        return {"items": slots, "count": len(slots),
                "from": _iso(from_dt), "to": _iso(to_dt), **meta}
