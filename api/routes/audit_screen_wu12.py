"""WU12 (audit-screen) router: GET /v1/demo/audit over the accepted seams.

Read-only exposure of the durable audit trail. Mounted onto the WU8 app like
the WU9 simulator router (shares app.state.demo_composition: resolver,
session account), so selector semantics are identical and the accepted
/v1/demo/activity route stays untouched.

Discipline (same as accepted reads):
- identity/scope come from the server resolver only; no query parameter is
  ever read as an account selector, and a forged clientAccountId is simply
  not part of the parameter whitelist (ignored provably);
- validation failures -> 400 invalid_query envelope with the request
  correlation in header and body;
- reads NEVER write audit records: exposing the trail must not pollute it;
- the response carries no metadata key at all (projection lives in the
  service); foreign/ghost filter ids -> empty 200 (filters do not enumerate).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_audit_screen import PostgresAuditScreenRepository
from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.http_contracts import envelope
from api.queries.audit_screen_wu12 import AuditScreenService
from api.queries.crm_reads import CrmReadValidation
from api.tenant_boundary import TenantAccessDenied, TenantAuthenticationRequired

AUDIT_ROUTE = "/audit"
AUDIT_PATH = f"/v1/demo{AUDIT_ROUTE}"


def build_audit_screen_router(conn_kwargs: dict) -> APIRouter:
    """One router instance per app; its runner shares the app's connection
    settings (a dedicated pool like the simulator's command owns)."""
    router = APIRouter()
    runner = PostgresRunner(ConnectionPool(dict(conn_kwargs)))
    service = AuditScreenService(runner, audit=PostgresAuditScreenRepository(runner))

    @router.get(AUDIT_ROUTE)
    async def audit(request: Request):
        composition = request.app.state.demo_composition
        correlation = request.state.correlation_id
        try:
            context = await composition.resolver.resolve(
                request, composition.target_client_account_id)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required",
                            "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        try:
            payload = await service.list_audit(context, dict(request.query_params))
        except CrmReadValidation as problem:
            return envelope(400, "invalid_query", str(problem), correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    return router
