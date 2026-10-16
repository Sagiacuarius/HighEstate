"""Current-demo property writes over the accepted WU11 commands."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.crm_commands_wu5 import CrmCommandDenied, CrmCommandValidationError
from api.application.property_commands_wu11 import CreatePropertyCommand, UpdatePropertyCommand
from api.domain.commercial_crm import CrmDomainError
from api.domain.property_catalog import PropertyRecord
from api.http_contracts import envelope
from api.tenant_boundary import TenantAccessDenied, TenantAuthenticationRequired, TenantContext

ENVIRONMENT = "dev-only-property-catalog"
PROPERTY_ROUTE = "/properties"
PROPERTY_DETAIL_ROUTE = "/properties/{property_id}"
CREATE_KEYS = frozenset({
    "id", "reference_code", "title", "operation_type", "status", "price_amount",
    "price_currency", "property_type", "address", "neighborhood", "city", "rooms",
    "bedrooms", "bathrooms", "parking_spaces", "covered_area_m2", "total_area_m2",
    "amenity_ids",
})
UPDATE_KEYS = frozenset(CREATE_KEYS - {"id", "status", "amenity_ids", "bedrooms"})


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception as error:
        raise CrmDomainError(f"{field} must be a number") from error


def _body_error(raw: Any, allowed: frozenset[str]) -> str | None:
    if not isinstance(raw, dict):
        return "body must be an object"
    unknown = set(raw) - allowed
    if unknown:
        return f"unsupported keys: {sorted(unknown)}"
    return None


def _record(context: TenantContext, body: dict[str, Any]) -> PropertyRecord:
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise CrmDomainError("Property.title is required")
    if body.get("price_amount") in (None, ""):
        raise CrmDomainError("Property.price_amount is required")
    bedrooms = body.get("bedrooms", body.get("rooms"))
    if "rooms" in body and "bedrooms" in body:
        raise CrmDomainError("rooms and bedrooms are mutually exclusive")
    amenity_ids = body.get("amenity_ids", ())
    if not isinstance(amenity_ids, list) or any(not isinstance(item, str) or not item.strip() for item in amenity_ids):
        raise CrmDomainError("amenity_ids must be a list of non-empty strings")
    return PropertyRecord(
        id=str(body.get("id") or uuid4()), client_account_id=context.client_account.id,
        title=title, operation_type=body.get("operation_type", "sale"),
        status=body.get("status", "capturing"), price_amount=_decimal(body.get("price_amount"), "price_amount") or Decimal(0),
        price_currency=body.get("price_currency", "USD"), reference_code=body.get("reference_code"),
        property_type=body.get("property_type"), address=body.get("address"),
        neighborhood=body.get("neighborhood"), city=body.get("city"), rooms=bedrooms,
        bathrooms=body.get("bathrooms"), parking_spaces=body.get("parking_spaces"),
        covered_area_m2=_decimal(body.get("covered_area_m2"), "covered_area_m2"),
        total_area_m2=_decimal(body.get("total_area_m2"), "total_area_m2"),
    )


def build_property_catalog_router(conn_kwargs: dict[str, Any]) -> APIRouter:
    router = APIRouter()
    create = CreatePropertyCommand(dict(conn_kwargs))
    update = UpdatePropertyCommand(dict(conn_kwargs))

    async def context(request: Request, correlation: str):
        composition = request.app.state.demo_composition
        try:
            return await composition.resolver.resolve(request, request.app.state.target_client_account_id), None
        except TenantAuthenticationRequired:
            return None, envelope(401, "authentication_required", "authentication is required", correlation)
        except TenantAccessDenied:
            return None, envelope(403, "tenant_access_denied", "access denied", correlation)

    def command_error(problem: Exception, correlation: str):
        if isinstance(problem, (CrmCommandValidationError, CrmDomainError)):
            return envelope(400, "invalid_command", str(problem), correlation)
        if isinstance(problem, IdempotencyConflict):
            return envelope(409, "tenant_idempotency_conflict", "command idempotency conflict", correlation)
        if isinstance(problem, CrmCommandDenied):
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        raise problem

    def ok(outcome, correlation: str) -> JSONResponse:
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": ENVIRONMENT, "accepted": True,
            "replayed": outcome.replayed, "correlation_id": correlation, "result": outcome.payload,
        })

    @router.post(PROPERTY_ROUTE)
    async def create_property(request: Request):
        correlation = request.state.correlation_id
        tenant, error = await context(request, correlation)
        if error is not None:
            return error
        try:
            body = await request.json()
            if (message := _body_error(body, CREATE_KEYS)):
                return envelope(400, "invalid_command", message, correlation)
            record = _record(tenant, body)
        except (CrmDomainError, TypeError, ValueError) as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        key = request.headers.get("x-idempotency-key", "").strip() or f"property:create:{record.id}"
        try:
            outcome = await create.execute_create(tenant, record=record, idempotency_key=key,
                                                  amenity_ids=tuple(body.get("amenity_ids", ())))
        except Exception as problem:
            return command_error(problem, correlation)
        return ok(outcome, correlation)

    @router.put(PROPERTY_DETAIL_ROUTE)
    async def update_property(request: Request, property_id: str):
        correlation = request.state.correlation_id
        tenant, error = await context(request, correlation)
        if error is not None:
            return error
        try:
            UUID(property_id)
            body = await request.json()
            if (message := _body_error(body, UPDATE_KEYS)):
                return envelope(400, "invalid_command", message, correlation)
            changes = dict(body)
            if "bedrooms" in changes:
                changes["rooms"] = changes.pop("bedrooms")
            if "title" in changes and (not isinstance(changes["title"], str) or not changes["title"].strip()):
                raise CrmDomainError("Property.title is required")
            for field in ("price_amount", "covered_area_m2", "total_area_m2"):
                if field in changes:
                    changes[field] = _decimal(changes[field], field)
        except (CrmDomainError, TypeError, ValueError) as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        key = request.headers.get("x-idempotency-key", "").strip() or f"property:update:{property_id}"
        try:
            outcome = await update.execute_update(tenant, property_id=property_id, changes=changes,
                                                  idempotency_key=key)
        except Exception as problem:
            return command_error(problem, correlation)
        return ok(outcome, correlation)

    return router
