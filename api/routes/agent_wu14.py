"""Work Unit 14 (LLM Conversational Agent): analyze + draft decisions.

Thin WU14-owned glue over accepted seams — nothing accepted is modified:
ConversationAgent + Analyze/Decide/Finalize commands (WU14), the accepted
WU5 follow-up command, WU11 interest command, WU13 calendar commands,
TenantContextResolver and the WU8 app.state (resolver, scoped cache).

The model provider is injected (`build_agent_router(conn_kwargs, model_provider)`);
absent, the app selects from the environment (key -> opencode-go, none -> stub).

Approval is a saga: the atomic decision, then per-action dispatch through the
accepted commands with keys `agent-draft:{draft_id}:{index}` (idempotent — a
retry re-runs safely), then one atomic finalize that records the aggregate
`execution_result` and moves approved -> executed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_agent_drafts import (
    AgentDraftRepository,
    conversation_contact,
    free_slots,
    lead_for_contact,
    search_properties_for_criteria,
    upcoming_event,
)
from api.adapters.postgres_core import ConnectionPool, PostgresRunner
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.agent_commands_wu14 import (
    AnalyzeInboundMessageCommand,
    DecideAgentDraftCommand,
    FinalizeAgentDraftCommand,
)
from api.application.calendar_commands_wu13 import (
    CreateCalendarEventCommand,
    UpdateCalendarEventCommand,
)
from api.application.conversation_agent import ConversationAgent
from api.application.crm_commands_wu5 import (
    CrmCommandDenied,
    CrmCommandValidationError,
    CreateFollowUpCommand,
)
from api.application.model_provider import (
    InterpretationError,
    ProviderError,
    select_model_provider,
)
from api.application.property_commands_wu11 import RegisterPropertyInterestCommand
from api.domain.agent_draft import AgentDraft
from api.domain.commercial_crm import FollowUp
from api.http_contracts import envelope
from api.routes.simulator_wu9 import CANONICAL_ACCOUNT_SLOT
from api.tenant_boundary import (
    TenantAccessDenied,
    TenantAuthenticationRequired,
)

AGENT_PREFIX = "/agent"
ANALYZE_ROUTE = f"{AGENT_PREFIX}/analyze"
DRAFTS_ROUTE = f"{AGENT_PREFIX}/drafts"
AGENT_ENVIRONMENT = "dev-only-agent"

_ANALYZE_KEYS = frozenset({"body", "idempotencyKey"})

_NOT_FOUND_REASONS = frozenset({
    "conversation_not_found_in_scope", "contact_not_found_in_scope",
    "draft_not_found_in_scope",
})
_CONFLICT_REASONS = frozenset({"draft_not_proposed", "draft_not_approved"})


def _iso(value: datetime | None) -> str | None:
    return (value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z") if value else None)


def _draft_json(draft: AgentDraft) -> dict[str, Any]:
    return {
        "draft_id": draft.id,
        "status": draft.status,
        "intent": draft.intent,
        "criteria": draft.criteria,
        "matched_property_ids": list(draft.matched_property_ids),
        "proposed_actions": [a.to_dict() for a in draft.proposed_actions],
        "reply_text": draft.reply_text,
        "model_provider": draft.model_provider,
        "model_name": draft.model_name,
        "conversation_id": draft.conversation_id,
        "contact_id": draft.contact_id,
        "lead_id": draft.lead_id,
        "created_at": _iso(draft.created_at),
        "decided_at": _iso(draft.decided_at),
        "execution_result": (list(draft.execution_result)
                             if draft.execution_result is not None else None),
    }


def _key(raw: Any, fallback: str) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return fallback


def build_agent_router(conn_kwargs: dict[str, Any], model_provider: Any | None = None) -> APIRouter:
    """POST analyze + draft list/get/approve/reject, over the session account."""
    router = APIRouter()
    provider = model_provider or select_model_provider()
    runner = PostgresRunner(ConnectionPool(dict(conn_kwargs)))
    drafts = AgentDraftRepository(runner)
    agent = ConversationAgent(
        provider,
        search=lambda ctx, criteria: search_properties_for_criteria(ctx, runner, criteria),
        availability=lambda ctx, **kw: free_slots(ctx, runner, **kw),
        upcoming=lambda ctx, **kw: upcoming_event(ctx, runner, **kw),
    )
    analyze_cmd = AnalyzeInboundMessageCommand(dict(conn_kwargs))
    decide_cmd = DecideAgentDraftCommand(dict(conn_kwargs))
    finalize_cmd = FinalizeAgentDraftCommand(dict(conn_kwargs))
    follow_up_cmd = CreateFollowUpCommand(dict(conn_kwargs))
    interest_cmd = RegisterPropertyInterestCommand(dict(conn_kwargs))
    create_event_cmd = CreateCalendarEventCommand(dict(conn_kwargs))
    update_event_cmd = UpdateCalendarEventCommand(dict(conn_kwargs))

    async def _context(request: Request, correlation: str):
        composition = request.app.state.demo_composition
        try:
            context = await composition.resolver.resolve(
                request, request.app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return None, envelope(401, "authentication_required",
                                  "authentication is required", correlation)
        except TenantAccessDenied:
            return None, envelope(403, "tenant_access_denied", "access denied", correlation)
        return context, None

    def _denial(problem: CrmCommandDenied, correlation: str):
        if problem.reason in _NOT_FOUND_REASONS:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        if problem.reason in _CONFLICT_REASONS:
            return envelope(409, problem.reason, "draft is not in an actionable state",
                            correlation)
        return envelope(403, "tenant_access_denied", "access denied", correlation)

    @router.post(ANALYZE_ROUTE)
    async def analyze(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            raw = await request.json()
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            return envelope(400, "invalid_command", "analyze body must be an object", correlation)
        unknown = set(raw.keys()) - _ANALYZE_KEYS
        if unknown:
            return envelope(400, "invalid_command",
                            f"unsupported keys: {sorted(unknown)}", correlation)
        body = raw.get("body")
        if not isinstance(body, str) or not body.strip() or len(body) > 4000:
            return envelope(400, "invalid_command", "body must be a non-empty string", correlation)
        body = body.strip()
        account_id = context.client_account.id
        slot = CANONICAL_ACCOUNT_SLOT.get(account_id)
        if slot is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        conversation_id = slot["conversation_id"]
        contact_id = await conversation_contact(context, runner, conversation_id)
        if contact_id is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        lead_id = await lead_for_contact(context, runner, contact_id)
        now = datetime.now(timezone.utc)
        try:
            plan = await agent.analyze(context, body, contact_id=contact_id,
                                       lead_id=lead_id, now=now)
        except InterpretationError:
            return envelope(502, "invalid_model_output",
                            "model response did not match the bounded schema", correlation)
        except ProviderError:
            return envelope(502, "provider_unavailable",
                            "model provider is unavailable", correlation)
        fallback_key = "agent-analyze:" + hashlib.sha256(
            f"{account_id}|{conversation_id}|{body}".encode("utf-8")).hexdigest()[:24]
        try:
            outcome = await analyze_cmd.execute_analyze(
                context, body=body, conversation_id=conversation_id,
                contact_id=contact_id, plan=plan,
                idempotency_key=_key(raw.get("idempotencyKey"), fallback_key))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        draft = await drafts.get(context, outcome.payload["resource_id"])
        assert draft is not None
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": AGENT_ENVIRONMENT,
            "accepted": True, "replayed": outcome.replayed,
            "correlation_id": correlation, "draft": _draft_json(draft),
        })

    @router.get(DRAFTS_ROUTE)
    async def list_drafts(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        conversation_id = request.query_params.get("conversation_id")
        if conversation_id is not None and not conversation_id.strip():
            conversation_id = None
        try:
            limit = int(request.query_params.get("limit") or 20)
        except ValueError:
            return envelope(400, "invalid_query", "limit must be an integer", correlation)
        items = await drafts.list_conversation(context, conversation_id=conversation_id,
                                               limit=limit)
        return JSONResponse(status_code=200, content={
            "account_id": context.client_account.id, "as_of": _iso(datetime.now(timezone.utc)),
            "correlation_id": correlation, "count": len(items),
            "items": [_draft_json(d) for d in items],
        }, headers={"X-Correlation-Id": correlation})

    async def _get_draft(request: Request, draft_id: str, correlation: str):
        context, error = await _context(request, correlation)
        if error is not None:
            return None, None, error
        draft = await drafts.get(context, draft_id)
        if draft is None:
            return None, None, envelope(404, "resource_not_found",
                                        "resource is not available in this account scope",
                                        correlation)
        return context, draft, None

    @router.get(DRAFTS_ROUTE + "/{draft_id}")
    async def get_draft(request: Request, draft_id: str):
        correlation = request.state.correlation_id
        _context_, draft, error = await _get_draft(request, draft_id, correlation)
        if error is not None:
            return error
        return JSONResponse(status_code=200, content={
            "account_id": draft.client_account_id, "as_of": _iso(datetime.now(timezone.utc)),
            "correlation_id": correlation, "draft": _draft_json(draft),
        }, headers={"X-Correlation-Id": correlation})

    @router.post(DRAFTS_ROUTE + "/{draft_id}/reject")
    async def reject_draft(request: Request, draft_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            outcome = await decide_cmd.execute_decision(
                context, draft_id=draft_id, decision="reject",
                idempotency_key=f"agent-decide:{draft_id}")
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        draft = await drafts.get(context, outcome.payload["resource_id"])
        assert draft is not None
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": AGENT_ENVIRONMENT,
            "accepted": True, "replayed": outcome.replayed,
            "correlation_id": correlation, "draft": _draft_json(draft),
        })

    @router.post(DRAFTS_ROUTE + "/{draft_id}/approve")
    async def approve_draft(request: Request, draft_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        draft = await drafts.get(context, draft_id)
        if draft is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        replayed_decision = False
        if draft.status == "proposed":
            try:
                outcome = await decide_cmd.execute_decision(
                    context, draft_id=draft_id, decision="approve",
                    idempotency_key=f"agent-decide:{draft_id}")
                replayed_decision = outcome.replayed
            except CrmCommandDenied as problem:
                return _denial(problem, correlation)
            except CrmCommandValidationError as problem:
                return envelope(400, "invalid_command", str(problem), correlation)
            except IdempotencyConflict:
                return envelope(409, "tenant_idempotency_conflict",
                                "command idempotency conflict", correlation)
            draft = await drafts.get(context, draft_id)
            assert draft is not None
        elif draft.status == "approved":
            # saga retry: the decision exists; re-dispatch (actions are idempotent)
            pass
        else:
            return envelope(409, "draft_not_proposed",
                            "draft is not in an actionable state", correlation)

        account_id = context.client_account.id
        results: list[dict[str, Any]] = []
        for index, action in enumerate(draft.proposed_actions):
            entry: dict[str, Any] = {"index": index, "type": action.type}
            key = f"agent-draft:{draft.id}:{index}"
            params = action.params
            try:
                if action.type == "create_follow_up":
                    due_at = datetime.fromisoformat(str(params["due_at"]).replace("Z", "+00:00"))
                    follow_up = FollowUp(
                        id=str(uuid5(NAMESPACE_URL, key)), client_account_id=account_id,
                        lead_id=params.get("lead_id"), contact_id=params.get("contact_id"),
                        title=str(params["title"])[:200], due_at=due_at, status="pending")
                    outcome = await follow_up_cmd.execute_follow_up(
                        context, follow_up=follow_up, idempotency_key=key)
                elif action.type == "register_interest":
                    outcome = await interest_cmd.execute_register(
                        context, property_id=str(params["property_id"]),
                        contact_id=str(params["contact_id"]),
                        origin=str(params.get("origin", "ai_agent")), idempotency_key=key)
                elif action.type == "create_event":
                    outcome = await create_event_cmd.execute_create(
                        context, title=str(params["title"])[:200],
                        starts_at=str(params["starts_at"]), ends_at=str(params["ends_at"]),
                        contact_id=params.get("contact_id"), source="agent",
                        idempotency_key=key)
                elif action.type == "reschedule_event":
                    outcome = await update_event_cmd.execute_update(
                        context, event_id=str(params["event_id"]),
                        starts_at=str(params["starts_at"]), ends_at=str(params["ends_at"]),
                        idempotency_key=key)
                else:
                    entry.update(outcome="unsupported")
                    results.append(entry)
                    continue
                entry.update(outcome="replayed" if outcome.replayed else "success",
                             resource_id=outcome.payload.get("resource_id"))
            except CrmCommandDenied as problem:
                entry.update(outcome="denied", reason=problem.reason)
            except CrmCommandValidationError as problem:
                entry.update(outcome="invalid", reason=str(problem))
            except IdempotencyConflict:
                entry.update(outcome="conflict")
            except KeyError:
                entry.update(outcome="invalid", reason="missing action params")
            results.append(entry)

        try:
            await finalize_cmd.execute_finalize(
                context, draft_id=draft.id, execution_result=results,
                idempotency_key=f"agent-finalize:{draft.id}")
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        draft = await drafts.get(context, draft_id)
        assert draft is not None
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": AGENT_ENVIRONMENT,
            "accepted": True, "replayed": replayed_decision,
            "correlation_id": correlation, "draft": _draft_json(draft),
        })

    return router
