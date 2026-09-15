"""Work Unit 9 (MVP Commercial Demo): dev-only simulator endpoint + blocked-reset resolution.

Thin WU9-owned glue over accepted seams — nothing accepted is modified:
ProcessInboundMessageCommand (WU5), parse_inbound_message_event (WU4),
TenantContextResolver (accepted tenant boundary), the WU8 dashboard
composition's app.state (resolver, scoped cache, seed/reset helpers).

Mounted as an APIRouter onto the WU8 app (composition_wu8_mvp) so selector,
reads and the demo account session are shared with the dashboard; the button
in the Inbox view calls this surface.

Blocked reset (WU7 documented finding): the accepted mvp_reset.sql deletes
ONLY demo-marked rows, so simulator-created unmarked children of seed rows
fail its RESTRICT FKs. The POST /v1/demo/reset route keeps the accepted
fail-closed 500 `demo_script_failed` envelope, and additionally reports the
blocking effect rows. A new ?clear_effects=true (UI "Force reset") runs the
tested clear_simulator_effects deletion (same rules as the WU7 harness
clear_unmarked helper — unmarked CRM effect rows plus the demo
idempotency/audit/outbox evidence) BEFORE the reset+seed transaction.
Unmarked rows never referenced by anything are still preserved for other
surfaces; nothing marked is ever touched outside the accepted scripts.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_core import PostgresRunner
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.crm_commands_wu5 import (
    CrmCommandDenied,
    CrmCommandValidationError,
    ProcessInboundMessageCommand,
)
from api.http_contracts import envelope
from api.inbound_boundary import InboundPayloadInvalid, parse_inbound_message_event
from api.queries.demo_cache import build_scoped_key
from api.tenant_boundary import (
    TenantAccessDenied,
    TenantAuthenticationRequired,
)

SIMULATE_ROUTE = "/simulate"
DEMO_SIMULATE_PREFIX = "/v1/demo"
SIMULATE_PATH = f"{DEMO_SIMULATE_PREFIX}{SIMULATE_ROUTE}"
SIMULATOR_ENVIRONMENT = "dev-only-simulator"
CANONICAL_ACCOUNT_SLOT = {
    "20000000-0000-0000-0000-000000000001": {
        "conversation_id": "66000000-0000-0000-0000-000000000001",
        "event_slot": "a",
        "stage_name": "Simulador: visita calificada",
    },
    "20000000-0000-0000-0000-000000000002": {
        "conversation_id": "66000000-0000-0000-0000-000000000002",
        "event_slot": "b",
        "stage_name": "Simulador: visita calificada",
    },
}


def canonical_simulator_payload(account_id: str, *, body: str, received_at: str) -> dict[str, Any]:
    """Provider-neutral canonical shape (the approved WU1/WU4 inbound shape).

    conversationId targets the seeded conversation of the session account;
    clientAccountId is only a claim (server context wins). eventId is
    DETERMINISTIC from (account slot, body, receivedAt): the same button click
    sent twice produces the same event, the same idempotency-key fallback and
    therefore a replay with the canonical result. message.id derives from the
    eventId (uuid5): replay protection lives in the idempotency reservation
    (WU5), the message primary key stays stable for identical events.
    """
    slot = CANONICAL_ACCOUNT_SLOT[account_id]
    normalized_body = body.strip()
    digest = hashlib.sha256(
        f"{slot['event_slot']}|{normalized_body}|{received_at}".encode("utf-8")
    ).hexdigest()
    event_id = f"sim-{slot['event_slot']}-{digest[:12]}"
    message_id = str(uuid5(NAMESPACE_URL, f"mvp-simulator:{event_id}"))
    return {
        "eventId": event_id,
        "clientAccountId": account_id,
        "conversationId": slot["conversation_id"],
        "message": {
            "id": message_id,
            "conversationId": slot["conversation_id"],
            "clientAccountId": account_id,
            "direction": "inbound",
            "body": normalized_body,
            "receivedAt": received_at,
        },
    }


async def ensure_simulator_stage(runner: PostgresRunner, account_id: str) -> str:
    """WU9 demo fixture: deterministic second pipeline stage so the accepted
    WU5 inbound effect (advance to next declared stage) is visible in the
    demo. UNMARKED on purpose (demo_seed_key NULL): the accepted reset keeps
    failing closed over it until the tested clear/force path removes it, so
    the seed/reset ownership contract is untouched. ON CONFLICT makes it
    idempotent across clicks and restarts."""
    slot = CANONICAL_ACCOUNT_SLOT[account_id]
    stage_id = str(uuid5(NAMESPACE_URL, f"mvp-sim-stage:{account_id}"))
    await runner.execute(
        """
        INSERT INTO pipeline_stages (id, client_account_id, pipeline_id, name, position)
        SELECT %s, %s, p.id, %s, 1
          FROM pipelines p
         WHERE p.client_account_id = %s
           AND NOT EXISTS (SELECT 1 FROM pipeline_stages s
                            WHERE s.pipeline_id = p.id AND s.position = 1)
        ON CONFLICT (id) DO NOTHING
        """,
        (stage_id, account_id, slot["stage_name"], account_id),
    )
    return stage_id


# ---------------------------------------------------------------------------
# blocked-reset: simulator effect inventory + accepted-harness clear rules
# ---------------------------------------------------------------------------

async def count_pending_effects(runner: PostgresRunner, account_ids: tuple[str, str]) -> int:
    """Rows the accepted fail-closed reset will refuse to delete (children of
    seed rows without demo markers). Same predicate shape as the WU7 harness
    clear_unmarked helper."""
    placeholders = ", ".join(["%s"] * len(account_ids))
    sql = f"""
        SELECT
          (SELECT count(*) FROM messages WHERE demo_seed_key IS NULL) +
          (SELECT count(*) FROM follow_ups WHERE demo_seed_key IS NULL) +
          (SELECT count(*) FROM leads WHERE demo_seed_key IS NULL) +
          (SELECT count(*) FROM pipeline_stages WHERE demo_seed_key IS NULL) +
          (SELECT count(*) FROM conversations WHERE demo_seed_key IS NULL) +
          (SELECT count(*) FROM contacts WHERE demo_seed_key IS NULL) +
          (SELECT count(*) FROM command_idempotency) +
          (SELECT count(*) FROM audit_records
             WHERE client_account_id IS NULL OR client_account_id IN ({placeholders})) +
          (SELECT count(*) FROM outbox_events
             WHERE client_account_id IS NULL OR client_account_id IN ({placeholders}))
          AS n
    """
    params = tuple(account_ids) * 2
    rows = await runner.query(sql, params)
    return int(rows[0]["n"]) if rows else -1


async def clear_simulator_effects(runner: PostgresRunner) -> int:
    """Delete only simulator/command effect rows (unmarked), mirroring the
    WU7 harness clear_unmarked rules — the exact statements that unblock the
    accepted reset after command effects (fail-closed contract D9c). Returns
    the number of deleted CRM effect rows (evidence tables are flushed too).

    Deliberately narrower than a general reset: seed-marked rows are never
    touched here; the accepted mvp_reset/mvp_seed scripts still own them.
    """
    statements = (
        "DELETE FROM follow_ups WHERE demo_seed_key IS NULL",
        "DELETE FROM messages WHERE demo_seed_key IS NULL",
        "UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000001', "
        "status='new', updated_at = now() WHERE demo_seed_key = 'demo-a-lead-001'",
        "UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000002', "
        "status='qualified', updated_at = now() WHERE demo_seed_key = 'demo-b-lead-001'",
        "DELETE FROM leads WHERE demo_seed_key IS NULL",
        "DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL",
        "DELETE FROM conversations WHERE demo_seed_key IS NULL",
        "DELETE FROM contacts WHERE demo_seed_key IS NULL",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records WHERE client_account_id IS NULL OR "
        "client_account_id IN ('20000000-0000-0000-0000-000000000001', "
        "'20000000-0000-0000-0000-000000000002')",
        "DELETE FROM outbox_events WHERE client_account_id IS NULL OR "
        "client_account_id IN ('20000000-0000-0000-0000-000000000001', "
        "'20000000-0000-0000-0000-000000000002')",
    )
    deleted = 0
    for stmt in statements:
        deleted += await runner.execute(stmt)
    return deleted


# ---------------------------------------------------------------------------
# simulator router (mounted onto the WU8 app)
# ---------------------------------------------------------------------------

def build_simulator_router(conn_kwargs: dict[str, Any]) -> APIRouter:
    """POST /v1/demo/simulate — labeled dev-only simulator over the atomic WU5
    inbound command. Body (all optional): body, receivedAt, idempotencyKey.
    The server owns scope, event ids, conversation and the key's fallback."""
    router = APIRouter()
    command = ProcessInboundMessageCommand(dict(conn_kwargs))

    @router.post(SIMULATE_ROUTE)
    async def simulate(request: Request):
        # DEV-ONLY simulator surface: no Kapso, no WhatsApp, no LLM. Identity
        # is the server-owned demo user; scope comes from the session account.
        composition = request.app.state.demo_composition
        cache = composition.cache
        correlation = request.state.correlation_id
        try:
            raw = await request.json()
        except Exception:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        # optional overrides must be clean strings; anything else uses the
        # canonical default (never a server-side AttributeError).
        raw_body = raw.get("body")
        body_text = raw_body.strip() if isinstance(raw_body, str) and raw_body.strip() else \
            "Simulador entrante: me gustaría visitar la propiedad."
        raw_ts = raw.get("receivedAt")
        received_at = raw_ts.strip() if isinstance(raw_ts, str) and raw_ts.strip() \
            else "2026-09-05T12:00:00.000Z"
        explicit_key = raw.get("idempotencyKey")

        try:
            context = await composition.resolver.resolve(request, request.app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required", "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)

        account_id = context.client_account.id
        if account_id not in CANONICAL_ACCOUNT_SLOT:
            # only the two demo accounts have canonical slots; never a silent fallback
            return envelope(403, "tenant_access_denied", "access denied", correlation)

        payload = canonical_simulator_payload(account_id, body=body_text, received_at=received_at)
        try:
            event = parse_inbound_message_event(payload)
        except InboundPayloadInvalid as error:
            return envelope(400, "invalid_payload", str(error), correlation)

        key = explicit_key.strip() if isinstance(explicit_key, str) and explicit_key.strip() \
            else f"sim-event:{event.event_id}"
        # deterministic demo fixture: the advance effect needs a declared next
        # stage; unmarked, so the accepted reset ownership contract is intact.
        await ensure_simulator_stage(composition.crm.runner, account_id)
        try:
            outcome = await command.execute_event(context, event=event, idempotency_key=key)
        except CrmCommandDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)
        except CrmCommandValidationError as error:
            return envelope(400, "invalid_command", str(error), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict", "command idempotency conflict", correlation)

        # durable effects changed account state: drop the scoped cache entries
        cache.invalidate_scope(context.agency.id, context.client_account.id)
        return JSONResponse(
            status_code=200,
            headers={"X-Correlation-Id": correlation},
            content={
                "contract_version": "v1",
                "environment": SIMULATOR_ENVIRONMENT,
                "accepted": True,
                "replayed": outcome.replayed,
                "correlation_id": correlation,
                "result": outcome.payload,
            },
        )

    return router
