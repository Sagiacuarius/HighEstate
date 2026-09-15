"""Work Unit 6 (MVP Commercial Demo) composition: versioned, tenant-scoped
READ APIs over the accepted WU3 repositories, the WU4 server-owned context
resolution pattern, and the WU6 SQL read adapter.

Routes are under a fixed versioned prefix /v1/crm/* and are registered
statically (never derived from user input). Identity remains server-owned
(same TenantContextResolver + session target account as WU4/WU5 MVP); no read
route accepts a client account id, and no body/query field can override the
context-derived predicate. Errors share the accepted v1 envelope with a
consistent X-Correlation-Id header; unknown routes and wrong methods produce
the same 404/405 envelope shape, not FastAPI defaults.

Only read wiring here: no commands (WU5 owns them), no UI, no Kapso/LLM/auth.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from api.adapters.postgres_commercial_crm import CrmNotFoundError
from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.adapters.postgres_crm_reads import PostgresCrmReadRepository
from api.adapters.postgres_tenancy import (
    PostgresMembershipRepository,
    PostgresTenantRepository,
)
from api.composition_wu3_mvp import CrmRepositoryComposition
from api.http_contracts import envelope
from api.queries.crm_reads import (
    CrmReadNotFound,
    CrmReadService,
    CrmReadValidation,
)
from api.tenant_boundary import (
    CurrentIdentity,
    TenantAccessDenied,
    TenantAuthenticationRequired,
    TenantContext,
    TenantContextResolver,
)

API_PREFIX = "/v1/crm"


class DemoCompositionWU6Mvp:
    """Named composition root: resolver + read service + session account."""

    def __init__(self, resolver: TenantContextResolver, crm: CrmRepositoryComposition,
                 reads: CrmReadService, target_client_account_id: str):
        self.resolver = resolver
        self.crm = crm
        self.reads = reads
        self.target_client_account_id = target_client_account_id


def _json_ok(content: dict, correlation: str) -> JSONResponse:
    return JSONResponse(status_code=200, content=content,
                        headers={"X-Correlation-Id": correlation})


def create_mvp_reads_app(
    identity: CurrentIdentity,
    tenants: PostgresTenantRepository,
    memberships: PostgresMembershipRepository,
    target_client_account_id: str,
    crm: CrmRepositoryComposition,
) -> FastAPI:
    """Isolated FastAPI app: versioned CRM read APIs over server-owned context."""
    app = FastAPI(title="mvp-commercial-demo-wu6", version="v1")

    resolver = TenantContextResolver(identity, tenants, memberships)
    read_repo = PostgresCrmReadRepository(crm.runner)
    service = CrmReadService(crm, read_repo)
    composition = DemoCompositionWU6Mvp(
        resolver=resolver, crm=crm, reads=service,
        target_client_account_id=target_client_account_id,
    )
    app.state.demo_composition = composition
    app.state.target_client_account_id = target_client_account_id

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        value = request.headers.get("x-correlation-id", "").strip() or f"mvp-wu6-{uuid4().hex[:12]}"
        request.state.correlation_id = value
        response: Response = await call_next(request)
        response.headers["X-Correlation-Id"] = value
        return response

    async def scoped(request: Request):
        """Resolve context or return an error envelope (401/403 non-enumerating)."""
        correlation = request.state.correlation_id
        try:
            context: TenantContext = await resolver.resolve(
                request, app.state.target_client_account_id)
            return context, None
        except TenantAuthenticationRequired:
            return None, envelope(401, "authentication_required",
                                  "authentication is required", correlation)
        except TenantAccessDenied:
            return None, envelope(403, "tenant_access_denied", "access denied", correlation)

    def guard(coro_factory):
        async def runner(request: Request, **path_kwargs):
            correlation = request.state.correlation_id
            context, error = await scoped(request)
            if error is not None:
                return error
            try:
                payload = await coro_factory(context, request, **path_kwargs)
            except CrmReadValidation as problem:
                return envelope(400, "invalid_query", str(problem), correlation)
            except CrmReadNotFound:
                # missing-in-scope and foreign-in-scope are one 404 shape
                return envelope(404, "resource_not_found",
                                "resource is not available in this account scope", correlation)
            except CrmNotFoundError:
                return envelope(404, "resource_not_found",
                                "resource is not available in this account scope", correlation)
            return _json_ok(payload, correlation)
        return runner

    @app.get(f"{API_PREFIX}/dashboard")
    async def dashboard(request: Request):
        return await guard(lambda ctx, req: service.dashboard(ctx))(request)

    @app.get(f"{API_PREFIX}/inbox")
    async def inbox(request: Request):
        return await guard(lambda ctx, req: service.inbox(ctx))(request)

    @app.get(f"{API_PREFIX}/inbox/{{conversation_id}}/messages")
    async def inbox_messages(request: Request, conversation_id: str):
        return await guard(
            lambda ctx, req, **kw: service.conversation_messages(ctx, kw["conversation_id"]))(
            request, conversation_id=conversation_id)

    @app.get(f"{API_PREFIX}/leads")
    async def leads(request: Request):
        return await guard(lambda ctx, req: service.leads(ctx))(request)

    @app.get(f"{API_PREFIX}/properties")
    async def properties(request: Request):
        return await guard(lambda ctx, req: service.properties(ctx))(request)

    @app.get(f"{API_PREFIX}/properties/{{property_id}}")
    async def property_detail(request: Request, property_id: str):
        return await guard(
            lambda ctx, req, **kw: service.property_detail(ctx, kw["property_id"]))(
            request, property_id=property_id)

    @app.get(f"{API_PREFIX}/pipeline")
    async def pipeline(request: Request):
        return await guard(lambda ctx, req: service.pipeline(ctx))(request)

    @app.get(f"{API_PREFIX}/agenda")
    async def agenda(request: Request):
        return await guard(lambda ctx, req: service.agenda(ctx))(request)

    @app.get(f"{API_PREFIX}/activity")
    async def activity(request: Request):
        return await guard(
            lambda ctx, req: service.activity(ctx, req.query_params.get("limit")))(request)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return envelope(404, "route_not_found", "route is not available",
                        request.state.correlation_id)

    @app.exception_handler(405)
    async def method_not_allowed(request: Request, exc):
        return envelope(405, "method_not_allowed", "method is not allowed",
                        request.state.correlation_id)

    return app


def build_mvp_wu6_application(conn_kwargs: dict, target_client_account_id: str,
                              identity: CurrentIdentity) -> FastAPI:
    """Composition root: fail-fast DB config + WU3 repos + WU6 read service."""
    pool = ConnectionPool(dict(conn_kwargs))
    runner = PostgresRunner(pool)
    tenants = PostgresTenantRepository(runner)
    memberships = PostgresMembershipRepository(runner)
    crm = CrmRepositoryComposition(runner)
    return create_mvp_reads_app(identity, tenants, memberships, target_client_account_id, crm)
