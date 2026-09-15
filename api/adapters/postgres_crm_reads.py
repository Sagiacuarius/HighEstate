"""MVP Commercial Demo WU6: tenant-scoped SQL reads for the versioned read APIs.

psycopg stays inside this adapter layer. Every method requires the server-derived
TenantContext (same _require guard as the accepted WU3 repositories) and derives
the mandatory `client_account_id = %s` predicate ONLY from that context — no
client-supplied scope ever reaches SQL. Counts are derived from the database
here (COUNT/GROUP BY), never computed from fixtures or in-memory lists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from api.adapters.postgres_commercial_crm import _require
from api.adapters.postgres_core import PostgresRunner
from api.tenant_boundary import TenantContext


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return str(value)


def _scalar(row: dict[str, Any], key: str) -> int:
    return int(row[key])


class PostgresCrmReadRepository:
    """Read-model queries for the demo surfaces. Reuses the WU3 guard/runner seams."""

    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    # -- dashboard ---------------------------------------------------------
    async def dashboard_counts(self, context: TenantContext) -> dict[str, Any]:
        account_id = _require(context)
        leads = await self._runner.query(
            """
            SELECT status, count(*) AS n FROM leads
            WHERE client_account_id = %s GROUP BY status
            """, (account_id,))
        props = await self._runner.query(
            """
            SELECT availability, count(*) AS n FROM properties
            WHERE client_account_id = %s GROUP BY availability
            """, (account_id,))
        conversations = await self._runner.query(
            """
            SELECT count(*) AS total,
                   COALESCE(sum(unread_count), 0) AS unread
            FROM conversations
            WHERE client_account_id = %s AND status = 'open'
            """, (account_id,))
        follow_ups = await self._runner.query(
            """
            SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
                   count(*) FILTER (WHERE status = 'pending' AND due_at <= now()) AS overdue
            FROM follow_ups WHERE client_account_id = %s
            """, (account_id,))
        stages = await self._runner.query(
            """
            SELECT s.id AS stage_id, s.name AS stage_name, s.position, count(l.id) AS n
            FROM pipeline_stages s
            LEFT JOIN leads l ON l.stage_id = s.id AND l.client_account_id = s.client_account_id
            WHERE s.client_account_id = %s
            GROUP BY s.id, s.name, s.position
            ORDER BY s.position, s.id
            """, (account_id,))
        by_lead = {r["status"]: _scalar(r, "n") for r in leads}
        by_prop = {r["availability"]: _scalar(r, "n") for r in props}
        conv = conversations[0]
        fu = follow_ups[0]
        return {
            "leads_total": sum(by_lead.values()),
            "leads_by_status": by_lead,
            "properties_total": sum(by_prop.values()),
            "properties_by_availability": by_prop,
            "conversations_open": _scalar(conv, "total"),
            "unread_messages": int(conv["unread"]),
            "follow_ups_pending": _scalar(fu, "pending"),
            "follow_ups_overdue": _scalar(fu, "overdue"),
            "leads_by_stage": [
                {"stage_id": str(r["stage_id"]), "stage_name": r["stage_name"],
                 "position": r["position"], "count": _scalar(r, "n")}
                for r in stages],
        }

    # -- inbox -------------------------------------------------------------
    async def inbox_items(self, context: TenantContext) -> list[dict[str, Any]]:
        account_id = _require(context)
        rows = await self._runner.query(
            """
            SELECT c.id, c.contact_id, ct.name AS contact_name, c.status,
                   c.unread_count, c.updated_at,
                   m.id AS last_message_id, m.direction AS last_direction,
                   m.body AS last_body, m.created_at AS last_created_at
            FROM conversations c
            JOIN contacts ct ON ct.id = c.contact_id
                             AND ct.client_account_id = c.client_account_id
            LEFT JOIN LATERAL (
              SELECT id, direction, body, created_at FROM messages
              WHERE conversation_id = c.id AND client_account_id = c.client_account_id
              ORDER BY created_at DESC, id DESC LIMIT 1
            ) m ON TRUE
            WHERE c.client_account_id = %s
            ORDER BY c.unread_count DESC, c.updated_at DESC, c.id
            """, (account_id,))
        return [{
            "conversation_id": str(r["id"]),
            "contact_id": str(r["contact_id"]),
            "contact_name": r["contact_name"],
            "status": r["status"],
            "unread_count": r["unread_count"],
            "updated_at": _iso(r["updated_at"]),
            "last_message": None if r["last_message_id"] is None else {
                "message_id": str(r["last_message_id"]),
                "direction": r["last_direction"],
                "body": r["last_body"],
                "created_at": _iso(r["last_created_at"]),
            },
        } for r in rows]

    async def conversation_exists(self, context: TenantContext, conversation_id: str) -> bool:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT 1 AS ok FROM conversations WHERE id = %s AND client_account_id = %s",
            (conversation_id, account_id))
        return bool(rows)

    # -- leads ---------------------------------------------------------------
    async def lead_views(self, context: TenantContext) -> list[dict[str, Any]]:
        account_id = _require(context)
        rows = await self._runner.query(
            """
            SELECT l.id, l.contact_id, ct.name AS contact_name, l.pipeline_id,
                   p.name AS pipeline_name, l.stage_id, s.name AS stage_name,
                   l.status, l.selected_property_id, l.source, l.created_at, l.updated_at
            FROM leads l
            JOIN contacts ct ON ct.id = l.contact_id AND ct.client_account_id = l.client_account_id
            JOIN pipelines p ON p.id = l.pipeline_id AND p.client_account_id = l.client_account_id
            JOIN pipeline_stages s ON s.id = l.stage_id AND s.client_account_id = l.client_account_id
            WHERE l.client_account_id = %s
            ORDER BY l.updated_at DESC, l.id
            """, (account_id,))
        return [{
            "lead_id": str(r["id"]), "contact_id": str(r["contact_id"]),
            "contact_name": r["contact_name"], "pipeline_id": str(r["pipeline_id"]),
            "pipeline_name": r["pipeline_name"], "stage_id": str(r["stage_id"]),
            "stage_name": r["stage_name"], "status": r["status"],
            "selected_property_id": str(r["selected_property_id"]) if r["selected_property_id"] else None,
            "source": r["source"],
            "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]),
        } for r in rows]

    # -- pipeline ------------------------------------------------------------
    async def pipeline_views(self, context: TenantContext) -> list[dict[str, Any]]:
        account_id = _require(context)
        pipelines = await self._runner.query(
            "SELECT id, name FROM pipelines WHERE client_account_id = %s ORDER BY created_at, id",
            (account_id,))
        stages = await self._runner.query(
            """
            SELECT s.id, s.pipeline_id, s.name, s.position, count(l.id) AS n
            FROM pipeline_stages s
            LEFT JOIN leads l ON l.stage_id = s.id AND l.client_account_id = s.client_account_id
            WHERE s.client_account_id = %s
            GROUP BY s.id, s.pipeline_id, s.name, s.position
            ORDER BY s.position, s.id
            """, (account_id,))
        return [{
            "pipeline_id": str(p["id"]), "name": p["name"],
            "stages": [
                {"stage_id": str(s["id"]), "name": s["name"], "position": s["position"],
                 "lead_count": _scalar(s, "n")}
                for s in stages if str(s["pipeline_id"]) == str(p["id"])],
        } for p in pipelines]

    # -- agenda / follow-ups ---------------------------------------------------
    async def agenda(self, context: TenantContext) -> dict[str, Any]:
        account_id = _require(context)
        rows = await self._runner.query(
            """
            SELECT f.id, f.lead_id, f.contact_id, COALESCE(ct.name, '') AS contact_name,
                   f.title, f.due_at, f.status,
                   (f.status = 'pending' AND f.due_at <= now()) AS overdue,
                   (f.status = 'pending' AND f.due_at > now()
                     AND (f.due_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date) AS due_today
            FROM follow_ups f
            LEFT JOIN contacts ct ON ct.id = f.contact_id AND ct.client_account_id = f.client_account_id
            WHERE f.client_account_id = %s
            ORDER BY f.status = 'pending' DESC, f.due_at, f.id
            """, (account_id,))
        return {
            "items": [{
                "follow_up_id": str(r["id"]),
                "lead_id": str(r["lead_id"]) if r["lead_id"] else None,
                "contact_id": str(r["contact_id"]) if r["contact_id"] else None,
                "contact_name": r["contact_name"],
                "title": r["title"], "due_at": _iso(r["due_at"]), "status": r["status"],
                "overdue": bool(r["overdue"]), "due_today": bool(r["due_today"]),
            } for r in rows],
            "counts": {
                "pending": sum(1 for r in rows if r["status"] == "pending"),
                "overdue": sum(1 for r in rows if r["overdue"]),
                "due_today": sum(1 for r in rows if r["due_today"]),
                "completed": sum(1 for r in rows if r["status"] == "completed"),
            },
        }

    # -- activity ---------------------------------------------------------------
    async def activity(self, context: TenantContext, limit: int) -> dict[str, Any]:
        """Own-account audit trail only. Rows whose client_account_id is NULL are
        cross-context events and stay invisible here (non-enumerating). Metadata is
        never returned: action/outcome/context class/correlation is all the view shows."""
        account_id = _require(context)
        rows = await self._runner.query(
            """
            SELECT id, action, outcome, resource_type, resource_id, actor_id,
                   correlation_id, idempotency_key, metadata->>'reason' AS reason, created_at
            FROM audit_records
            WHERE client_account_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """, (account_id, limit))
        total = await self._runner.query(
            "SELECT count(*) AS n FROM audit_records WHERE client_account_id = %s",
            (account_id,))
        return {
            "items": [{
                "activity_id": str(r["id"]), "action": r["action"], "outcome": r["outcome"],
                "resource_type": r["resource_type"],
                "resource_id": str(r["resource_id"]) if r["resource_id"] else None,
                "actor_id": str(r["actor_id"]), "correlation_id": r["correlation_id"],
                "idempotency_key": r["idempotency_key"],
                "reason": (r["reason"] if r["outcome"] != "success" else None),
                "created_at": _iso(r["created_at"]),
            } for r in rows],
            "total": _scalar(total[0], "n"),
            "limit": limit,
        }

    # -- clock for envelope metadata ------------------------------------------
    async def server_now(self, context: TenantContext) -> str:
        _require(context)
        rows = await self._runner.query("SELECT now() AS now")
        return _iso(rows[0]["now"]) or ""


def decimal_to_str(value: Decimal | int | float) -> str:
    return str(value)
