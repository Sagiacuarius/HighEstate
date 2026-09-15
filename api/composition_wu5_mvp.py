"""Work Unit 5 (MVP Commercial Demo) composition: FastAPI wiring for the
ATOMIC inbound CRM command over the accepted WU3 repositories/WU4 boundary.

Does not touch or replace the accepted WU4 MVP composition: it reuses the same
server-owned TenantContextResolver, the same strict parser and the same
dev-only route shape, and adds the durable command execution:
reservation -> message/lead/transition/follow-up -> audit + outbox -> commit,
with X-Idempotency-Key replay/conflict semantics and error envelopes.

Identity remains server-owned; the payload clientAccountId stays a claim.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.adapters.postgres_tenancy import (
    PostgresMembershipRepository,
    PostgresTenantRepository,
)
from api.application.crm_commands_wu5 import (
    CrmCommandDenied,
    CrmCommandValidationError,
    ProcessInboundMessageCommand,
)
from api.composition_wu3_mvp import CrmRepositoryComposition
from api.http_contracts import envelope
from api.inbound_boundary import (
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

INBOUND_COMMAND_ROUTE = "/dev/inbound-message"


class DemoCompositionWU5Mvp:
    """Named composition root: WU4 shape + the atomic CRM inbound command."""

    def __init__(self, resolver: TenantContextResolver, crm: CrmRepositoryComposition,
                 inbound_command: ProcessInboundMessageCommand, target_client_account_id: str):
        self.resolver = resolver
        self.crm = crm
        self.inbound_command = inbound_command
        self.target_client_account_id = target_client_account_id


def create_mvp_command_app(
    identity: CurrentIdentity,
    tenants: PostgresTenantRepository,
    memberships: PostgresMembershipRepository,
    target_client_account_id: str,
    crm: CrmRepositoryComposition,
    conn_kwargs: dict,
) -> FastAPI:
    """Isolated FastAPI app: dev-only inbound boundary backed by WU5 commands."""
    app = FastAPI(title="mvp-commercial-demo-wu5", version="v1")

    resolver = TenantContextResolver(identity, tenants, memberships)
    command = ProcessInboundMessageCommand(dict(conn_kwargs))
    composition = DemoCompositionWU5Mvp(
        resolver=resolver, crm=crm, inbound_command=command,
        target_client_account_id=target_client_account_id,
    )
    app.state.demo_composition = composition
    app.state.target_client_account_id = target_client_account_id

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        value = request.headers.get("x-correlation-id", "").strip() or f"mvp-wu5-{uuid4().hex[:12]}"
        request.state.correlation_id = value
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = value
        return response

    @app.post(INBOUND_COMMAND_ROUTE)
    async def dev_inbound(request: Request):
        # DEV-ONLY endpoint for the local simulator. Not a production surface:
        # 401 semantics come from the dev identity adapter standing in for a
        # future auth layer. The command itself is the durable atomic slice.
        correlation = request.state.correlation_id
        try:
            payload = await request.json()
        except Exception:
            return envelope(400, "invalid_payload", "inbound message payload is malformed", correlation)
        try:
            event: NormalizedInboundEvent = parse_inbound_message_event(payload)
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
            return envelope(403, "tenant_access_denied", "access denied", correlation)

        # Idempotency key: explicit header wins; otherwise the deterministic
        # event id acts as the dev-simulator key (double-click == replay).
        key = request.headers.get("x-idempotency-key", "").strip() or f"dev-event:{event.event_id}"
        try:
            outcome = await composition.inbound_command.execute_event(
                context, event=event, idempotency_key=key,
            )
        except InboundScopeDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        except CrmCommandDenied:
            # foreign/missing conversation, integrity denial: one 403 shape
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        except CrmCommandValidationError as error:
            return envelope(400, "invalid_command", str(error), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict", "command idempotency conflict", correlation)
        return JSONResponse(
            status_code=200,
            headers={"X-Correlation-Id": correlation},
            content={
                "accepted": True,
                "replayed": outcome.replayed,
                "environment": "dev-only-inbound-command",
                "result": outcome.payload,
                "correlation_id": correlation,
            },
        )

    return app


def build_mvp_wu5_application(conn_kwargs: dict, target_client_account_id: str,
                              identity: CurrentIdentity) -> FastAPI:
    """Composition root: fail-fast DB config + WU3 repos + atomic inbound command."""
    pool = ConnectionPool(dict(conn_kwargs))
    runner = PostgresRunner(pool)
    tenants = PostgresTenantRepository(runner)
    memberships = PostgresMembershipRepository(runner)
    crm = CrmRepositoryComposition(runner)
    return create_mvp_command_app(identity, tenants, memberships, target_client_account_id, crm, conn_kwargs)
