"""Work Unit 13 (Calendar / Agenda): calendar event commands + reads.

Thin WU13-owned glue over accepted seams — nothing accepted is modified:
CreateCalendarEventCommand / UpdateCalendarEventCommand /
CancelCalendarEventCommand (WU13), CalendarReadService + CalendarEventRepository
(WU13), TenantContextResolver (accepted tenant boundary), the WU8 dashboard
composition's app.state (resolver, scoped cache).

Mounted as an APIRouter onto the WU8 app (composition_wu8_mvp) so selector and
the demo account session are shared with the dashboard.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_calendar import CalendarEventRepository
from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.calendar_commands_wu13 import (
    CancelCalendarEventCommand,
    CreateCalendarEventCommand,
    UpdateCalendarEventCommand,
    _UNSET,
)
from api.application.crm_commands_wu5 import (
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.http_contracts import envelope
from api.queries.calendar_reads_wu13 import CalendarReadService
from api.queries.crm_reads import CrmReadValidation
from api.tenant_boundary import (
    TenantAccessDenied,
    TenantAuthenticationRequired,
)

CALENDAR_PREFIX = "/calendar"
EVENTS_ROUTE = f"{CALENDAR_PREFIX}/events"
AVAILABILITY_ROUTE = f"{CALENDAR_PREFIX}/availability"
CALENDAR_ENVIRONMENT = "dev-only-calendar"

_CREATE_KEYS = frozenset({
    "title", "description", "startsAt", "endsAt", "contactId", "leadId",
    "propertyId", "notes", "source", "idempotencyKey",
})
_UPDATE_KEYS = frozenset({
    "title", "description", "startsAt", "endsAt", "contactId", "leadId",
    "propertyId", "notes", "status", "idempotencyKey",
})


def _key(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"calendar:{uuid4().hex}"


def build_calendar_router(conn_kwargs: dict[str, Any]) -> APIRouter:
    """Calendar event surface: manual CRUD + availability reads."""
    router = APIRouter()
    runner = PostgresRunner(ConnectionPool(dict(conn_kwargs)))
    create_cmd = CreateCalendarEventCommand(dict(conn_kwargs))
    update_cmd = UpdateCalendarEventCommand(dict(conn_kwargs))
    cancel_cmd = CancelCalendarEventCommand(dict(conn_kwargs))
    reads = CalendarReadService(runner, events=CalendarEventRepository(runner))

    async def _context(request: Request, correlation: str):
        composition = request.app.state.demo_composition
        try:
            context = await composition.resolver.resolve(
                request, request.app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return None, envelope(401, "authentication_required",
                                  "authentication is required", correlation)
        except TenantAccessDenied:
            return None, envelope(403, "tenant_access_denied", "access denied", correlation)
        return context, None

    @router.post(EVENTS_ROUTE)
    async def create_event(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            raw = await request.json()
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            return envelope(400, "invalid_command", "event body must be an object", correlation)
        unknown = set(raw.keys()) - _CREATE_KEYS
        if unknown:
            return envelope(400, "invalid_command",
                            f"unsupported keys: {sorted(unknown)}", correlation)
        try:
            outcome = await create_cmd.execute_create(
                context, title=raw.get("title"), starts_at=raw.get("startsAt"),
                ends_at=raw.get("endsAt"), description=raw.get("description"),
                contact_id=raw.get("contactId"), lead_id=raw.get("leadId"),
                property_id=raw.get("propertyId"), notes=raw.get("notes"),
                source=raw.get("source", "manual"),
                idempotency_key=_key(raw.get("idempotencyKey")),
            )
        except CrmCommandDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": CALENDAR_ENVIRONMENT,
            "accepted": True, "replayed": outcome.replayed,
            "correlation_id": correlation, "result": outcome.payload,
        })

    @router.put(EVENTS_ROUTE + "/{event_id}")
    async def update_event(request: Request, event_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            raw = await request.json()
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            return envelope(400, "invalid_command", "event body must be an object", correlation)
        unknown = set(raw.keys()) - _UPDATE_KEYS
        if unknown:
            return envelope(400, "invalid_command",
                            f"unsupported keys: {sorted(unknown)}", correlation)

        def _field(name: str):
            return raw[name] if name in raw else _UNSET

        try:
            outcome = await update_cmd.execute_update(
                context, event_id=event_id, idempotency_key=_key(raw.get("idempotencyKey")),
                title=_field("title"), description=_field("description"),
                starts_at=_field("startsAt"), ends_at=_field("endsAt"),
                contact_id=_field("contactId"), lead_id=_field("leadId"),
                property_id=_field("propertyId"), notes=_field("notes"),
                status=_field("status"),
            )
        except CrmCommandDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": CALENDAR_ENVIRONMENT,
            "accepted": True, "replayed": outcome.replayed,
            "correlation_id": correlation, "result": outcome.payload,
        })

    @router.delete(EVENTS_ROUTE + "/{event_id}")
    async def cancel_event(request: Request, event_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        # idempotency key travels as a query param on DELETE (no body).
        key = request.query_params.get("idempotencyKey")
        try:
            outcome = await cancel_cmd.execute_cancel(
                context, event_id=event_id, idempotency_key=_key(key),
            )
        except CrmCommandDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": CALENDAR_ENVIRONMENT,
            "accepted": True, "replayed": outcome.replayed,
            "correlation_id": correlation, "result": outcome.payload,
        })

    @router.get(EVENTS_ROUTE)
    async def list_events(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            payload = await reads.list_events(context, dict(request.query_params))
        except CrmReadValidation as problem:
            return envelope(400, "invalid_query", str(problem), correlation)
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    @router.get(AVAILABILITY_ROUTE)
    async def availability(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            payload = await reads.availability(context, dict(request.query_params))
        except CrmReadValidation as problem:
            return envelope(400, "invalid_query", str(problem), correlation)
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    return router
