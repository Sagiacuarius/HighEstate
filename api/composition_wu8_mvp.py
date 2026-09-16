"""Work Unit 8 (MVP Commercial Demo) composition: serving backend for the
real-data Next.js dashboard.

Thin WU8-owned glue over accepted seams — nothing accepted is modified:
TenantContextResolver (tenant_boundary), DemoContextSelector +
DemoIdentityAdapter (api/identity, WU7), CrmRepositoryComposition (WU3),
CrmReadService + PostgresCrmReadRepository (WU6), ScopedQueryCache +
build_scoped_key (WU7), envelope (http_contracts).

Why a new composition instead of reusing the WU6/WU7 apps directly: the WU6
reads app pins its session account at build time (no selector), and the WU7
demo app only exposes dashboard+inbox reads. The dashboard needs every read
surface under the selector-governed session account, so this module wires the
same session-account pattern (mutate app.state only after a successful
select) with the full WU6 read service. Rollback: delete this file + demo_run.py.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
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
from api.identity.demo_identity import (
    DemoContextSelector,
    MalformedAccountIntent,
)
from api.queries.crm_reads import (
    CrmReadNotFound,
    CrmReadService,
    CrmReadValidation,
)
from api.queries.demo_cache import ScopedQueryCache, build_scoped_key
from api.routes.audit_screen_wu12 import build_audit_screen_router
from api.routes.agent_wu14 import build_agent_router
from api.routes.calendar_wu13 import build_calendar_router
from api.routes.simulator_wu9 import (
    build_simulator_router,
    clear_simulator_effects,
    count_pending_effects,
)
from api.tenant_boundary import (
    CurrentIdentity,
    TenantAccessDenied,
    TenantAuthenticationRequired,
    TenantContext,
    TenantContextResolver,
)

DEMO_PREFIX = "/v1/demo"
SELECT_ROUTE = f"{DEMO_PREFIX}/context/select"
CURRENT_ROUTE = f"{DEMO_PREFIX}/context/current"
SEED_ROUTE = f"{DEMO_PREFIX}/seed"
RESET_ROUTE = f"{DEMO_PREFIX}/reset"

_SQL_DIR = Path(__file__).resolve().parent.parent / "infra" / "migrations" / "validation"
SEED_FILE = _SQL_DIR / "mvp_seed.sql"
RESET_FILE = _SQL_DIR / "mvp_reset.sql"
SEED_008_FILE = _SQL_DIR / "mvp_seed_008.sql"
SEED_009_FILE = _SQL_DIR / "mvp_seed_009.sql"
# WU16 (agent-console, D3 fix): dashboard seed/reset re-seeds the managed
# console defaults (008 KB corpus + 009 console rows) so reset no longer drops
# them. mvp_seed_005.sql stays harness-only; harness SEED_FILES stay
# independent of this list.
MANAGED_SEED_FILES = [SEED_FILE, SEED_008_FILE, SEED_009_FILE]


class DemoCompositionWU8Mvp:
    """Named composition root: selector + full reads + scoped cache + session."""

    def __init__(self, resolver: TenantContextResolver, selector: DemoContextSelector,
                 crm: CrmRepositoryComposition, reads: CrmReadService,
                 cache: ScopedQueryCache, conn_kwargs: dict,
                 target_client_account_id: str):
        self.resolver = resolver
        self.selector = selector
        self.crm = crm
        self.reads = reads
        self.cache = cache
        self.conn_kwargs = dict(conn_kwargs)
        self.target_client_account_id = target_client_account_id


class SelectContextBody(BaseModel):
    """Strict transport shape; the requested id is a hint, never authority."""

    model_config = ConfigDict(extra="forbid")

    requestedAccountId: str


def _projection(context: TenantContext, correlation: str) -> dict:
    return {
        "contract_version": "v1",
        "environment": "dev-only-demo-selector",
        "correlation_id": correlation,
        "user_id": context.user.id,
        "agency_id": context.agency.id,
        "client_account_id": context.client_account.id,
        "client_account_name": context.client_account.name,
        "permissions": sorted(context.membership.permissions),
    }


async def _run_demo_script(conn_kwargs: dict, path: Path) -> None:
    """Same approach as the WU7 composition: run the accepted validation SQL
    script verbatim, skipping psql backslash meta-lines the driver rejects.
    The scripts carry their own fail-closed demo-marker ownership gates."""
    lines = path.read_text(encoding="utf-8").splitlines()
    body = "\n".join(line for line in lines if not line.lstrip().startswith("\\"))
    conn = await psycopg.AsyncConnection.connect(**conn_kwargs, autocommit=True)
    try:
        async with conn.cursor() as cur:
            await cur.execute(body)
    finally:
        await conn.close()


def create_mvp_dashboard_app(
    identity: CurrentIdentity,
    tenants: PostgresTenantRepository,
    memberships: PostgresMembershipRepository,
    target_client_account_id: str,
    crm: CrmRepositoryComposition,
    conn_kwargs: dict,
) -> FastAPI:
    """Isolated FastAPI app: selector + full session-scoped reads + controls."""
    app = FastAPI(title="mvp-commercial-demo-wu8", version="v1")
    # Dev-only CORS: the Next.js dashboard (different origin/port) calls these
    # demo endpoints from the browser. Scoped to this throwaway composition app
    # only; the accepted WU4/WU6/WU7 apps are untouched.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        # cross-origin fetch cannot read custom response headers unless exposed;
        # without this the client's header/body correlation check fails on errors
        expose_headers=["X-Correlation-Id", "X-Cache-Status"],
    )

    resolver = TenantContextResolver(identity, tenants, memberships)
    selector = DemoContextSelector(identity, resolver)
    reads = CrmReadService(crm, PostgresCrmReadRepository(crm.runner))
    cache = ScopedQueryCache()
    composition = DemoCompositionWU8Mvp(
        resolver=resolver, selector=selector, crm=crm, reads=reads,
        cache=cache, conn_kwargs=dict(conn_kwargs),
        target_client_account_id=target_client_account_id,
    )
    app.state.demo_composition = composition
    app.state.target_client_account_id = target_client_account_id

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        value = request.headers.get("x-correlation-id", "").strip() or f"mvp-wu8-{uuid4().hex[:12]}"
        request.state.correlation_id = value
        response: Response = await call_next(request)
        response.headers["X-Correlation-Id"] = value
        return response

    @app.post(SELECT_ROUTE)
    async def select_context(request: Request):
        correlation = request.state.correlation_id
        try:
            body = SelectContextBody.model_validate(await request.json())
        except Exception:
            return envelope(400, "invalid_selector_intent",
                            "selector intent is malformed", correlation)
        try:
            context = await composition.selector.select(request, body.requestedAccountId)
        except MalformedAccountIntent:
            return envelope(400, "invalid_selector_intent",
                            "selector intent is malformed", correlation)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required",
                            "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        app.state.target_client_account_id = context.client_account.id
        composition.target_client_account_id = context.client_account.id
        return JSONResponse(status_code=200, content=_projection(context, correlation),
                            headers={"X-Correlation-Id": correlation})

    @app.get(CURRENT_ROUTE)
    async def current_context(request: Request):
        correlation = request.state.correlation_id
        try:
            context = await resolver.resolve(request, app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required",
                            "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        return JSONResponse(status_code=200, content=_projection(context, correlation),
                            headers={"X-Correlation-Id": correlation})

    async def _scoped(request: Request):
        correlation = request.state.correlation_id
        try:
            context = await resolver.resolve(request, app.state.target_client_account_id)
            return context, None
        except TenantAuthenticationRequired:
            return None, envelope(401, "authentication_required",
                                  "authentication is required", correlation)
        except TenantAccessDenied:
            return None, envelope(403, "tenant_access_denied", "access denied", correlation)

    def _guard(coro_factory, *, cache_surface: str | None = None):
        async def runner(request: Request, **path_kwargs):
            correlation = request.state.correlation_id
            context, error = await _scoped(request)
            if error is not None:
                return error
            try:
                if cache_surface is not None:
                    key = build_scoped_key(context, cache_surface)
                    payload, hit = await composition.cache.get_or_fill(
                        context, key, lambda ctx: coro_factory(ctx, request, **path_kwargs))
                    if hit:
                        payload = {**payload, "correlation_id": correlation}
                    return JSONResponse(
                        status_code=200, content=payload,
                        headers={"X-Correlation-Id": correlation,
                                 "X-Cache-Status": "hit" if hit else "miss"})
                payload = await coro_factory(context, request, **path_kwargs)
            except CrmReadValidation as problem:
                return envelope(400, "invalid_query", str(problem), correlation)
            except CrmReadNotFound:
                return envelope(404, "resource_not_found",
                                "resource is not available in this account scope", correlation)
            except CrmNotFoundError:
                return envelope(404, "resource_not_found",
                                "resource is not available in this account scope", correlation)
            except TenantAccessDenied:
                return envelope(403, "tenant_access_denied", "access denied", correlation)
            return JSONResponse(status_code=200, content=payload,
                                headers={"X-Correlation-Id": correlation})
        return runner

    @app.get(f"{DEMO_PREFIX}/dashboard")
    async def dashboard(request: Request):
        return await _guard(lambda ctx, req: reads.dashboard(ctx),
                            cache_surface="dashboard")(request)

    @app.get(f"{DEMO_PREFIX}/inbox")
    async def inbox(request: Request):
        return await _guard(lambda ctx, req: reads.inbox(ctx),
                            cache_surface="inbox")(request)

    @app.get(f"{DEMO_PREFIX}/inbox/{{conversation_id}}/messages")
    async def inbox_messages(request: Request, conversation_id: str):
        return await _guard(
            lambda ctx, req, **kw: reads.conversation_messages(ctx, kw["conversation_id"]))(
            request, conversation_id=conversation_id)

    @app.get(f"{DEMO_PREFIX}/leads")
    async def leads(request: Request):
        return await _guard(lambda ctx, req: reads.leads(ctx))(request)

    @app.get(f"{DEMO_PREFIX}/properties")
    async def properties(request: Request):
        return await _guard(lambda ctx, req: reads.properties(ctx))(request)

    @app.get(f"{DEMO_PREFIX}/pipeline")
    async def pipeline(request: Request):
        return await _guard(lambda ctx, req: reads.pipeline(ctx))(request)

    @app.get(f"{DEMO_PREFIX}/agenda")
    async def agenda(request: Request):
        return await _guard(lambda ctx, req: reads.agenda(ctx))(request)

    @app.get(f"{DEMO_PREFIX}/activity")
    async def activity(request: Request):
        return await _guard(
            lambda ctx, req: reads.activity(ctx, req.query_params.get("limit")))(request)

    async def _guarded_script(request: Request, paths: list[Path], ok_key: str,
                              *, effect_aware: bool = False):
        correlation = request.state.correlation_id
        try:
            context = await resolver.resolve(request, app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required",
                            "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        cleared: int | None = None
        if effect_aware:
            # WU9: the accepted reset deletes only demo-marked rows, so pending
            # simulator effects (unmarked children) fail its RESTRICT FKs
            # closed — the documented WU7 finding. Default keeps the accepted
            # fail-closed envelope but now REPORTS the blocking row count;
            # ?clear_effects=true runs the tested simulator-effect clear
            # (unmarked effect rows only) before reset+seed.
            crm_runner = composition.crm.runner  # accepted WU3 runner (pool-safe: one conn per op)
            if request.query_params.get("clear_effects", "").strip().lower() in ("1", "true"):
                cleared = await clear_simulator_effects(crm_runner)
            else:
                pending = await count_pending_effects(
                    crm_runner,
                    ("20000000-0000-0000-0000-000000000001",
                     "20000000-0000-0000-0000-000000000002"))
                if pending > 0:
                    return JSONResponse(
                        status_code=409,
                        content={"error": {
                            "code": "demo_effects_pending",
                            "message": ("reset blocked by pending simulator "
                                        "effects; retry with clear_effects=true"),
                            "correlation_id": correlation},
                            "pending_effects": pending},
                        headers={"X-Correlation-Id": correlation})
        try:
            for path in paths:
                await _run_demo_script(composition.conn_kwargs, path)
        except Exception:
            return envelope(500, "demo_script_failed",
                            "demo seed/reset script failed", correlation)
        composition.cache.clear()
        payload = {"contract_version": "v1",
                   "environment": "dev-only-demo-controls",
                   ok_key: True,
                   "account_id": context.client_account.id,
                   "correlation_id": correlation}
        if cleared is not None:
            payload["cleared_effects"] = cleared
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    @app.post(SEED_ROUTE)
    async def seed(request: Request):
        return await _guarded_script(request, MANAGED_SEED_FILES, "seeded")

    @app.post(RESET_ROUTE)
    async def reset(request: Request):
        return await _guarded_script(request, [RESET_FILE] + MANAGED_SEED_FILES, "reset",
                                     effect_aware=True)

    # WU9: dev-only simulator (mounted router shares this app's session state;
    # its route appears in the OpenAPI docs under /v1/demo/simulate).
    app.include_router(build_simulator_router(composition.conn_kwargs),
                       prefix=DEMO_PREFIX, tags=["dev-only-simulator"])

    # WU12: read-only audit screen over the accepted trail (same mount
    # precedent; the accepted /v1/demo/activity route above is untouched).
    app.include_router(build_audit_screen_router(composition.conn_kwargs),
                       prefix=DEMO_PREFIX, tags=["audit-screen"])

    # WU13: calendar / agenda (events + availability; same mount precedent).
    app.include_router(build_calendar_router(composition.conn_kwargs),
                       prefix=DEMO_PREFIX, tags=["calendar"])

    # WU14: conversational agent (analyze -> propose draft -> approve/reject).
    # The provider is selected from the environment (key -> opencode-go, else
    # deterministic stub); no accepted route is modified.
    app.include_router(build_agent_router(composition.conn_kwargs),
                       prefix=DEMO_PREFIX, tags=["conversational-agent"])

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return envelope(404, "route_not_found", "route is not available",
                        request.state.correlation_id)

    @app.exception_handler(405)
    async def method_not_allowed(request: Request, exc):
        return envelope(405, "method_not_allowed", "method is not allowed",
                        request.state.correlation_id)

    return app


def build_mvp_wu8_application(conn_kwargs: dict, target_client_account_id: str,
                              identity: CurrentIdentity) -> FastAPI:
    """Composition root: fail-fast DB config + WU3 repos + WU8 dashboard glue."""
    pool = ConnectionPool(dict(conn_kwargs))
    runner = PostgresRunner(pool)
    tenants = PostgresTenantRepository(runner)
    memberships = PostgresMembershipRepository(runner)
    crm = CrmRepositoryComposition(runner)
    return create_mvp_dashboard_app(identity, tenants, memberships,
                                    target_client_account_id, crm, conn_kwargs)
