"""Work Unit 4 composition: PostgreSQL-backed tenant wiring for tests/internals.

Keeps the accepted WU2 composition untouched and extends it with the real
repository set. The target_client_account_id represents the server-side
session/current-account state a future auth layer would supply; in these tests
composition sets it explicitly per identity scenario. It is never read from
client input by the resource routes.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request

from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.adapters.postgres_resources import PostgresTenantResourceRepository
from api.adapters.postgres_tenancy import (
    PostgresMembershipRepository,
    PostgresTenantRepository,
    PostgresUserRepository,
)
from api.tenant_boundary import (
    CurrentIdentity,
    TenantContextResolver,
    router as tenant_router,
)
from api.tenant_resources import router as resource_router


class TenantCompositionWU4:
    def __init__(self, resolver: TenantContextResolver, resources: PostgresTenantResourceRepository):
        self.resolver = resolver
        self.resource_repository = resources


def build_postgres_stack(conn_kwargs: dict) -> tuple[PostgresRunner, PostgresTenantRepository, PostgresMembershipRepository, PostgresUserRepository, PostgresTenantResourceRepository]:
    pool = ConnectionPool(conn_kwargs)
    runner = PostgresRunner(pool)
    tenants = PostgresTenantRepository(runner)
    memberships = PostgresMembershipRepository(runner)
    users = PostgresUserRepository(runner)
    resources = PostgresTenantResourceRepository(runner)
    return runner, tenants, memberships, users, resources


def create_wu4_application(identity: CurrentIdentity, tenants: PostgresTenantRepository, memberships: PostgresMembershipRepository, resources: PostgresTenantResourceRepository, target_client_account_id: str) -> FastAPI:
    """Isolated FastAPI app wired to the real PostgreSQL adapters."""
    app = FastAPI(title="wu4-postgres-tenant-test", version="v1")

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        value = request.headers.get("x-correlation-id", "").strip() or f"wu4-{uuid4().hex[:12]}"
        request.state.correlation_id = value
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = value
        return response

    resolver = TenantContextResolver(identity, tenants, memberships)
    app.include_router(tenant_router)
    app.include_router(resource_router)
    app.state.tenant_composition = TenantCompositionWU4(resolver, resources)
    app.state.target_client_account_id = target_client_account_id
    return app
