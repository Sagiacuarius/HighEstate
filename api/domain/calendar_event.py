"""Calendar / Agenda domain (WU13).

A visit/appointment is first-class. Business hours are a demo-global constant
(per-account hours arrive with the Configuración change); see the brief for the
timezone rationale. The slot generator is pure (no I/O) so the availability
service and the harness oracle share one definition of "business slot".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

EVENT_STATUSES = ("scheduled", "cancelled", "completed")
EVENT_SOURCES = ("manual", "agent")

# Business hours (Option A): Monday-Saturday, 09:00-18:00, 60-minute slots.
# Stored/rendered in a fixed business timezone; the DB holds UTC timestamptz.
BUSINESS_TIMEZONE = "America/Argentina/Buenos_Aires"
BUSINESS_DAYS = (0, 1, 2, 3, 4, 5)  # Monday-Saturday (Python weekday())
BUSINESS_START_HOUR = 9
BUSINESS_END_HOUR = 18
SLOT_MINUTES = 60


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    client_account_id: str
    title: str
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    contact_id: str | None = None
    lead_id: str | None = None
    property_id: str | None = None
    status: str = "scheduled"
    source: str = "manual"
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


def business_slot_starts(day: date, *, tz_name: str = BUSINESS_TIMEZONE) -> list[datetime]:
    """Slot start datetimes (timezone-aware, business tz) for one calendar day.

    Returns 09:00, 10:00, ..., 17:00 when the weekday is Mon-Sat, else [].
    Pure: no I/O, shared by the availability service and the harness oracle.
    """
    if day.weekday() not in BUSINESS_DAYS:
        return []
    tz = ZoneInfo(tz_name)
    starts: list[datetime] = []
    cursor = datetime.combine(day, time(BUSINESS_START_HOUR, 0), tzinfo=tz)
    end = datetime.combine(day, time(BUSINESS_END_HOUR, 0), tzinfo=tz)
    step = timedelta(minutes=SLOT_MINUTES)
    # the last slot STARTS at (END - SLOT); a slot starting at END is empty.
    while cursor + step <= end:
        starts.append(cursor)
        cursor += step
    return starts


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open overlap predicate: [a_start, a_end) vs [b_start, b_end)."""
    return a_start < b_end and b_start < a_end


def event_to_dict(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "client_account_id": event.client_account_id,
        "title": event.title,
        "description": event.description,
        "starts_at": event.starts_at,
        "ends_at": event.ends_at,
        "contact_id": event.contact_id,
        "lead_id": event.lead_id,
        "property_id": event.property_id,
        "status": event.status,
        "source": event.source,
        "notes": event.notes,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }
