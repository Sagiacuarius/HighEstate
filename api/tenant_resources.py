"""Minimal internal HTTP boundary proving PostgreSQL repository wiring (WU4).

Not a product UI and not authenticated for production: the tenant context is
derived exclusively from the composition-injected IdentityAdapter + PostgreSQL
resolver. Client payloads carry only resource ids/keys — never userId,
agencyId, accountId or permissions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_core import CrossTenantAccessDenied
from api.adapters.postgres_resources import PostgresTenantResourceRepository
from api.http_contracts import envelope
from api.tenant_boundary import (
    TenantAccessDenied,
    TenantAuthenticationRequired,
    TenantContext,
    TenantContextResolver,
    get_tenant_resolver,
)

router = APIRouter(prefix="/tenant/resources", tags=["tenant-resources"])


def get_resource_repository(request: Request) -> PostgresTenantResourceRepository | None:
    composition = getattr(request.app.state, "tenant_composition", None)
    return getattr(composition, "resource_repository", None)


async def _context(request: Request, resolver: TenantContextResolver | None) -> TenantContext:
    assert resolver is not None
    # The requested account id comes from server composition state (the test
    # identity's target account), never from a client-supplied authority field.
    target = getattr(request.app.state, "target_client_account_id", "")
    return await resolver.resolve(request, target)


def _json_ok(correlation: str, payload: Any) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        headers={"X-Correlation-Id": correlation},
        content={"contract_version": "v1", "correlation_id": correlation, **payload},
    )


@router.get("/")
async def list_resources(
    request: Request,
    resolver: TenantContextResolver | None = Depends(get_tenant_resolver),
    repo: PostgresTenantResourceRepository | None = Depends(get_resource_repository),
) -> JSONResponse:
    correlation = getattr(request.state, "correlation_id", "")
    if resolver is None or repo is None:
        return envelope(503, "tenant_service_not_configured", "Tenant service is not configured", correlation)
    try:
        context = await _context(request, resolver)
    except TenantAuthenticationRequired:
        return envelope(401, "authentication_required", "Authentication required", correlation)
    except TenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    items = await repo.list(context)
    return _json_ok(correlation, {"items": items})


@router.post("/")
async def create_resource(
    request: Request,
    resolver: TenantContextResolver | None = Depends(get_tenant_resolver),
    repo: PostgresTenantResourceRepository | None = Depends(get_resource_repository),
) -> JSONResponse:
    correlation = getattr(request.state, "correlation_id", "")
    if resolver is None or repo is None:
        return envelope(503, "tenant_service_not_configured", "Tenant service is not configured", correlation)
    try:
        context = await _context(request, resolver)
        body = await request.json()
        requested_account = body.get("client_account_id")
        if requested_account is not None and str(requested_account) != context.client_account.id:
            raise CrossTenantAccessDenied("client_account_id does not match server-derived context")
        created = await repo.create(
            context,
            resource_id=str(body.get("id")),
            resource_key=str(body.get("resource_key")),
            data=body.get("data", {}),
        )
    except TenantAuthenticationRequired:
        return envelope(401, "authentication_required", "Authentication required", correlation)
    except TenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    except CrossTenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    return _json_ok(correlation, {"item": created})


@router.get("/{resource_id}")
async def read_resource(
    request: Request,
    resource_id: str,
    resolver: TenantContextResolver | None = Depends(get_tenant_resolver),
    repo: PostgresTenantResourceRepository | None = Depends(get_resource_repository),
) -> JSONResponse:
    correlation = getattr(request.state, "correlation_id", "")
    if resolver is None or repo is None:
        return envelope(503, "tenant_service_not_configured", "Tenant service is not configured", correlation)
    try:
        context = await _context(request, resolver)
    except TenantAuthenticationRequired:
        return envelope(401, "authentication_required", "Authentication required", correlation)
    except TenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    item = await repo.read(context, resource_id)
    if item is None:
        # Same 403 for missing-in-scope and foreign: no cross-tenant enumeration.
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    return _json_ok(correlation, {"item": item})


@router.put("/{resource_id}")
async def update_resource(
    request: Request,
    resource_id: str,
    resolver: TenantContextResolver | None = Depends(get_tenant_resolver),
    repo: PostgresTenantResourceRepository | None = Depends(get_resource_repository),
) -> JSONResponse:
    correlation = getattr(request.state, "correlation_id", "")
    if resolver is None or repo is None:
        return envelope(503, "tenant_service_not_configured", "Tenant service is not configured", correlation)
    try:
        context = await _context(request, resolver)
        body = await request.json()
        updated = await repo.update(context, resource_id, {"data": body.get("data", {}), "client_account_id": body.get("client_account_id")})
    except TenantAuthenticationRequired:
        return envelope(401, "authentication_required", "Authentication required", correlation)
    except TenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    except CrossTenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    if updated is None:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    return _json_ok(correlation, {"item": updated})


@router.delete("/{resource_id}")
async def delete_resource(
    request: Request,
    resource_id: str,
    resolver: TenantContextResolver | None = Depends(get_tenant_resolver),
    repo: PostgresTenantResourceRepository | None = Depends(get_resource_repository),
) -> JSONResponse:
    correlation = getattr(request.state, "correlation_id", "")
    if resolver is None or repo is None:
        return envelope(503, "tenant_service_not_configured", "Tenant service is not configured", correlation)
    try:
        context = await _context(request, resolver)
        removed = await repo.delete(context, resource_id)
    except TenantAuthenticationRequired:
        return envelope(401, "authentication_required", "Authentication required", correlation)
    except TenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    except CrossTenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    if not removed:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    return _json_ok(correlation, {"deleted_id": resource_id})
