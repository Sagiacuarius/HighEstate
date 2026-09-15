"""Atomic calendar / agenda commands (change: calendar-agenda, WU13).

Same accepted machinery as the WU5 CRM commands — canonical hash,
reserve(actor, operation, key) -> apply -> audit + outbox -> one transaction;
replay returns the canonical result, key reuse with a different payload
conflicts, denials roll back everything and append one separate redacted
audit record. Overlap is re-checked inside the transaction against locked
scope state (lost-update safe); cancel is a soft delete, never physical.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from api.adapters.postgres_calendar import OVERLAP_SQL
from api.application.crm_commands_wu5 import (
    CrmCommandBase,
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.domain.calendar_event import EVENT_SOURCES, EVENT_STATUSES
from api.tenant_boundary import TenantContext

_UNSET: Any = object()
_UUID_FIELDS = ("contact_id", "lead_id", "property_id")


def _parse_iso(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CrmCommandValidationError(f"{label} must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CrmCommandValidationError(f"{label} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise CrmCommandValidationError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_title(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise CrmCommandValidationError("title must be a non-empty string (max 200)")
    if value != value.strip():
        raise CrmCommandValidationError("title must not have leading/trailing whitespace")
    return value.strip()


def _validate_uuid_or_none(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CrmCommandValidationError(f"{label} must be a uuid string or null")
    try:
        UUID(value.strip())
    except ValueError as error:
        raise CrmCommandValidationError(f"{label} must be a uuid string or null") from error
    return value.strip()


def _validate_source(value: Any) -> str:
    if not isinstance(value, str) or value.strip() not in EVENT_SOURCES:
        raise CrmCommandValidationError(f"source must be one of: {', '.join(EVENT_SOURCES)}")
    return value.strip()


async def _require_reference(cur: Any, table: str, ref_id: str, account_id: str,
                             label: str) -> None:
    """Fail closed when a referenced row is missing or foreign (non-enumerating)."""
    await cur.execute(
        f"SELECT 1 FROM {table} WHERE id = %s AND client_account_id = %s",
        (ref_id, account_id),
    )
    if await cur.fetchone() is None:
        raise CrmCommandDenied(f"{label}_not_found_in_scope")


async def _assert_no_overlap(cur: Any, account_id: str, start: datetime, end: datetime,
                             exclude_id: str | None = None) -> None:
    sql = OVERLAP_SQL
    params: list[Any] = [account_id, end, start]
    if exclude_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_id)
    await cur.execute(sql, tuple(params))
    if await cur.fetchone() is not None:
        raise CrmCommandDenied("calendar_slot_overlaps")


class CreateCalendarEventCommand(CrmCommandBase):
    """Atomic event creation with overlap validation and scoped references."""

    operation = "calendar.create"
    audit_resource_type = "calendar_event"
    outbox_event_type = "CalendarEventCreated"

    async def execute_create(self, context: TenantContext | None, *, title: str,
                             starts_at: str, ends_at: str,
                             description: str | None = None,
                             contact_id: str | None = None,
                             lead_id: str | None = None,
                             property_id: str | None = None,
                             notes: str | None = None,
                             source: str = "manual",
                             idempotency_key: str):
        clean_title = _validate_title(title)
        start = _parse_iso(starts_at, "starts_at")
        end = _parse_iso(ends_at, "ends_at")
        if not start < end:
            raise CrmCommandValidationError("starts_at must be before ends_at")
        clean_contact = _validate_uuid_or_none(contact_id, "contact_id")
        clean_lead = _validate_uuid_or_none(lead_id, "lead_id")
        clean_property = _validate_uuid_or_none(property_id, "property_id")
        clean_source = _validate_source(source)
        canonical_input = {
            "title": clean_title, "starts_at": starts_at, "ends_at": ends_at,
            "description": description, "contact_id": clean_contact,
            "lead_id": clean_lead, "property_id": clean_property,
            "notes": notes, "source": clean_source,
        }
        return await self.execute(
            context, idempotency_key=idempotency_key, canonical_input=canonical_input,
            event_id=str(uuid4()), title=clean_title, start=start, end=end,
            description=description, contact_id=clean_contact, lead_id=clean_lead,
            property_id=clean_property, notes=notes, source=clean_source,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        if intent["contact_id"] is not None:
            await _require_reference(cur, "contacts", intent["contact_id"], account_id, "contact")
        if intent["lead_id"] is not None:
            await _require_reference(cur, "leads", intent["lead_id"], account_id, "lead")
        if intent["property_id"] is not None:
            await _require_reference(cur, "properties", intent["property_id"], account_id, "property")
        await _assert_no_overlap(cur, account_id, intent["start"], intent["end"])
        event_id: str = intent["event_id"]
        await cur.execute(
            """
            INSERT INTO calendar_events
              (id, client_account_id, title, description, starts_at, ends_at,
               contact_id, lead_id, property_id, status, source, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'scheduled', %s, %s)
            """,
            (event_id, account_id, intent["title"], intent["description"],
             intent["start"], intent["end"], intent["contact_id"], intent["lead_id"],
             intent["property_id"], intent["source"], intent["notes"]),
        )
        return {"resource_id": event_id, "status": "scheduled",
                "starts_at": intent["start"].isoformat(), "ends_at": intent["end"].isoformat(),
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": payload["status"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"], "status": payload["status"]}


class UpdateCalendarEventCommand(CrmCommandBase):
    """Atomic event update (fields and/or reschedule). Cancelled events are
    terminal; `cancelled` is never reachable through update (use the Cancel
    command). The only allowed status transition here is scheduled->completed."""

    operation = "calendar.update"
    audit_resource_type = "calendar_event"
    outbox_event_type = "CalendarEventUpdated"

    async def execute_update(self, context: TenantContext | None, *, event_id: str,
                             idempotency_key: str, title: Any = _UNSET,
                             description: Any = _UNSET, starts_at: Any = _UNSET,
                             ends_at: Any = _UNSET, contact_id: Any = _UNSET,
                             lead_id: Any = _UNSET, property_id: Any = _UNSET,
                             notes: Any = _UNSET, status: Any = _UNSET):
        clean_id = _validate_uuid_or_none(event_id, "event_id")
        assert clean_id is not None
        patch: dict[str, Any] = {}
        if title is not _UNSET:
            patch["title"] = _validate_title(title)
        if description is not _UNSET:
            if description is not None and not isinstance(description, str):
                raise CrmCommandValidationError("description must be a string or null")
            patch["description"] = description
        if starts_at is not _UNSET:
            patch["starts_at"] = _parse_iso(starts_at, "starts_at").isoformat()
        if ends_at is not _UNSET:
            patch["ends_at"] = _parse_iso(ends_at, "ends_at").isoformat()
        for label in _UUID_FIELDS:
            value = {"contact_id": contact_id, "lead_id": lead_id,
                     "property_id": property_id}[label]
            if value is not _UNSET:
                patch[label] = _validate_uuid_or_none(value, label)
        if notes is not _UNSET:
            if notes is not None and not isinstance(notes, str):
                raise CrmCommandValidationError("notes must be a string or null")
            patch["notes"] = notes
        if status is not _UNSET:
            if status != "completed":
                raise CrmCommandValidationError("status may only transition to 'completed' here")
            patch["status"] = "completed"
        if not patch:
            raise CrmCommandValidationError("no fields to update")
        canonical_input = {"event_id": clean_id, **patch}
        return await self.execute(
            context, idempotency_key=idempotency_key, canonical_input=canonical_input,
            event_id=clean_id, patch=patch,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id, status, starts_at, ends_at FROM calendar_events "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["event_id"], account_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CrmCommandDenied("calendar_event_not_found_in_scope")
        if row["status"] == "cancelled":
            raise CrmCommandDenied("calendar_event_is_cancelled")
        patch: dict[str, Any] = dict(intent["patch"])
        if patch.get("status") == "completed" and row["status"] != "scheduled":
            raise CrmCommandDenied("calendar_status_transition_not_allowed")
        new_start = _parse_iso(patch["starts_at"], "starts_at") if "starts_at" in patch else row["starts_at"]
        new_end = _parse_iso(patch["ends_at"], "ends_at") if "ends_at" in patch else row["ends_at"]
        if not new_start < new_end:
            raise CrmCommandDenied("calendar_time_order_invalid")
        if "starts_at" in patch or "ends_at" in patch:
            await _assert_no_overlap(cur, account_id, new_start, new_end,
                                     exclude_id=intent["event_id"])
        for label, table in (("contact_id", "contacts"), ("lead_id", "leads"),
                             ("property_id", "properties")):
            if label in patch and patch[label] is not None:
                await _require_reference(cur, table, patch[label], account_id,
                                         label.replace("_id", ""))
        columns: list[str] = []
        params: list[Any] = []
        column_map = {"title": "title", "description": "description",
                      "contact_id": "contact_id", "lead_id": "lead_id",
                      "property_id": "property_id", "notes": "notes", "status": "status"}
        for key, column in column_map.items():
            if key in patch:
                columns.append(f"{column} = %s")
                params.append(patch[key])
        if "starts_at" in patch or "ends_at" in patch:
            columns.append("starts_at = %s")
            params.append(new_start)
            columns.append("ends_at = %s")
            params.append(new_end)
        columns.append("updated_at = now()")
        params.extend([intent["event_id"], account_id])
        await cur.execute(
            f"UPDATE calendar_events SET {', '.join(columns)} "
            "WHERE id = %s AND client_account_id = %s",
            tuple(params),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("calendar_update_not_allowed")
        return {"resource_id": intent["event_id"], "status": patch.get("status", row["status"]),
                "starts_at": new_start.isoformat(), "ends_at": new_end.isoformat(),
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": payload["status"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"], "status": payload["status"]}


class CancelCalendarEventCommand(CrmCommandBase):
    """Atomic soft-cancel. A cancelled event stays in the table (audit); only
    `scheduled` events may be cancelled."""

    operation = "calendar.cancel"
    audit_resource_type = "calendar_event"
    outbox_event_type = "CalendarEventCancelled"

    async def execute_cancel(self, context: TenantContext | None, *, event_id: str,
                             idempotency_key: str):
        clean_id = _validate_uuid_or_none(event_id, "event_id")
        assert clean_id is not None
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"event_id": clean_id}, event_id=clean_id,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id, status FROM calendar_events "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["event_id"], account_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CrmCommandDenied("calendar_event_not_found_in_scope")
        if row["status"] != "scheduled":
            raise CrmCommandDenied("calendar_event_not_cancellable")
        await cur.execute(
            "UPDATE calendar_events SET status = 'cancelled', updated_at = now() "
            "WHERE id = %s AND client_account_id = %s",
            (intent["event_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("calendar_cancel_not_allowed")
        return {"resource_id": intent["event_id"], "status": "cancelled",
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": payload["status"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"], "status": payload["status"]}
