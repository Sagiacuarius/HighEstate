"""Work Unit 11 (property-catalog) composition: versioned read APIs + atomic
commands for the property catalog over the accepted tenancy seams.

FastAPI app with fixed static routes under /v1/crm (same prefix discipline as
the accepted WU6 reads; the WU6 app itself is untouched — this is a separate,
named composition). Identity remains server-owned: no route accepts an account
id, every predicate comes from the resolved TenantContext, denials share the
non-enumerating envelope with consistent correlation. Commands reuse the
accepted WU5 machinery (canonical hash, reserve/replay/conflict, transaction,
redacted denial audit, outbox). No Kapso, no LLM, no RLS, no productive auth.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from api.adapters.postgres_commercial_crm import CrmNotFoundError
from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.adapters.postgres_property_catalog import (
    PropertyAmenityRepository,
    PropertyCatalogRepository,
    PropertyContactRepository,
    PropertyInterestRepository,
)
from api.adapters.postgres_tenancy import (
    PostgresMembershipRepository,
    PostgresTenantRepository,
)
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.crm_commands_wu5 import (
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.application.property_commands_wu11 import (
    ChangePropertyStatusCommand,
    CreatePropertyCommand,
    LinkContactToPropertyCommand,
    RegisterPropertyInterestCommand,
    UpdatePropertyCommand,
)
from api.domain.commercial_crm import CrmDomainError
from api.domain.property_catalog import PropertyRecord
from api.http_contracts import envelope
from api.queries.crm_reads import CrmReadNotFound, CrmReadValidation
from api.queries.property_reads_wu11 import PropertyReadService
from api.tenant_boundary import (
    CurrentIdentity,
    TenantAccessDenied,
    TenantAuthenticationRequired,
    TenantContext,
    TenantContextResolver,
)

API_PREFIX = "/v1/crm"
PROPERTIES_ROUTE = f"{API_PREFIX}/properties"
PROPERTY_DETAIL_ROUTE = f"{API_PREFIX}/properties/{{property_id}}"
CONTACT_PROPERTIES_ROUTE = f"{API_PREFIX}/contacts/{{contact_id}}/properties"
AMENITIES_ROUTE = f"{API_PREFIX}/amenities"
CMD_CREATE = f"{PROPERTIES_ROUTE}/commands/create"
CMD_UPDATE = PROPERTY_DETAIL_ROUTE + "/commands/update"
CMD_STATUS = PROPERTY_DETAIL_ROUTE + "/commands/status"
CMD_LINK = PROPERTY_DETAIL_ROUTE + "/commands/link-contact"
CMD_INTEREST = PROPERTY_DETAIL_ROUTE + "/commands/interest"


class PropertyCatalogComposition:
    """Named composition root: resolver + repos + read service + commands."""

    def __init__(self, resolver: TenantContextResolver,
                 properties: PropertyCatalogRepository,
                 service: PropertyReadService,
                 create: CreatePropertyCommand, update: UpdatePropertyCommand,
                 status: ChangePropertyStatusCommand,
                 link: LinkContactToPropertyCommand,
                 interest: RegisterPropertyInterestCommand,
                 target_client_account_id: str):
        self.resolver = resolver
        self.properties = properties
        self.service = service
        self.create = create
        self.update = update
        self.status = status
        self.link = link
        self.interest = interest
        self.target_client_account_id = target_client_account_id


def _key(request: Request, fallback_scope: str) -> str:
    value = request.headers.get("x-idempotency-key", "").strip()
    return value or f"prop-cmd:{fallback_scope}:{uuid4().hex}"


def create_property_catalog_app(
    identity: CurrentIdentity,
    tenants: PostgresTenantRepository,
    memberships: PostgresMembershipRepository,
    target_client_account_id: str,
    conn_kwargs: dict,
) -> FastAPI:
    app = FastAPI(title="property-catalog-wu11", version="v1")
    pool = ConnectionPool(dict(conn_kwargs))
    runner = PostgresRunner(pool)
    resolver = TenantContextResolver(identity, tenants, memberships)

    catalog = PropertyCatalogRepository(runner)
    amenities = PropertyAmenityRepository(runner)
    links = PropertyContactRepository(runner)
    interests = PropertyInterestRepository(runner)
    service = PropertyReadService(runner, properties=catalog, amenities=amenities,
                                  links=links, interests=interests)
    commands = {
        "create": CreatePropertyCommand(dict(conn_kwargs)),
        "update": UpdatePropertyCommand(dict(conn_kwargs)),
        "status": ChangePropertyStatusCommand(dict(conn_kwargs)),
        "link": LinkContactToPropertyCommand(dict(conn_kwargs)),
        "interest": RegisterPropertyInterestCommand(dict(conn_kwargs)),
    }
    composition = PropertyCatalogComposition(
        resolver=resolver, properties=catalog, service=service,
        create=commands["create"], update=commands["update"],
        status=commands["status"], link=commands["link"],
        interest=commands["interest"],
        target_client_account_id=target_client_account_id)
    app.state.catalog_composition = composition
    app.state.target_client_account_id = target_client_account_id

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        value = request.headers.get("x-correlation-id", "").strip() or \
            f"prop-wu11-{uuid4().hex[:12]}"
        request.state.correlation_id = value
        response: Response = await call_next(request)
        response.headers["X-Correlation-Id"] = value
        return response

    async def scoped(request: Request):
        correlation = request.state.correlation_id
        try:
            context: TenantContext = await resolver.resolve(
                request, app.state.target_client_account_id)
            return context, None
        except TenantAuthenticationRequired:
            return None, envelope(401, "authentication_required",
                                  "authentication is required", correlation)
        except TenantAccessDenied:
            return None, envelope(403, "tenant_access_denied",
                                  "access denied", correlation)

    def _command_error(problem: Exception, correlation: str):
        if isinstance(problem, CrmCommandValidationError):
            return envelope(400, "invalid_command", str(problem), correlation)
        if isinstance(problem, CrmDomainError):
            return envelope(400, "invalid_command", str(problem), correlation)
        if isinstance(problem, IdempotencyConflict):
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        if isinstance(problem, CrmCommandDenied):
            # foreign/missing resources share one non-enumerating 403
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        raise problem

    def _bad_uuid(value: str) -> bool:
        import uuid as _uuid
        try:
            _uuid.UUID(value)
            return False
        except (ValueError, AttributeError, TypeError):
            return True

    # ---- read surfaces ----------------------------------------------------

    @app.get(PROPERTIES_ROUTE)
    async def list_properties(request: Request):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        query = dict(request.query_params)
        try:
            payload = await composition.service.list_properties(context, query)
        except CrmReadValidation as problem:
            return envelope(400, "invalid_query", str(problem), correlation)
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    @app.get(AMENITIES_ROUTE)
    async def list_amenities(request: Request):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        payload = await composition.service.amenities(context)
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    @app.get(CONTACT_PROPERTIES_ROUTE)
    async def contact_properties(request: Request, contact_id: str):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        if _bad_uuid(contact_id):
            # malformed path id is indistinguishable from an unknown route;
            # it must never reach a uuid-typed SQL parameter.
            return envelope(404, "route_not_found", "route is not available",
                            correlation)
        payload = await composition.service.contact_properties(context, contact_id)
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    @app.get(PROPERTY_DETAIL_ROUTE)
    async def property_detail(request: Request, property_id: str):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        if _bad_uuid(property_id):
            # malformed path id is indistinguishable from an unknown route;
            # it must never reach a uuid-typed SQL parameter.
            return envelope(404, "route_not_found", "route is not available",
                            correlation)
        try:
            payload = await composition.service.property_detail(context, property_id)
        except CrmReadNotFound:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        except CrmNotFoundError:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    # ---- command surfaces --------------------------------------------------

    @app.post(CMD_CREATE)
    async def create_property(request: Request):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        try:
            body = await request.json()
            record = _record_from_body(context, body)
            amenity_ids = tuple(body.get("amenity_ids") or ())
        except (CrmDomainError, TypeError, ValueError) as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        key = _key(request, f"create:{record.id}")
        try:
            outcome = await composition.create.execute_create(
                context, record=record, idempotency_key=key,
                amenity_ids=amenity_ids)
        except Exception as problem:
            translated = _command_error(problem, correlation)
            if translated is not None:
                return translated
            raise
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation},
                            content={"accepted": True, "replayed": outcome.replayed,
                                     "environment": "dev-only-property-catalog",
                                     "result": outcome.payload,
                                     "correlation_id": correlation})

    @app.post(CMD_UPDATE)
    async def update_property(request: Request, property_id: str):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        if _bad_uuid(property_id):
            return envelope(400, "invalid_command", "property_id is invalid",
                            correlation)
        try:
            body = await request.json()
        except Exception:
            return envelope(400, "invalid_command", "body is required", correlation)
        if not isinstance(body, dict) or "changes" not in body:
            return envelope(400, "invalid_command", "changes object is required",
                            correlation)
        key = _key(request, f"update:{property_id}")
        try:
            outcome = await composition.update.execute_update(
                context, property_id=property_id, changes=body["changes"],
                idempotency_key=key)
        except Exception as problem:
            translated = _command_error(problem, correlation)
            if translated is not None:
                return translated
            raise
        return _command_ok(outcome, correlation)

    @app.post(CMD_STATUS)
    async def change_status(request: Request, property_id: str):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        if _bad_uuid(property_id):
            return envelope(400, "invalid_command", "property_id is invalid",
                            correlation)
        try:
            body = await request.json()
            to_status = body["status"]
        except Exception:
            return envelope(400, "invalid_command", "status is required", correlation)
        key = _key(request, f"status:{property_id}:{uuid4().hex}")
        try:
            outcome = await composition.status.execute_change(
                context, property_id=property_id, to_status=str(to_status),
                idempotency_key=key)
        except Exception as problem:
            translated = _command_error(problem, correlation)
            if translated is not None:
                return translated
            raise
        return _command_ok(outcome, correlation)

    @app.post(CMD_LINK)
    async def link_contact(request: Request, property_id: str):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        if _bad_uuid(property_id):
            return envelope(400, "invalid_command", "property_id is invalid",
                            correlation)
        try:
            body = await request.json()
            contact_id, role = body["contact_id"], body["role"]
        except Exception:
            return envelope(400, "invalid_command",
                            "contact_id and role are required", correlation)
        if _bad_uuid(str(contact_id)):
            return envelope(400, "invalid_command", "contact_id is invalid",
                            correlation)
        key = _key(request, f"link:{property_id}:{contact_id}:{role}")
        try:
            outcome = await composition.link.execute_link(
                context, property_id=property_id, contact_id=str(contact_id),
                role=str(role), idempotency_key=key)
        except Exception as problem:
            translated = _command_error(problem, correlation)
            if translated is not None:
                return translated
            raise
        return _command_ok(outcome, correlation)

    @app.post(CMD_INTEREST)
    async def register_interest(request: Request, property_id: str):
        correlation = request.state.correlation_id
        context, error = await scoped(request)
        if error is not None:
            return error
        if _bad_uuid(property_id):
            return envelope(400, "invalid_command", "property_id is invalid",
                            correlation)
        try:
            body = await request.json()
            contact_id = body["contact_id"]
            origin = str(body.get("origin", "manual"))
        except Exception:
            return envelope(400, "invalid_command", "contact_id is required",
                            correlation)
        if _bad_uuid(str(contact_id)):
            return envelope(400, "invalid_command", "contact_id is invalid",
                            correlation)
        key = _key(request, f"interest:{property_id}:{contact_id}")
        try:
            outcome = await composition.interest.execute_register(
                context, property_id=property_id, contact_id=str(contact_id),
                origin=origin, idempotency_key=key)
        except Exception as problem:
            translated = _command_error(problem, correlation)
            if translated is not None:
                return translated
            raise
        return _command_ok(outcome, correlation)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return envelope(404, "route_not_found", "route is not available",
                        request.state.correlation_id)

    @app.exception_handler(405)
    async def method_not_allowed(request: Request, exc):
        return envelope(405, "method_not_allowed", "method is not allowed",
                        request.state.correlation_id)

    return app


def _command_ok(outcome, correlation: str) -> JSONResponse:
    return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation},
                        content={"accepted": True, "replayed": outcome.replayed,
                                 "environment": "dev-only-property-catalog",
                                 "result": outcome.payload,
                                 "correlation_id": correlation})


def _dec(value):
    from decimal import Decimal
    return None if value is None else Decimal(str(value))


def _dt(value):
    from datetime import datetime
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _record_from_body(context: TenantContext, body: dict) -> PropertyRecord:
    """Client body may only carry the property's own fields. client_account_id
    comes from the SERVER context; ids absent in the body are minted here."""
    allowed_top = {"id", "reference_code", "title", "operation_type", "status",
                   "price_amount", "price_currency", "property_type", "address",
                   "floor_unit", "neighborhood", "city", "latitude", "longitude",
                   "expenses_amount", "accepts_swap", "covered_area_m2",
                   "total_area_m2", "rooms", "bathrooms", "parking_spaces",
                   "property_age_years", "construction_status", "photos",
                   "video_url", "capture_source", "captured_at",
                   "responsible_agent_id", "amenity_ids", "client_account_id"}
    unknown = set(body) - allowed_top
    if unknown:
        raise CrmDomainError(f"unsupported keys: {sorted(unknown)}")
    if "client_account_id" in body and str(body["client_account_id"]) != context.client_account.id:
        raise CrmDomainError("client_account_id is server-derived")
    if not str(body.get("title", "")).strip():
        raise CrmDomainError("Property.title is required")
    return PropertyRecord(
        id=str(body.get("id") or uuid4()),
        client_account_id=context.client_account.id,
        title=body["title"],
        operation_type=body.get("operation_type", "sale"),
        status=body.get("status", "capturing"),
        price_amount=_dec(body.get("price_amount")),
        price_currency=body.get("price_currency", "USD"),
        reference_code=body.get("reference_code"),
        property_type=body.get("property_type"),
        address=body.get("address"),
        floor_unit=body.get("floor_unit"),
        neighborhood=body.get("neighborhood"),
        city=body.get("city"),
        latitude=_dec(body.get("latitude")),
        longitude=_dec(body.get("longitude")),
        expenses_amount=_dec(body.get("expenses_amount")),
        accepts_swap=bool(body.get("accepts_swap", False)),
        covered_area_m2=_dec(body.get("covered_area_m2")),
        total_area_m2=_dec(body.get("total_area_m2")),
        rooms=body.get("rooms"), bathrooms=body.get("bathrooms"),
        parking_spaces=body.get("parking_spaces"),
        property_age_years=body.get("property_age_years"),
        construction_status=body.get("construction_status"),
        photos=list(body.get("photos") or []),
        video_url=body.get("video_url"),
        capture_source=body.get("capture_source"),
        captured_at=_dt(body.get("captured_at")),
        responsible_agent_id=body.get("responsible_agent_id"),
    )


def build_property_catalog_application(conn_kwargs: dict,
                                       target_client_account_id: str,
                                       identity: CurrentIdentity) -> FastAPI:
    """Composition root: fail-fast DB config + tenancy repos + WU11 wiring."""
    pool = ConnectionPool(dict(conn_kwargs))
    runner = PostgresRunner(pool)
    tenants = PostgresTenantRepository(runner)
    memberships = PostgresMembershipRepository(runner)
    return create_property_catalog_app(identity, tenants, memberships,
                                       target_client_account_id, conn_kwargs)
