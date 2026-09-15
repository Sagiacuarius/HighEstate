"""MVP Commercial Demo WU6: tenant-scoped read application service.

Provider-neutral: no FastAPI here. Every method receives the server-resolved
TenantContext (never a caller-supplied account/user/permission), delegates the
mandatory client_account_id predicate to the accepted WU3 repositories
(properties, conversations, contacts/leads) and to the SQL read adapter
(dashboard counts, inbox joins, agenda, activity). Counts are derived from the
database, never from fixtures or in-memory accumulation. Missing/foreign
resource ids share one CrmReadNotFound outcome (non-enumerating).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.adapters.postgres_commercial_crm import CrmNotFoundError
from api.adapters.postgres_crm_reads import PostgresCrmReadRepository
from api.composition_wu3_mvp import CrmRepositoryComposition
from api.tenant_boundary import TenantContext

ACTIVITY_LIMIT_MAX = 200
ACTIVITY_LIMIT_DEFAULT = 50


class CrmReadNotFound(Exception):
    """Missing-in-scope and foreign-in-scope are one safe outcome."""


class CrmReadValidation(Exception):
    """Raised before any SQL when a query parameter violates a pure rule."""


def _validate_activity_limit(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return ACTIVITY_LIMIT_DEFAULT
    try:
        value = int(raw)
    except ValueError as error:
        raise CrmReadValidation("limit must be an integer") from error
    if not 1 <= value <= ACTIVITY_LIMIT_MAX:
        raise CrmReadValidation(f"limit must be between 1 and {ACTIVITY_LIMIT_MAX}")
    return value


def _property_view(property_entity: Any) -> dict[str, Any]:
    return {
        "property_id": property_entity.id,
        "title": property_entity.title,
        "operation_type": property_entity.operation_type,
        "location": property_entity.location,
        "bedrooms": property_entity.bedrooms,
        "price": str(property_entity.price),
        "currency": property_entity.currency,
        "availability": property_entity.availability,
    }


class CrmReadService:
    """All versioned read views for the demo surfaces, scoped by context."""

    def __init__(self, crm: CrmRepositoryComposition, reads: PostgresCrmReadRepository):
        self._crm = crm
        self._reads = reads

    async def _server_meta(self, context: TenantContext) -> dict[str, Any]:
        return {
            "account_id": context.client_account.id,
            "as_of": await self._reads.server_now(context),
            "correlation_id": context.correlation_id,
        }

    async def dashboard(self, context: TenantContext) -> dict[str, Any]:
        counts = await self._reads.dashboard_counts(context)
        meta = await self._server_meta(context)
        return {"counts": counts, **meta}

    async def inbox(self, context: TenantContext) -> dict[str, Any]:
        items = await self._reads.inbox_items(context)
        # last-read state is persisted, not fixture-derived: reuse the WU3 entity read
        meta = await self._server_meta(context)
        return {"items": items, "count": len(items), **meta}

    async def leads(self, context: TenantContext) -> dict[str, Any]:
        views = await self._reads.lead_views(context)
        entities = await self._crm.contacts.list_leads(context)
        # cross-check: the read model must agree with the entity repository
        if {v["lead_id"] for v in views} != {e.id for e in entities}:
            raise CrmReadNotFound("lead read model diverged")
        meta = await self._server_meta(context)
        return {"items": views, "count": len(views), **meta}

    async def properties(self, context: TenantContext) -> dict[str, Any]:
        entities = await self._crm.properties.list(context)
        items = [_property_view(p) for p in entities]
        meta = await self._server_meta(context)
        return {"items": items, "count": len(items), **meta}

    async def property_detail(self, context: TenantContext, property_id: str) -> dict[str, Any]:
        entity = await self._crm.properties.get(context, property_id)
        if entity is None:
            raise CrmReadNotFound("property is not available in this account scope")
        view = _property_view(entity)
        view.update(await self._server_meta(context))
        return view

    async def pipeline(self, context: TenantContext) -> dict[str, Any]:
        views = await self._reads.pipeline_views(context)
        # entity-level cross-check with the accepted WU3 pipeline repository
        entities = await self._crm.pipelines.list(context)
        if {v["pipeline_id"] for v in views} != {e.id for e in entities}:
            raise CrmReadNotFound("pipeline read model diverged")
        meta = await self._server_meta(context)
        return {"items": views, "count": len(views), **meta}

    async def agenda(self, context: TenantContext) -> dict[str, Any]:
        payload = await self._reads.agenda(context)
        meta = await self._server_meta(context)
        return {**payload, **meta}

    async def activity(self, context: TenantContext, limit_raw: str | None) -> dict[str, Any]:
        limit = _validate_activity_limit(limit_raw)
        payload = await self._reads.activity(context, limit)
        meta = await self._server_meta(context)
        return {**payload, **meta}

    async def conversation_messages(self, context: TenantContext, conversation_id: str) -> dict[str, Any]:
        if not await self._reads.conversation_exists(context, conversation_id):
            raise CrmReadNotFound("conversation is not available in this account scope")
        try:
            messages = await self._crm.conversations.get_messages(context, conversation_id)
        except CrmNotFoundError:
            raise CrmReadNotFound("conversation is not available in this account scope") from None
        items = [{
            "message_id": m.id, "direction": m.direction, "body": m.body,
            "created_at": (m.created_at.astimezone(timezone.utc)
                           .isoformat(timespec="milliseconds").replace("+00:00", "Z")
                           if m.created_at else None),
        } for m in messages]
        meta = await self._server_meta(context)
        return {"conversation_id": conversation_id, "items": items, "count": len(items), **meta}
