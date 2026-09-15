"""Explicit application composition for the provider-neutral tenant boundary."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from uuid import uuid4

from fastapi import FastAPI, Request

from api.tenant_boundary import TenantContextResolver, router


@dataclass(frozen=True)
class TenantComposition:
    """Composition-owned dependency; not an authentication implementation."""

    resolver: TenantContextResolver | None


@contextmanager
def compose_tenant_boundary(app: FastAPI, resolver: TenantContextResolver | None) -> Iterator[FastAPI]:
    """Install a resolver through composition and restore app state afterwards.

    Production authentication is intentionally not provided. Passing ``None``
    produces a safe service-not-configured response at the HTTP boundary.
    """
    marker = object()
    previous = getattr(app.state, "tenant_composition", marker)
    app.state.tenant_composition = TenantComposition(resolver)
    try:
        yield app
    finally:
        if previous is marker:
            try:
                del app.state.tenant_composition
            except AttributeError:
                pass
        else:
            app.state.tenant_composition = previous


def create_test_application(resolver: TenantContextResolver | None) -> FastAPI:
    """Create an isolated FastAPI app with a test-only composition."""
    app = FastAPI(title="tenant-boundary-test", version="v1")

    @app.middleware("http")
    async def test_correlation(request: Request, call_next):
        correlation = request.headers.get("x-correlation-id", "").strip() or f"test-{uuid4().hex[:12]}"
        request.state.correlation_id = correlation
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation
        return response

    app.include_router(router)
    app.state.tenant_composition = TenantComposition(resolver)
    return app
