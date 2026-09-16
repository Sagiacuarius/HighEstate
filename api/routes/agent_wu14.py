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
from dataclasses import replace
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
from api.adapters.postgres_knowledge_base import KnowledgeBaseRepository
from api.application.agent_commands_wu14 import (
    AnalyzeInboundMessageCommand,
    DecideAgentDraftCommand,
    FinalizeAgentDraftCommand,
)
from api.application.agent_commands_wu15 import (
    PersistOutboundReplyCommand,
    SetConversationAgentModeCommand,
)
from api.application.agent_policy import ESCALATION_MECHANICS
from api.application.calendar_commands_wu13 import (
    CreateCalendarEventCommand,
    UpdateCalendarEventCommand,
)
from api.application.conversation_agent import ConversationAgent
from api.application.crm_commands_wu5 import (
    CrmCommandDenied,
    CrmCommandValidationError,
    CreateFollowUpCommand,
    TransitionLeadStageCommand,
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
AGENT_STATE_ROUTE = f"{AGENT_PREFIX}/state"
AGENT_RESUME_ROUTE = f"{AGENT_PREFIX}/handoff/resume"
AGENT_ENVIRONMENT = "dev-only-agent"

_ANALYZE_KEYS = frozenset({"body", "idempotencyKey"})
_STATE_KEYS = frozenset({"mode"})

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
        "policy_decision": draft.policy_decision,
        "execution_mode": draft.execution_mode,
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


async def _enrich_draft(runner: PostgresRunner, account_id: str,
                        draft: AgentDraft) -> AgentDraft:
    """Attach the persisted WU15 attribution columns.

    `AgentDraftRepository` is an accepted read owned by WU14 (untouched), so
    the three migration-008 columns are re-read here with the same
    account-scoped predicate; absent rows/columns fall back to human-path
    defaults.
    """
    try:
        rows = await runner.query(
            "SELECT policy_decision, execution_mode, auto_decision_actor "
            "FROM agent_drafts WHERE id = %s AND client_account_id = %s",
            (draft.id, account_id),
        )
    except Exception:
        return draft
    if not rows:
        return draft
    row = rows[0]
    actor = row.get("auto_decision_actor")
    return replace(
        draft,
        policy_decision=row.get("policy_decision") or "escalate",
        execution_mode=row.get("execution_mode") or "human",
        auto_decision_actor=str(actor) if actor else None,
    )


def _state_shape(*, conversation_id: str, agent_mode: str, account_id: str,
                 correlation: str) -> dict[str, Any]:
    return {
        "contract_version": "v1",
        "environment": AGENT_ENVIRONMENT,
        "conversation_id": conversation_id,
        "agent_mode": agent_mode,
        "handoff_open": agent_mode == "paused",
        "account_id": account_id,
        "correlation_id": correlation,
    }


async def _dispatch_actions(*, context: Any, draft: AgentDraft,
                            indexed: list[tuple[int, Any]], account_id: str,
                            follow_up_cmd: Any, interest_cmd: Any,
                            create_event_cmd: Any, update_event_cmd: Any,
                            reply_cmd: Any, mode_cmd: Any,
                            transition_cmd: Any) -> list[dict[str, Any]]:
    """Shared per-action saga dispatch (approve, auto path, handoff
    escalation). Keys stay `agent-draft:{draft_id}:{original_index}` so a
    filtered (mechanics-only) dispatch remains idempotent with the full one.
    Existing branches are byte-identical to the accepted approve loop."""
    results: list[dict[str, Any]] = []
    for index, action in indexed:
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
            elif action.type == "send_reply":
                outcome = await reply_cmd.execute_reply(
                    context, conversation_id=str(draft.conversation_id),
                    source=str(params["source"]), body=str(params["reply_text"]),
                    article_id=(str(params["article_id"])
                                if params.get("article_id") is not None else None),
                    property_id=(str(params["property_id"])
                                 if params.get("property_id") is not None else None),
                    external_event_id=key, idempotency_key=key)
            elif action.type == "handoff_to_human":
                outcome = await mode_cmd.execute_mode(
                    context, conversation_id=str(draft.conversation_id),
                    mode="paused", idempotency_key=key)
            elif action.type == "transition_lead":
                outcome = await transition_cmd.execute_transition(
                    context, lead_id=str(params["lead_id"]),
                    to_stage_id=str(params["to_stage_id"]),
                    new_status=str(params.get("new_status", "qualified")),
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
    return results


def build_agent_router(conn_kwargs: dict[str, Any], model_provider: Any | None = None) -> APIRouter:
    """POST analyze + draft list/get/approve/reject, over the session account."""
    router = APIRouter()
    provider = model_provider or select_model_provider()
    runner = PostgresRunner(ConnectionPool(dict(conn_kwargs)))
    drafts = AgentDraftRepository(runner)
    kb_repo = KnowledgeBaseRepository(runner)

    async def _kb_lookup(ctx: Any, text: str) -> dict[str, Any] | None:
        return await kb_repo.find_best_match(ctx, text)

    async def _price_read(ctx: Any, property_id: str) -> dict[str, Any] | None:
        rows = await runner.query(
            "SELECT id, title, price_amount, price_currency, operation_type "
            "FROM properties WHERE id = %s AND client_account_id = %s "
            "AND status = 'available'",
            (property_id, ctx.client_account.id),
        )
        if not rows:
            return None
        row = rows[0]
        return {"id": str(row["id"]), "title": row["title"],
                "price_amount": row["price_amount"],
                "price_currency": row["price_currency"],
                "operation_type": row["operation_type"]}

    async def _last_interest(ctx: Any, contact_id: str) -> str | None:
        rows = await runner.query(
            "SELECT property_id FROM property_interests "
            "WHERE client_account_id = %s AND contact_id = %s "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (ctx.client_account.id, contact_id),
        )
        return str(rows[0]["property_id"]) if rows else None

    async def _stage_id_for_name(ctx: Any, lead_id: str, name: str) -> str | None:
        rows = await runner.query(
            "SELECT s.id FROM pipeline_stages s "
            "JOIN leads l ON l.pipeline_id = s.pipeline_id "
            "AND l.client_account_id = s.client_account_id "
            "WHERE s.client_account_id = %s AND l.id = %s AND s.name = %s",
            (ctx.client_account.id, lead_id, name),
        )
        return str(rows[0]["id"]) if rows else None

    agent = ConversationAgent(
        provider,
        search=lambda ctx, criteria: search_properties_for_criteria(ctx, runner, criteria),
        availability=lambda ctx, **kw: free_slots(ctx, runner, **kw),
        upcoming=lambda ctx, **kw: upcoming_event(ctx, runner, **kw),
        kb_lookup=_kb_lookup,
        price_read=_price_read,
        last_interest=_last_interest,
        stage_id_for_name=_stage_id_for_name,
    )
    analyze_cmd = AnalyzeInboundMessageCommand(dict(conn_kwargs))
    decide_cmd = DecideAgentDraftCommand(dict(conn_kwargs))
    finalize_cmd = FinalizeAgentDraftCommand(dict(conn_kwargs))
    follow_up_cmd = CreateFollowUpCommand(dict(conn_kwargs))
    interest_cmd = RegisterPropertyInterestCommand(dict(conn_kwargs))
    create_event_cmd = CreateCalendarEventCommand(dict(conn_kwargs))
    update_event_cmd = UpdateCalendarEventCommand(dict(conn_kwargs))
    reply_cmd = PersistOutboundReplyCommand(dict(conn_kwargs))
    mode_cmd = SetConversationAgentModeCommand(dict(conn_kwargs))
    transition_cmd = TransitionLeadStageCommand(dict(conn_kwargs))

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
        draft = await _enrich_draft(runner, account_id, draft)
        auto_executed = (
            draft.status == "executed" and draft.execution_mode == "agent_policy"
        )
        if not outcome.replayed:
            mode_rows = await runner.query(
                "SELECT agent_mode FROM conversations "
                "WHERE id = %s AND client_account_id = %s",
                (conversation_id, account_id),
            )
            agent_mode = mode_rows[0]["agent_mode"] if mode_rows else "manual"
            verdict = (plan.get("policy") or {}).get("verdict")
            indexed = list(enumerate(draft.proposed_actions))
            auto_indexed: list[tuple[int, Any]] | None = None
            if agent_mode == "auto" and verdict == "auto":
                auto_indexed = indexed
            elif (agent_mode == "auto" and verdict == "escalate"
                    and draft.intent == "handoff"):
                # Handoff escalation runs mechanics only (pause + follow-up);
                # a reply to the lead never dispatches. Original indexes keep
                # the per-action idempotency keys stable.
                auto_indexed = [(i, a) for i, a in indexed
                                if a.type in ESCALATION_MECHANICS]
            if auto_indexed is not None:
                try:
                    await decide_cmd.execute_decision(
                        context, draft_id=draft.id, decision="auto",
                        idempotency_key=f"agent-decide:{draft.id}")
                except CrmCommandDenied as problem:
                    return _denial(problem, correlation)
                except IdempotencyConflict:
                    return envelope(409, "tenant_idempotency_conflict",
                                    "command idempotency conflict", correlation)
                results = await _dispatch_actions(
                    context=context, draft=draft, indexed=auto_indexed,
                    account_id=account_id, follow_up_cmd=follow_up_cmd,
                    interest_cmd=interest_cmd, create_event_cmd=create_event_cmd,
                    update_event_cmd=update_event_cmd, reply_cmd=reply_cmd,
                    mode_cmd=mode_cmd, transition_cmd=transition_cmd)
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
                draft = await drafts.get(context, draft.id)
                assert draft is not None
                draft = await _enrich_draft(runner, account_id, draft)
                auto_executed = True
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": AGENT_ENVIRONMENT,
            "accepted": True, "replayed": outcome.replayed,
            "auto_executed": auto_executed,
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
        enriched = [await _enrich_draft(runner, context.client_account.id, d)
                    for d in items]
        return JSONResponse(status_code=200, content={
            "account_id": context.client_account.id, "as_of": _iso(datetime.now(timezone.utc)),
            "correlation_id": correlation, "count": len(enriched),
            "items": [_draft_json(d) for d in enriched],
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
        draft = await _enrich_draft(runner, _context_.client_account.id, draft)
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
        draft = await _enrich_draft(runner, context.client_account.id, draft)
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
        # Manual override: the advisor's click dispatches the FULL plan and
        # never consults the policy.
        results = await _dispatch_actions(
            context=context, draft=draft,
            indexed=list(enumerate(draft.proposed_actions)),
            account_id=account_id, follow_up_cmd=follow_up_cmd,
            interest_cmd=interest_cmd, create_event_cmd=create_event_cmd,
            update_event_cmd=update_event_cmd, reply_cmd=reply_cmd,
            mode_cmd=mode_cmd, transition_cmd=transition_cmd)

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
        draft = await _enrich_draft(runner, account_id, draft)
        return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": AGENT_ENVIRONMENT,
            "accepted": True, "replayed": replayed_decision,
            "auto_executed": False,
            "correlation_id": correlation, "draft": _draft_json(draft),
        })

    @router.get(AGENT_STATE_ROUTE)
    async def get_agent_state(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        account_id = context.client_account.id
        slot = CANONICAL_ACCOUNT_SLOT.get(account_id)
        if slot is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        rows = await runner.query(
            "SELECT agent_mode FROM conversations "
            "WHERE id = %s AND client_account_id = %s",
            (slot["conversation_id"], account_id),
        )
        if not rows:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        return JSONResponse(status_code=200, content=_state_shape(
            conversation_id=slot["conversation_id"],
            agent_mode=rows[0]["agent_mode"], account_id=account_id,
            correlation=correlation), headers={"X-Correlation-Id": correlation})

    @router.post(AGENT_STATE_ROUTE)
    async def set_agent_state(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            raw = await request.json()
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            return envelope(400, "invalid_command", "state body must be an object", correlation)
        unknown = set(raw.keys()) - _STATE_KEYS
        if unknown:
            return envelope(400, "invalid_command",
                            f"unsupported keys: {sorted(unknown)}", correlation)
        mode = raw.get("mode")
        # Dev-only surface: manual<->auto toggle; paused is handoff-only and
        # can never be set here (nor resumed through here).
        if mode not in ("manual", "auto"):
            return envelope(400, "invalid_command",
                            "mode must be manual or auto", correlation)
        account_id = context.client_account.id
        slot = CANONICAL_ACCOUNT_SLOT.get(account_id)
        if slot is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        rows = await runner.query(
            "SELECT agent_mode FROM conversations "
            "WHERE id = %s AND client_account_id = %s",
            (slot["conversation_id"], account_id),
        )
        if not rows:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        if rows[0]["agent_mode"] == "paused":
            return envelope(409, "agent_handoff_open",
                            "handoff is open; resume before switching mode", correlation)
        try:
            outcome = await mode_cmd.execute_mode(
                context, conversation_id=slot["conversation_id"], mode=mode,
                idempotency_key=f"agent-mode:{slot['conversation_id']}:{mode}")
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return JSONResponse(status_code=200, content=_state_shape(
            conversation_id=slot["conversation_id"],
            agent_mode=outcome.payload["agent_mode"], account_id=account_id,
            correlation=correlation), headers={"X-Correlation-Id": correlation})

    @router.post(AGENT_RESUME_ROUTE)
    async def resume_handoff(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        account_id = context.client_account.id
        slot = CANONICAL_ACCOUNT_SLOT.get(account_id)
        if slot is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        rows = await runner.query(
            "SELECT agent_mode FROM conversations "
            "WHERE id = %s AND client_account_id = %s",
            (slot["conversation_id"], account_id),
        )
        if not rows:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        if rows[0]["agent_mode"] != "paused":
            return envelope(409, "agent_handoff_not_open",
                            "no open handoff to resume", correlation)
        try:
            outcome = await mode_cmd.execute_mode(
                context, conversation_id=slot["conversation_id"], mode="auto",
                idempotency_key=f"agent-resume:{slot['conversation_id']}")
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return JSONResponse(status_code=200, content=_state_shape(
            conversation_id=slot["conversation_id"],
            agent_mode=outcome.payload["agent_mode"], account_id=account_id,
            correlation=correlation), headers={"X-Correlation-Id": correlation})

    return router
