"""Minimal HTTP wiring for the transactional tenant-resource command."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_idempotency import (
    CommandResourceNotFound,
    CrossTenantAccessDenied,
    IdempotencyConflict,
    TransactionalUpdateTenantResource,
)
from api.http_contracts import envelope
from api.tenant_boundary import (
    TenantAccessDenied,
    TenantAuthenticationRequired,
    TenantContextResolver,
    get_tenant_resolver,
)

router = APIRouter(prefix="/commands/tenant-resources", tags=["commands"])


def get_command(request: Request) -> TransactionalUpdateTenantResource | None:
    composition = getattr(request.app.state, "tenant_composition", None)
    return getattr(composition, "command", None)


def _hash_body(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@router.put("/{resource_id}")
async def update_resource_command(
    request: Request,
    resource_id: str,
    resolver: TenantContextResolver | None = Depends(get_tenant_resolver),
    command: TransactionalUpdateTenantResource | None = Depends(get_command),
) -> JSONResponse:
    correlation = getattr(request.state, "correlation_id", "")
    key = getattr(request.state, "idempotency_key", None)
    if not key:
        return envelope(400, "invalid_idempotency_key", "X-Idempotency-Key is required", correlation)
    if resolver is None or command is None:
        return envelope(503, "command_service_not_configured", "Command service is not configured", correlation)
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("data", {}), dict):
            return envelope(400, "invalid_command", "Command payload is invalid", correlation)
        context = await resolver.resolve(
            request,
            getattr(request.app.state, "target_client_account_id", ""),
        )
        result = await command.execute(
            context,
            resource_id=resource_id,
            request_hash=_hash_body(body),
            data=body["data"],
            idempotency_key=key,
        )
    except TenantAuthenticationRequired:
        return envelope(401, "authentication_required", "Authentication required", correlation)
    except TenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    except CrossTenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    except IdempotencyConflict:
        return envelope(409, "tenant_idempotency_conflict", "Command idempotency conflict", correlation)
    except CommandResourceNotFound:
        return envelope(404, "not_found", "Resource not found", correlation)
    return JSONResponse(
        status_code=200,
        headers={"X-Correlation-Id": correlation},
        content={
            "contract_version": "v1",
            "correlation_id": correlation,
            "replayed": result.replayed,
            "rows_affected": result.rows_affected,
            "result": result.payload,
        },
    )
