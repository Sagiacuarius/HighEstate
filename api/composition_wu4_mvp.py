"""Work Unit 4 (MVP Commercial Demo) composition: real FastAPI wiring for the
local demo over the PostgreSQL CRM repositories and the server-owned tenant
context resolver.

Does not touch or replace the technical WU4/WU5/WU6 compositions nor the MVP
WU3 repository composition. Identity comes from a server-owned CurrentIdentity
port; the dev identity adapter stands in for a future auth layer. The target
account is session/server state, never read from request bodies — the inbound
payload's clientAccountId is a claim re-validated against the resolved context.

Only dev-safe wiring here: no product auth, no CRM commands (WU5), no read
APIs (WU6).
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.adapters.postgres_tenancy import (
    PostgresMembershipRepository,
    PostgresTenantRepository,
)
from api.composition_wu3_mvp import CrmRepositoryComposition
from api.http_contracts import envelope
from api.inbound_boundary import (
    DevJournalInboundHandler,
    InboundPayloadInvalid,
    InboundScopeDenied,
    NormalizedInboundEvent,
    parse_inbound_message_event,
)
from api.tenant_boundary import (
    CurrentIdentity,
    TenantAccessDenied,
    TenantAuthenticationRequired,
    TenantContextResolver,
)

INBOUND_ROUTE = "/dev/inbound-message"


class DemoCompositionWU4Mvp:
    """Named composition root for the MVP local demo slice."""

    def __init__(self, resolver: TenantContextResolver, crm: CrmRepositoryComposition,
                 inbound_handler: DevJournalInboundHandler, target_client_account_id: str):
        self.resolver = resolver
        self.crm = crm
        self.inbound_handler = inbound_handler
        self.target_client_account_id = target_client_account_id


class InboundMessageBody(BaseModel):
    """Strict transport shape; extra keys rejected. The clientAccountId here is
    a claim — never authority — re-checked against the server-resolved scope."""

    model_config = ConfigDict(extra="forbid")

    eventId: str
    clientAccountId: str
    conversationId: str
    message: dict


def create_mvp_inbound_app(
    identity: CurrentIdentity,
    tenants: PostgresTenantRepository,
    memberships: PostgresMembershipRepository,
    target_client_account_id: str,
    crm: CrmRepositoryComposition,
) -> FastAPI:
    """Isolated FastAPI app for the MVP local demo inbound boundary."""
    app = FastAPI(title="mvp-commercial-demo-wu4", version="v1")

    resolver = TenantContextResolver(identity, tenants, memberships)
    composition = DemoCompositionWU4Mvp(
        resolver=resolver,
        crm=crm,
        inbound_handler=DevJournalInboundHandler(crm.conversations),
        target_client_account_id=target_client_account_id,
    )
    app.state.demo_composition = composition
    app.state.target_client_account_id = target_client_account_id

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        value = request.headers.get("x-correlation-id", "").strip() or f"mvp-wu4-{uuid4().hex[:12]}"
        request.state.correlation_id = value
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = value
        return response

    @app.post(INBOUND_ROUTE)
    async def dev_inbound(request: Request):
        # DEV-ONLY endpoint for the local simulator. Not a production surface:
        # 401 semantics come from the dev identity adapter standing in for a
        # future auth layer; CRM commands/idempotency/audit arrive in WU5.
        correlation = request.state.correlation_id
        try:
            body = InboundMessageBody.model_validate(await request.json())
        except Exception:
            return envelope(400, "invalid_payload", "inbound message payload is malformed", correlation)
        try:
            event = parse_inbound_message_event(body.model_dump())
        except InboundPayloadInvalid as error:
            return envelope(400, "invalid_payload", str(error), correlation)
        # Server-owned scope resolution: body ids never authorize anything.
        try:
            context = await resolver.resolve(request, app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required", "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        if event.client_account_id != context.client_account.id:
            # A claim that does not match the server session scope is a scope
            # attack, not a payload problem; one non-enumerating 403 shape.
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        try:
            await composition.inbound_handler.handle(context, event)
        except InboundScopeDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        return _accepted(event, correlation)

    return app


def _accepted(event: NormalizedInboundEvent, correlation: str) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        headers={"X-Correlation-Id": correlation},
        content={
            "accepted": True,
            "environment": "dev-only-inbound-boundary",
            "event": event.to_dict(),
            "correlation_id": correlation,
        },
    )


def build_mvp_wu4_application(conn_kwargs: dict, target_client_account_id: str, identity: CurrentIdentity) -> FastAPI:
    """Composition root: fail-fast DB config + real CRM repositories + dev identity."""
    pool = ConnectionPool(dict(conn_kwargs))
    runner = PostgresRunner(pool)
    tenants = PostgresTenantRepository(runner)
    memberships = PostgresMembershipRepository(runner)
    crm = CrmRepositoryComposition(runner)
    return create_mvp_inbound_app(identity, tenants, memberships, target_client_account_id, crm)
