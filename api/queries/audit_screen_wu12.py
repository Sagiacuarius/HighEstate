"""WU12 (audit-screen) query service: filter validation, keyset cursor codec,
and the redacted projection over the audit SQL adapter.

Provider-neutral like the accepted WU6 reads: every value is validated BEFORE
SQL (uuid shape, ISO-8601 timestamptz-parseable text, action charset/length,
limit bounds), invalid input raises CrmReadValidation (400 invalid_query at
the route), and the response NEVER serializes metadata (only the already
redacted non-success `reason` class, mirroring the accepted activity view).

The cursor is an opaque base64url token of "iso_ts|uuid" carrying NO
authority: the account predicate is re-derived from the server context on
every page, so a captured cursor from another scope can only ever page within
the caller's own. Tampered/garbage cursors fail closed (CrmReadValidation).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timezone
from typing import Any

from api.queries.crm_reads import CrmReadValidation

AUDIT_LIMIT_DEFAULT = 25
AUDIT_LIMIT_MAX = 100
ACTION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_MAX_ACTION_LEN = 128
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _validate_uuid_filter(raw: str | None, label: str) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if not _UUID_PATTERN.match(value):
        raise CrmReadValidation(f"{label} must be a uuid")
    return value


def _validate_action(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if len(value) > _MAX_ACTION_LEN or not ACTION_PATTERN.match(value) or value != raw:
        raise CrmReadValidation("action is invalid")
    return value


def _validate_iso(raw: str | None, label: str) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CrmReadValidation(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise CrmReadValidation(f"{label} must carry a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _validate_limit(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return AUDIT_LIMIT_DEFAULT
    try:
        value = int(raw)
    except ValueError as error:
        raise CrmReadValidation("limit must be an integer") from error
    if not 1 <= value <= AUDIT_LIMIT_MAX:
        raise CrmReadValidation(f"limit must be between 1 and {AUDIT_LIMIT_MAX}")
    return value


def encode_cursor(created_at_iso: str, row_id: str) -> str:
    token = json.dumps([created_at_iso, row_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(token).decode("ascii").rstrip("=")


def decode_cursor(raw: str | None) -> tuple[str, str] | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise CrmReadValidation("cursor is malformed") from error
    if (not isinstance(decoded, list) or len(decoded) != 2
            or not isinstance(decoded[0], str) or not isinstance(decoded[1], str)
            or not _UUID_PATTERN.match(decoded[1])):
        raise CrmReadValidation("cursor is malformed")
    try:
        parsed = datetime.fromisoformat(decoded[0].replace("Z", "+00:00"))
    except ValueError as error:
        raise CrmReadValidation("cursor is malformed") from error
    if parsed.tzinfo is None:
        raise CrmReadValidation("cursor is malformed")
    return decoded[0], decoded[1]


def _iso(value: Any) -> str:
    # Microsecond precision: the DB stores microseconds and from/to filters
    # are compared inclusively against serialized values the client echoes
    # back; truncating to milliseconds would make a boundary row
    # (created_at == displayed value) unselectable.
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _cursor_iso(value: Any) -> str:
    # Keyset cursor must keep full DB precision (microseconds): a truncated
    # millisecond bound would silently drop rows that sort between the
    # truncated and the real timestamp of the page boundary.
    return _iso(value)


def audit_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Projection without metadata: only columns + non-success reason class."""
    return {
        "audit_id": str(row["id"]),
        "action": row["action"],
        "outcome": row["outcome"],
        "resource_type": row["resource_type"],
        "resource_id": str(row["resource_id"]) if row["resource_id"] is not None else None,
        "actor_id": str(row["actor_id"]) if row["actor_id"] is not None else None,
        "correlation_id": row["correlation_id"],
        "idempotency_key": row["idempotency_key"],
        "reason": row["reason"] if row["outcome"] != "success" else None,
        "created_at": _iso(row["created_at"]),
    }


class AuditScreenService:
    def __init__(self, runner, *, audit):
        self._runner = runner
        self._audit = audit

    async def _server_meta(self, context) -> dict[str, Any]:
        rows = await self._runner.query("SELECT now() AS now")
        now = rows[0]["now"].astimezone(timezone.utc)
        return {
            "account_id": context.client_account.id,
            "as_of": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "correlation_id": context.correlation_id,
        }

    async def list_audit(self, context, query: dict[str, str | None]) -> dict[str, Any]:
        filters = {
            "contact_id": _validate_uuid_filter(query.get("contact_id"), "contact_id"),
            "property_id": _validate_uuid_filter(query.get("property_id"), "property_id"),
            "user_id": _validate_uuid_filter(query.get("user_id"), "user_id"),
            "action": _validate_action(query.get("action")),
            "from": _validate_iso(query.get("from"), "from"),
            "to": _validate_iso(query.get("to"), "to"),
        }
        if filters["from"] is not None and filters["to"] is not None \
                and filters["from"] > filters["to"]:
            raise CrmReadValidation("from must be <= to")
        limit = _validate_limit(query.get("limit"))
        cursor = decode_cursor(query.get("cursor"))

        rows = await self._audit.query_audit(
            context, contact_id=filters["contact_id"], property_id=filters["property_id"],
            user_id=filters["user_id"], action=filters["action"],
            date_from=filters["from"], date_to=filters["to"],
            cursor=cursor, limit=limit + 1)
        has_more = len(rows) > limit
        page = rows[:limit]
        items = [audit_entry(r) for r in page]
        next_cursor = (encode_cursor(_cursor_iso(page[-1]["created_at"]), str(page[-1]["id"]))
                       if has_more and page else None)
        if cursor is None:
            total = await self._audit.total_matching(
                context, contact_id=filters["contact_id"], property_id=filters["property_id"],
                user_id=filters["user_id"], action=filters["action"],
                date_from=filters["from"], date_to=filters["to"])
        else:
            total = len(items)  # pages after the first only guarantee the page shape
        meta = await self._server_meta(context)
        return {
            "items": items,
            "count": len(items),
            "total": total,
            "limit": limit,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "filters": {k: filters[k] for k in
                        ("contact_id", "property_id", "user_id", "action", "from", "to")},
            **meta,
        }
