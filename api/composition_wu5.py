"""Explicit WU5 composition for PostgreSQL command tests."""
from __future__ import annotations

from uuid import uuid4
from fastapi import FastAPI, Request

from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.adapters.postgres_idempotency import PostgresCommandConnection, TransactionalUpdateTenantResource
from api.adapters.postgres_tenancy import PostgresMembershipRepository, PostgresTenantRepository
from api.command_http import router as command_router
from api.tenant_boundary import CurrentIdentity, TenantContextResolver


def create_wu5_application(identity: CurrentIdentity, conn_kwargs: dict, target_client_account_id: str) -> tuple[FastAPI, PostgresRunner]:
    app = FastAPI(title="wu5-command-test", version="v1")

    @app.middleware("http")
    async def request_metadata(request: Request, call_next):
        correlation = request.headers.get("x-correlation-id", "").strip() or f"wu5-{uuid4().hex[:12]}"
        request.state.correlation_id = correlation
        raw_key = request.headers.get("x-idempotency-key")
        request.state.idempotency_key = raw_key.strip() if raw_key and raw_key.strip() else None
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation
        return response

    runner = PostgresRunner(ConnectionPool(conn_kwargs))
    tenants = PostgresTenantRepository(runner)
    memberships = PostgresMembershipRepository(runner)
    resolver = TenantContextResolver(identity, tenants, memberships)
    command = TransactionalUpdateTenantResource(PostgresCommandConnection(conn_kwargs))
    app.include_router(command_router)
    app.state.tenant_composition = type("WU5Composition", (), {"resolver": resolver, "command": command})()
    app.state.target_client_account_id = target_client_account_id
    return app, runner
