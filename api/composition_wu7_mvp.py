"""Work Unit 7 (MVP Commercial Demo) composition: server-owned demo selector,
context composition, scoped cache keys, and demo seed/reset controls.

Reuses the accepted seams without touching them: TenantContextResolver +
InMemory-style server identity pattern from api/tenant_boundary.py, the WU3
CrmRepositoryComposition, and the WU6 CrmReadService. The requested account id
is an intent/hint only — membership resolves server-side; session state
(app.state.target_client_account_id) mutates ONLY after a successful select,
so a denied select preserves the prior context. Errors share the accepted v1
envelope with a consistent X-Correlation-Id header.

Only selector/cache/seed-reset wiring here: no commands (WU5 owns them), no
new read shapes (WU6 owns them), no UI, no Kapso/LLM/auth.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response

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
from api.queries.crm_reads import CrmReadService
from api.queries.demo_cache import ScopedQueryCache, build_scoped_key
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
DASHBOARD_ROUTE = f"{DEMO_PREFIX}/dashboard"
INBOX_ROUTE = f"{DEMO_PREFIX}/inbox"

_SQL_DIR = Path(__file__).resolve().parent.parent / "infra" / "migrations" / "validation"
SEED_FILE = _SQL_DIR / "mvp_seed.sql"
RESET_FILE = _SQL_DIR / "mvp_reset.sql"


class DemoCompositionWU7Mvp:
    """Named composition root: selector + reads + scoped cache + session account."""

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
    """Execute a validation SQL script, skipping psql meta-commands.

    The mvp_seed/mvp_reset scripts are single BEGIN...COMMIT transactions with
    their own fail-closed ownership gates; only the advisory-lock + SQL body
    runs here. psycopg cannot interpret backslash meta-commands, so those
    lines are dropped (the server still aborts the whole script on the first
    SQL error, matching ON_ERROR_STOP semantics).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    body = "\n".join(line for line in lines if not line.lstrip().startswith("\\"))
    conn = await psycopg.AsyncConnection.connect(**conn_kwargs, autocommit=True)
    try:
        async with conn.cursor() as cur:
            await cur.execute(body)
    finally:
        await conn.close()


def create_mvp_demo_app(
    identity: CurrentIdentity,
    tenants: PostgresTenantRepository,
    memberships: PostgresMembershipRepository,
    target_client_account_id: str,
    crm: CrmRepositoryComposition,
    conn_kwargs: dict,
) -> FastAPI:
    """Isolated FastAPI app: demo selector + cached reads + seed/reset."""
    app = FastAPI(title="mvp-commercial-demo-wu7", version="v1")

    resolver = TenantContextResolver(identity, tenants, memberships)
    selector = DemoContextSelector(identity, resolver)
    reads = CrmReadService(crm, PostgresCrmReadRepository(crm.runner))
    cache = ScopedQueryCache()
    composition = DemoCompositionWU7Mvp(
        resolver=resolver, selector=selector, crm=crm, reads=reads,
        cache=cache, conn_kwargs=dict(conn_kwargs),
        target_client_account_id=target_client_account_id,
    )
    app.state.demo_composition = composition
    app.state.target_client_account_id = target_client_account_id

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        value = request.headers.get("x-correlation-id", "").strip() or f"mvp-wu7-{uuid4().hex[:12]}"
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
            # Missing/foreign/no-membership share one non-enumerating denial;
            # session state is untouched, so the prior context stays active.
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        # Success path only: adopt the resolved scope as the session account.
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

    async def _cached_read(request: Request, surface: str, loader):
        correlation = request.state.correlation_id
        try:
            context = await resolver.resolve(request, app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required",
                            "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        try:
            key = build_scoped_key(context, surface)
            payload, hit = await composition.cache.get_or_fill(context, key, loader)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        if hit:
            # Cached data is a snapshot; the correlation id always belongs to
            # THIS request (header/body consistency is contractual).
            payload = {**payload, "correlation_id": correlation}
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation,
                                     "X-Cache-Status": "hit" if hit else "miss"})

    @app.get(DASHBOARD_ROUTE)
    async def dashboard(request: Request):
        return await _cached_read(request, "dashboard", lambda ctx: reads.dashboard(ctx))

    @app.get(INBOX_ROUTE)
    async def inbox(request: Request):
        return await _cached_read(request, "inbox", lambda ctx: reads.inbox(ctx))

    async def _guarded_script(request: Request, paths: list[Path], ok_key: str):
        """Seed/reset controls: authed session context required; demo markers only.

        The SQL scripts delete/recreate ONLY rows carrying the exact demo-*
        markers inside the two designated accounts (ownership gates abort on
        foreign/unmarked rows). Unmarked scratch rows always survive.
        """
        correlation = request.state.correlation_id
        try:
            context = await resolver.resolve(request, app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required",
                            "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        try:
            for path in paths:
                await _run_demo_script(composition.conn_kwargs, path)
        except Exception:
            return envelope(500, "demo_script_failed",
                            "demo seed/reset script failed", correlation)
        composition.cache.clear()
        return JSONResponse(status_code=200,
                            content={"contract_version": "v1",
                                     "environment": "dev-only-demo-controls",
                                     ok_key: True,
                                     "account_id": context.client_account.id,
                                     "correlation_id": correlation},
                            headers={"X-Correlation-Id": correlation})

    @app.post(SEED_ROUTE)
    async def seed(request: Request):
        # Convergent re-apply: same deterministic state, no duplicates.
        return await _guarded_script(request, [SEED_FILE], "seeded")

    @app.post(RESET_ROUTE)
    async def reset(request: Request):
        # Reset restores the deterministic seed state (reset + re-seed).
        return await _guarded_script(request, [RESET_FILE, SEED_FILE], "reset")

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return envelope(404, "route_not_found", "route is not available",
                        request.state.correlation_id)

    @app.exception_handler(405)
    async def method_not_allowed(request: Request, exc):
        return envelope(405, "method_not_allowed", "method is not allowed",
                        request.state.correlation_id)

    return app


def build_mvp_wu7_application(conn_kwargs: dict, target_client_account_id: str,
                              identity: CurrentIdentity) -> FastAPI:
    """Composition root: fail-fast DB config + WU3 repos + WU7 selector/cache."""
    pool = ConnectionPool(dict(conn_kwargs))
    runner = PostgresRunner(pool)
    tenants = PostgresTenantRepository(runner)
    memberships = PostgresMembershipRepository(runner)
    crm = CrmRepositoryComposition(runner)
    return create_mvp_demo_app(identity, tenants, memberships,
                               target_client_account_id, crm, conn_kwargs)
