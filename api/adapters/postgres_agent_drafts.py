"""Tenant-scoped reads for the conversational agent (WU14).

Deterministic, provider-neutral SQL: draft fetch/list (account predicate first),
the criteria property match, the free-slot computation over business hours, and
the contact's next upcoming scheduled event. No writes, no audit, no HTTP, no
LLM. The agent receives these as pure callables and the command layer owns all
mutations. Cross-scope is non-enumerating (returns None / empty).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.adapters.postgres_calendar import CalendarEventRepository
from api.adapters.postgres_commercial_crm import _require
from api.adapters.postgres_core import PostgresRunner
from api.domain.agent_draft import AgentDraft, ProposedAction
from api.domain.calendar_event import CalendarEvent, business_slot_starts, overlaps
from api.domain.property_catalog import OPERATION_TYPES_V11, PROPERTY_TYPES

import json


def _draft(row: dict[str, Any]) -> AgentDraft:
    def _jsonb(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, str):
            return json.loads(value)
        return value
    criteria = _jsonb(row.get("criteria"), {})
    matched = _jsonb(row.get("matched_property_ids"), [])
    actions = _jsonb(row.get("proposed_actions"), [])
    execution = _jsonb(row.get("execution_result"), None)
    return AgentDraft(
        id=str(row["id"]),
        client_account_id=str(row["client_account_id"]),
        conversation_id=str(row["conversation_id"]),
        message_id=str(row["message_id"]) if row.get("message_id") else None,
        contact_id=str(row["contact_id"]),
        lead_id=str(row["lead_id"]) if row.get("lead_id") else None,
        status=row["status"],
        intent=row["intent"],
        criteria=criteria,
        matched_property_ids=tuple(matched),
        proposed_actions=tuple(ProposedAction(a["type"], a.get("params", {})) for a in actions),
        reply_text=row.get("reply_text"),
        model_provider=row["model_provider"],
        model_name=row["model_name"],
        created_at=row.get("created_at"),
        decided_at=row.get("decided_at"),
        decided_by=str(row["decided_by"]) if row.get("decided_by") else None,
        execution_result=tuple(execution) if execution is not None else None,
    )


class AgentDraftRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def get(self, context, draft_id: str) -> AgentDraft | None:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM agent_drafts WHERE id = %s AND client_account_id = %s",
            (draft_id, account_id),
        )
        return _draft(rows[0]) if rows else None

    async def list_conversation(self, context, *, conversation_id: str | None = None,
                                limit: int = 20) -> list[AgentDraft]:
        account_id = _require(context)
        if conversation_id is not None:
            rows = await self._runner.query(
                "SELECT * FROM agent_drafts WHERE client_account_id = %s "
                "AND conversation_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                (account_id, conversation_id, max(1, min(int(limit or 20), 100))),
            )
        else:
            rows = await self._runner.query(
                "SELECT * FROM agent_drafts WHERE client_account_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (account_id, max(1, min(int(limit or 20), 100))),
            )
        return [_draft(r) for r in rows]


async def search_properties_for_criteria(context, runner: PostgresRunner,
                                         criteria: dict[str, Any]) -> list[str]:
    """Deterministic tenant-scoped match over `properties` for the criteria the
    model extracted (NOT WU11's list_properties; the agent needs rooms/bathrooms
    /operation filters it does not expose). Returns up to 10 property ids."""
    account_id = _require(context)
    sql = ("SELECT id FROM properties "
           "WHERE client_account_id = %s AND status = 'available'")
    params: list[Any] = [account_id]
    op = criteria.get("operation_type")
    if op in OPERATION_TYPES_V11:
        sql += " AND operation_type = %s"
        params.append(op)
    ptype = criteria.get("property_type")
    if ptype in PROPERTY_TYPES:
        sql += " AND property_type = %s"
        params.append(ptype)
    city = criteria.get("city")
    if isinstance(city, str) and city.strip():
        sql += " AND city ILIKE %s"
        params.append(f"%{city.strip()}%")
    budget = criteria.get("budget_max")
    if isinstance(budget, (int, float)) and not isinstance(budget, bool):
        sql += " AND price_amount <= %s"
        params.append(budget)
    rooms = criteria.get("rooms_min")
    if isinstance(rooms, int) and rooms >= 0:
        sql += " AND (rooms >= %s OR rooms IS NULL)"
        params.append(rooms)
    baths = criteria.get("bathrooms_min")
    if isinstance(baths, int) and baths >= 0:
        sql += " AND (bathrooms >= %s OR bathrooms IS NULL)"
        params.append(baths)
    sql += " ORDER BY price_amount ASC NULLS LAST, id LIMIT 10"
    rows = await runner.query(sql, tuple(params))
    return [str(r["id"]) for r in rows]


async def free_slots(context, runner: PostgresRunner, *, from_dt: datetime,
                     to_dt: datetime, exclude_event_id: str | None = None,
                     tz_name: str | None = None, calendar: Any | None = None
                     ) -> list[tuple[datetime, datetime]]:
    """Business-hours slots within [from_dt, to_dt) minus any overlap
    with non-cancelled events (optionally excluding one event id, for the
    contact's own reschedule target).

    Additive `calendar` config (change: agent-console, WU16): a mapping or
    object carrying tz_name/start_hour/end_hour/slot_minutes/working_days;
    None (or absent fields) falls back to the module constants, so bare
    calls keep the accepted 60-minute behavior exactly.
    """
    account_id = _require(context)
    events = await CalendarEventRepository(runner).list_in_range(
        context, from_dt=from_dt, to_dt=to_dt, include_cancelled=False)
    busy = [(e.starts_at, e.ends_at) for e in events
            if e.starts_at is not None and e.ends_at is not None
            and (exclude_event_id is None or e.id != exclude_event_id)]
    from api.domain.calendar_event import BUSINESS_TIMEZONE
    from datetime import date as _date, timedelta
    cfg_tz, cfg_start, cfg_end, cfg_slot, cfg_days = _resolve_calendar(calendar)
    tz = _tzinfo(tz_name or cfg_tz or BUSINESS_TIMEZONE)
    step = timedelta(minutes=cfg_slot)
    first_day = from_dt.astimezone(tz).date()
    last_day = to_dt.astimezone(tz).date()
    out: list[tuple[datetime, datetime]] = []
    day: _date = first_day
    while day <= last_day:
        for start_local in business_slot_starts(
                day, tz_name=tz_name or cfg_tz or BUSINESS_TIMEZONE,
                start_hour=cfg_start, end_hour=cfg_end,
                slot_minutes=cfg_slot, working_days=cfg_days):
            s_utc = start_local.astimezone(_utc())
            e_utc = s_utc + step
            if not (from_dt <= s_utc and e_utc <= to_dt):
                continue
            if any(overlaps(s_utc, e_utc, b_start, b_end) for b_start, b_end in busy):
                continue
            out.append((s_utc, e_utc))
        day += timedelta(days=1)
    return out


def _resolve_calendar(calendar: Any | None) -> tuple[Any, Any, Any, Any, Any]:
    """Resolve (tz_name, start_hour, end_hour, slot_minutes, working_days).

    Accepts None, a mapping (raw config row or dict), or an attribute
    object (e.g. the console CalendarConfig); every absent/None field falls
    back to the module constant, so bare calls behave exactly as before.
    """
    from api.domain.calendar_event import (
        BUSINESS_DAYS,
        BUSINESS_END_HOUR,
        BUSINESS_START_HOUR,
        BUSINESS_TIMEZONE,
        SLOT_MINUTES,
    )
    defaults = {
        "tz_name": BUSINESS_TIMEZONE,
        "start_hour": BUSINESS_START_HOUR,
        "end_hour": BUSINESS_END_HOUR,
        "slot_minutes": SLOT_MINUTES,
        "working_days": BUSINESS_DAYS,
    }
    if calendar is None:
        return (defaults["tz_name"], defaults["start_hour"], defaults["end_hour"],
                defaults["slot_minutes"], defaults["working_days"])

    # Raw config rows name the window window_start/window_end; accept both.
    _KEYS = {"start_hour": ("start_hour", "window_start"),
             "end_hour": ("end_hour", "window_end")}

    def _raw(name: str) -> Any:
        if isinstance(calendar, dict):
            for key in _KEYS.get(name, (name,)):
                if calendar.get(key) is not None:
                    return calendar.get(key)
            return None
        value = getattr(calendar, name, None)
        if value is None:
            for key in _KEYS.get(name, (name,))[1:]:
                value = getattr(calendar, key, None)
                if value is not None:
                    break
        return value

    def _get(name: str) -> Any:
        value = _raw(name)
        # Raw config rows carry the window as "HH:MM" text; accept both.
        if value is None:
            return defaults[name]
        if name in ("start_hour", "end_hour") and isinstance(value, str):
            try:
                return int(value.strip().split(":")[0])
            except (ValueError, IndexError):
                return defaults[name]
        if name == "working_days" and isinstance(value, list):
            return tuple(value)
        return value

    return (_get("tz_name"), _get("start_hour"), _get("end_hour"),
            _get("slot_minutes"), _get("working_days"))


async def upcoming_event(context, runner: PostgresRunner, *, contact_id: str,
                         now_dt: datetime) -> CalendarEvent | None:
    """The contact's next scheduled event at/after `now_dt` (for reschedule)."""
    account_id = _require(context)
    rows = await runner.query(
        "SELECT * FROM calendar_events WHERE client_account_id = %s AND contact_id = %s "
        "AND status = 'scheduled' AND starts_at >= %s "
        "ORDER BY starts_at ASC, id LIMIT 1",
        (account_id, contact_id, now_dt),
    )
    if not rows:
        return None
    from api.domain.calendar_event import CalendarEvent as CE  # local, avoid cycle noise
    r = rows[0]
    return CE(
        id=str(r["id"]), client_account_id=str(r["client_account_id"]), title=r["title"],
        description=r.get("description"), starts_at=r.get("starts_at"), ends_at=r.get("ends_at"),
        contact_id=str(r["contact_id"]) if r.get("contact_id") else None,
        lead_id=str(r["lead_id"]) if r.get("lead_id") else None,
        property_id=str(r["property_id"]) if r.get("property_id") else None,
        status=r["status"], source=r["source"], notes=r.get("notes"),
        created_at=r.get("created_at"), updated_at=r.get("updated_at"),
    )


async def conversation_contact(context, runner: PostgresRunner,
                               conversation_id: str) -> str | None:
    """Server-derived contact for a session conversation (None = foreign/ghost)."""
    account_id = _require(context)
    rows = await runner.query(
        "SELECT contact_id FROM conversations WHERE id = %s AND client_account_id = %s",
        (conversation_id, account_id),
    )
    return str(rows[0]["contact_id"]) if rows else None


async def lead_for_contact(context, runner: PostgresRunner,
                           contact_id: str) -> str | None:
    """The contact's first lead in scope (None when absent — valid for the
    draft: actions carry lead_id optional)."""
    account_id = _require(context)
    rows = await runner.query(
        "SELECT id FROM leads WHERE client_account_id = %s AND contact_id = %s "
        "ORDER BY created_at, id LIMIT 1",
        (account_id, contact_id),
    )
    return str(rows[0]["id"]) if rows else None


def _tzinfo(name: str):
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def _utc():
    from datetime import timezone
    return timezone.utc
