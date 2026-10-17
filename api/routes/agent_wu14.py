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
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_agent_console import (
    ALL_CONSOLE_ACTION_TYPES,
    LEGACY_FIELD_ALLOWLIST,
    ConsoleSettings,
    default_calendar_config,
    default_verdict,
    load_calendar_config,
    load_console_settings,
    load_field_allowlist,
    load_signal_bindings,
    preview_prompt,
    score_question,
)
from api.adapters.postgres_configuration import (
    list_contacts_by_tag,
    load_custom_field_definitions,
    load_custom_field_values,
)
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
from api.adapters.postgres_knowledge_base import (
    KnowledgeBaseRepository,
    normalize_tokens,
)
from api.application.agent_commands_wu14 import (
    AnalyzeInboundMessageCommand,
    DecideAgentDraftCommand,
    FinalizeAgentDraftCommand,
)
from api.application.agent_commands_wu15 import (
    PersistOutboundReplyCommand,
    SetConversationAgentModeCommand,
)
from api.application.agent_commands_wu16 import (
    ALLOWLIST_COLUMNS,
    KB_CATEGORIES,
    CreateAgentPromptVersionCommand,
    CreateKbArticleCommand,
    DeactivateKbArticleCommand,
    UpdateAgentSettingsCommand,
    UpdateCalendarConfigCommand,
    UpdateFieldAllowlistCommand,
    UpdateKbArticleCommand,
)
from api.application.agent_persona import build_system_prompt, compose_prompt
from api.application.agent_policy import (
    AUTO_ACTION_TYPES,
    AUTO_EVENT_CREATION,
    ESCALATION_MECHANICS,
)
from api.application.agent_signal_bindings_commands import (
    CreateSignalBindingCommand,
    DeleteSignalBindingCommand,
)
from api.application.agent_signals import SIGNAL_KEYS
from api.application.calendar_commands_wu13 import (
    CreateCalendarEventCommand,
    UpdateCalendarEventCommand,
)
from api.application.configuration_commands_wu17 import (
    AddContactTagCommand,
    CreateCustomFieldDefinitionCommand,
    DeleteCustomFieldDefinitionCommand,
    RemoveContactTagCommand,
    SetLeadCustomFieldValueCommand,
    UpdateCustomFieldDefinitionCommand,
)
from api.application.conversation_agent import ConversationAgent
from api.application.kapso_agent_orchestrator import KapsoAgentOrchestrator
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
from api.inbound_boundary import NormalizedInboundEvent
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
    "prompt_version_not_found_in_scope", "kb_article_not_found_in_scope",
    "custom_field_not_found_in_scope", "lead_not_found_in_scope",
    "signal_binding_not_found_in_scope",
})
_CONFLICT_REASONS = frozenset({"draft_not_proposed", "draft_not_approved"})

# `TransitionLeadStageCommand._apply` writes stage_id AND status together, so
# the dispatch gate requires BOTH allowed (dual-column gate, WU16).
_TRANSITION_WRITES = frozenset({"stage_id", "status"})

# Console idempotency namespace: console:{scope}:{entity}:{key}.
_SETTINGS_KEYS = frozenset({"global_auto", "calendar_auto_enabled",
                            "matrix", "idempotencyKey"})
_PROMPT_KEYS = frozenset({"tone", "instructions", "idempotencyKey"})
_REVERT_KEYS = frozenset({"idempotencyKey"})
_KB_WRITE_KEYS = frozenset({"category", "question", "keywords", "answer",
                            "idempotencyKey"})
_KB_DELETE_KEYS = frozenset({"idempotencyKey"})
_SELFTEST_KEYS = frozenset({"question"})
_CALENDAR_KEYS = frozenset({"window_start", "window_end", "slot_minutes",
                            "working_days", "destination_label",
                            "timezone", "idempotencyKey"})
_ALLOWLIST_KEYS = frozenset({"allowed", "idempotencyKey"})
_CUSTOM_FIELD_KEYS = frozenset({"name", "type", "choices", "idempotencyKey"})
_CUSTOM_FIELD_DELETE_KEYS = frozenset({"idempotencyKey"})
_CUSTOM_VALUES_KEYS = frozenset({"values", "idempotencyKey"})
_TAGS_KEYS = frozenset({"tag", "idempotencyKey"})
_SIGNAL_BINDING_KEYS = frozenset({"signal_key", "field_definition_id",
                                  "idempotencyKey"})


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


async def _console_settings_or_default(context: Any,
                                         runner: PostgresRunner) -> ConsoleSettings:
    """Policy overlay for the context account; pre-009 bases (or any read
    failure) fall back to the code constants, so old behavior is exact."""
    try:
        return await load_console_settings(context, runner)
    except Exception:
        matrix = {action: default_verdict(action)
                  for action in ALL_CONSOLE_ACTION_TYPES}
        return ConsoleSettings(
            auto_types=frozenset(AUTO_ACTION_TYPES),
            calendar_allowed=bool(AUTO_EVENT_CREATION),
            global_auto=True,
            matrix=matrix,
        )


async def _console_allowlist_or_default(context: Any,
                                        runner: PostgresRunner) -> frozenset:
    """Allowed lead columns; pre-009 bases fall back to the legacy effect."""
    try:
        return await load_field_allowlist(context, runner)
    except Exception:
        return frozenset(LEGACY_FIELD_ALLOWLIST)


async def _console_prompt_or_default(context: Any,
                                     runner: PostgresRunner) -> str:
    """Exact system-prompt text the model receives; missing rows/tables fall
    back to the accepted defaults."""
    try:
        return await preview_prompt(context, runner)
    except Exception:
        return build_system_prompt()


def _console_key(account_id: str, entity: str, raw: dict[str, Any],
                 canonical: dict[str, Any]) -> str:
    """console:{scope}:{entity}:{key}: client key when supplied, else a
    deterministic content hash (same payload replays, new payload is new)."""
    client = raw.get("idempotencyKey")
    if isinstance(client, str) and client.strip():
        return f"console:{account_id}:{entity}:{client.strip()}"
    digest = hashlib.sha256(json.dumps(
        canonical, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()[:24]
    return f"console:{account_id}:{entity}:{digest}"


def _custom_value_key(account_id: str, raw: dict[str, Any], lead_id: str,
                      field_id: str, value: Any) -> str:
    """Per-field idempotency key for a multi-field custom-value upsert; a
    client key is disambiguated by field_id, otherwise the digest covers the
    full (lead, field, value) triple so an updated value is a new upsert."""
    client = raw.get("idempotencyKey")
    if isinstance(client, str) and client.strip():
        return f"console:{account_id}:custom-value:{client.strip()}:{field_id}"
    digest = hashlib.sha256(json.dumps(
        {"lead_id": lead_id, "field_id": field_id, "value": value},
        sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:24]
    return f"console:{account_id}:custom-value:{digest}"


def _console_ok(correlation: str, resource: str, payload: dict[str, Any],
                *, replayed: bool = False) -> JSONResponse:
    content = {"contract_version": "v1", "environment": AGENT_ENVIRONMENT,
               "accepted": True, "replayed": replayed,
               "correlation_id": correlation, resource: payload}
    return JSONResponse(status_code=200, headers={"X-Correlation-Id": correlation},
                        content=content)


async def _console_body(request: Request, correlation: str,
                        allowed: frozenset) -> tuple[dict[str, Any] | None, Any]:
    try:
        raw = await request.json()
    except Exception:
        raw = None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return None, envelope(400, "invalid_command",
                              "body must be an object", correlation)
    unknown = set(raw.keys()) - allowed
    if unknown:
        return None, envelope(400, "invalid_command",
                              f"unsupported keys: {sorted(unknown)}",
                              correlation)
    return raw, None


def _uuid_or_none(value: str) -> str | None:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError):
        return None


async def _dispatch_actions(*, context: Any, draft: AgentDraft,
                            indexed: list[tuple[int, Any]], account_id: str,
                            follow_up_cmd: Any, interest_cmd: Any,
                            create_event_cmd: Any, update_event_cmd: Any,
                            reply_cmd: Any, mode_cmd: Any,
                            transition_cmd: Any,
                            custom_field_set_cmd: Any = None,
                            allowed_lead_fields: frozenset | None = None
                            ) -> list[dict[str, Any]]:
    """Shared per-action saga dispatch (approve, auto path, handoff
    escalation). Keys stay `agent-draft:{draft_id}:{original_index}` so a
    filtered (mechanics-only) dispatch remains idempotent with the full one.
    Existing branches are byte-identical to the accepted approve loop.

    WU16 allowlist gate: the `transition_lead` branch requires BOTH
    `stage_id` AND `status` allowed (the accepted command writes both
    together); disallowed records `outcome=denied,
    reason=lead_field_not_allowed` truthfully and the saga still finalizes
    (overlap-denial precedent). None (the default) means the legacy
    pre-console effect {stage_id, status}.

    WU18 custom-field gate: the `set_custom_field_value` branch re-checks
    `params["field_name"] in allowed` at dispatch time (defense-in-depth,
    the allowlist may have changed since analyze); disallowed (or missing
    field_name) records `outcome=denied, reason=lead_field_not_allowed`
    and the saga still finalizes. Allowed delegates to the accepted WU17
    `SetLeadCustomFieldValueCommand.execute_set` (single coercion
    authority); command denials/validation surface truthfully per action.
    """
    allowed = (set(allowed_lead_fields) if allowed_lead_fields is not None
               else set(LEGACY_FIELD_ALLOWLIST))
    results: list[dict[str, Any]] = []
    for index, action in indexed:
        entry: dict[str, Any] = {"index": index, "type": action.type}
        key = f"agent-draft:{draft.id}:{index}"
        params = action.params
        if (action.type == "transition_lead"
                and not _TRANSITION_WRITES <= allowed):
            entry.update(outcome="denied", reason="lead_field_not_allowed")
            results.append(entry)
            continue
        if action.type == "set_custom_field_value":
            field_name = (params.get("field_name")
                          if isinstance(params, dict) else None)
            if field_name not in allowed:
                entry.update(outcome="denied",
                             reason="lead_field_not_allowed")
                results.append(entry)
                continue
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
            elif action.type == "set_custom_field_value":
                if custom_field_set_cmd is None:
                    entry.update(outcome="unsupported")
                    results.append(entry)
                    continue
                outcome = await custom_field_set_cmd.execute_set(
                    context, lead_id=draft.lead_id,
                    field_id=str(params["field_id"]),
                    value=params["value"], idempotency_key=key)
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

    async def _signal_bindings(ctx: Any) -> dict[str, dict[str, str]]:
        """WU18 enriched signal bindings {signal: {field_id, field_name}}.

        Joins the account's `load_signal_bindings` ({signal: field_id})
        with the defined custom-field names (the planner needs field_name
        for the allowlist gate and dispatch re-check). Tolerant {} on any
        read failure (pre-011 base), so emission stays off and bare
        behavior is exact."""
        try:
            bound = await load_signal_bindings(ctx, runner)
        except Exception:
            return {}
        if not bound:
            return {}
        try:
            definitions = await load_custom_field_definitions(ctx, runner)
        except Exception:
            return {}
        names = {str(d["id"]): d["name"] for d in definitions
                 if d.get("id") and d.get("name")}
        enriched: dict[str, dict[str, str]] = {}
        for signal_key, field_id in bound.items():
            name = names.get(str(field_id))
            if name:
                enriched[signal_key] = {"field_id": str(field_id),
                                       "field_name": name}
        return enriched

    async def _availability(ctx: Any, **kw: Any):
        """Free slots under the account's console calendar config (WU16);
        missing rows/tables fall back to the module constants, so bare
        behavior is exact on pre-009 bases."""
        try:
            calendar = await load_calendar_config(ctx, runner)
        except Exception:
            calendar = None
        return await free_slots(ctx, runner, calendar=calendar, **kw)

    agent = ConversationAgent(
        provider,
        search=lambda ctx, criteria: search_properties_for_criteria(ctx, runner, criteria),
        availability=_availability,
        upcoming=lambda ctx, **kw: upcoming_event(ctx, runner, **kw),
        kb_lookup=_kb_lookup,
        price_read=_price_read,
        last_interest=_last_interest,
        stage_id_for_name=_stage_id_for_name,
        bindings=_signal_bindings,
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
    settings_cmd = UpdateAgentSettingsCommand(dict(conn_kwargs))
    prompt_cmd = CreateAgentPromptVersionCommand(dict(conn_kwargs))
    kb_create_cmd = CreateKbArticleCommand(dict(conn_kwargs))
    kb_update_cmd = UpdateKbArticleCommand(dict(conn_kwargs))
    kb_deactivate_cmd = DeactivateKbArticleCommand(dict(conn_kwargs))
    calendar_cmd = UpdateCalendarConfigCommand(dict(conn_kwargs))
    allowlist_cmd = UpdateFieldAllowlistCommand(dict(conn_kwargs))
    custom_field_create_cmd = CreateCustomFieldDefinitionCommand(dict(conn_kwargs))
    custom_field_update_cmd = UpdateCustomFieldDefinitionCommand(dict(conn_kwargs))
    custom_field_delete_cmd = DeleteCustomFieldDefinitionCommand(dict(conn_kwargs))
    custom_field_set_cmd = SetLeadCustomFieldValueCommand(dict(conn_kwargs))
    add_tag_cmd = AddContactTagCommand(dict(conn_kwargs))
    remove_tag_cmd = RemoveContactTagCommand(dict(conn_kwargs))
    signal_binding_create_cmd = CreateSignalBindingCommand(dict(conn_kwargs))
    signal_binding_delete_cmd = DeleteSignalBindingCommand(dict(conn_kwargs))

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
        # WU16 console overlay: settings-driven verdicts via the pure
        # evaluate() overrides (kill-switch precedence lives in evaluate),
        # the active prompt row for composition, the calendar config inside
        # availability, and the allowlist for the transition_lead branch.
        # Every loader falls back to the code constants, so pre-009 bases
        # (including the pinned WU15 base) behave exactly as before.
        settings = await _console_settings_or_default(context, runner)
        allowed_fields = await _console_allowlist_or_default(context, runner)
        prompt_text = await _console_prompt_or_default(context, runner)
        try:
            plan = await agent.analyze(context, body, contact_id=contact_id,
                                       lead_id=lead_id, now=now,
                                       auto_types=settings.auto_types,
                                       calendar_allowed=settings.calendar_allowed,
                                       system_prompt=prompt_text,
                                       allowed_lead_fields=allowed_fields)
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
                contact_id=contact_id, plan=plan, lead_id=lead_id,
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
            # global_auto=FALSE is the master switch: no auto dispatch at
            # all, while the draft still records its policy_decision above.
            # The auto branch dispatches only the policy's auto_execute
            # types, so a narrowed matrix row is honored action by action
            # (a no-op under the seeded/default overlay).
            if (settings.global_auto and agent_mode == "auto"
                    and verdict == "auto"):
                allowed_types = set((plan.get("policy") or {}).get(
                    "auto_execute_types") or [a.type for _, a in indexed])
                auto_indexed = [(i, a) for i, a in indexed
                                if a.type in allowed_types]
            elif (settings.global_auto and agent_mode == "auto"
                    and verdict == "escalate"
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
                    mode_cmd=mode_cmd, transition_cmd=transition_cmd,
                    custom_field_set_cmd=custom_field_set_cmd,
                    allowed_lead_fields=allowed_fields)
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
        # never consults the policy. The field allowlist still gates the
        # transition_lead branch (field-level control, not a policy verdict).
        allowed_fields = await _console_allowlist_or_default(context, runner)
        results = await _dispatch_actions(
            context=context, draft=draft,
            indexed=list(enumerate(draft.proposed_actions)),
            account_id=account_id, follow_up_cmd=follow_up_cmd,
            interest_cmd=interest_cmd, create_event_cmd=create_event_cmd,
            update_event_cmd=update_event_cmd, reply_cmd=reply_cmd,
            mode_cmd=mode_cmd, transition_cmd=transition_cmd,
            custom_field_set_cmd=custom_field_set_cmd,
            allowed_lead_fields=allowed_fields)

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

    # --- Agent Console (WU16, additive; state/resume above unchanged) ---

    @router.get(f"{AGENT_PREFIX}/settings")
    async def get_console_settings(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        settings = await _console_settings_or_default(context, runner)
        return _console_ok(correlation, "settings", {
            "global_auto": settings.global_auto,
            "calendar_auto_enabled": settings.calendar_allowed,
            "matrix": [{"action_type": action,
                        "verdict": settings.matrix[action]}
                       for action in sorted(settings.matrix)],
            "account_id": context.client_account.id,
        })

    @router.put(f"{AGENT_PREFIX}/settings")
    async def put_console_settings(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _SETTINGS_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await settings_cmd.execute_settings(
                context, global_auto=raw.get("global_auto"),
                calendar_auto_enabled=raw.get("calendar_auto_enabled"),
                matrix=raw.get("matrix"),
                idempotency_key=_console_key(
                    account_id, "settings", raw,
                    {"global_auto": raw.get("global_auto"),
                     "calendar_auto_enabled":
                         raw.get("calendar_auto_enabled"),
                     "matrix": raw.get("matrix")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "settings", outcome.payload,
                           replayed=outcome.replayed)

    @router.get(f"{AGENT_PREFIX}/prompts")
    async def list_prompts(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            rows = await runner.query(
                "SELECT id, version, tone, instructions, composed_snapshot, "
                "is_active, created_at FROM agent_prompts "
                "WHERE client_account_id = %s ORDER BY version",
                (context.client_account.id,),
            )
        except Exception:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        return _console_ok(correlation, "prompts", {
            "count": len(rows),
            "items": [{
                "id": str(row["id"]), "version": row["version"],
                "tone": row["tone"], "instructions": row["instructions"],
                "composed_snapshot": row["composed_snapshot"],
                "is_active": bool(row["is_active"]),
                "created_at": _iso(row["created_at"]),
            } for row in rows],
        })

    @router.post(f"{AGENT_PREFIX}/prompts")
    async def create_prompt(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _PROMPT_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await prompt_cmd.execute_create(
                context, tone=raw.get("tone"),
                instructions=raw.get("instructions"),
                idempotency_key=_console_key(
                    account_id, "prompt", raw,
                    {"tone": raw.get("tone"),
                     "instructions": raw.get("instructions")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "prompt", outcome.payload,
                           replayed=outcome.replayed)

    @router.get(f"{AGENT_PREFIX}/prompts/active")
    async def get_active_prompt(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            rows = await runner.query(
                "SELECT id, version, tone, instructions, composed_snapshot, "
                "created_at FROM agent_prompts "
                "WHERE client_account_id = %s AND is_active "
                "ORDER BY version DESC LIMIT 1",
                (context.client_account.id,),
            )
        except Exception:
            rows = []
        if not rows:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        row = rows[0]
        return _console_ok(correlation, "prompt", {
            "id": str(row["id"]), "version": row["version"],
            "tone": row["tone"], "instructions": row["instructions"],
            "composed_snapshot": row["composed_snapshot"],
            "is_active": True, "created_at": _iso(row["created_at"]),
        })

    @router.get(f"{AGENT_PREFIX}/prompts/composed-preview")
    async def preview_active_prompt(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            rows = await runner.query(
                "SELECT version, tone, instructions FROM agent_prompts "
                "WHERE client_account_id = %s AND is_active "
                "ORDER BY version DESC LIMIT 1",
                (context.client_account.id,),
            )
        except Exception:
            rows = []
        if not rows:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        row = rows[0]
        return _console_ok(correlation, "preview", {
            "version": row["version"],
            "composed_text": compose_prompt(
                row["tone"], row["instructions"] or ""),
        })

    @router.get(f"{AGENT_PREFIX}/prompts/{{version}}")
    async def get_prompt_version(request: Request, version: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            number = int(version)
        except (TypeError, ValueError):
            number = -1
        try:
            rows = await runner.query(
                "SELECT id, version, tone, instructions, composed_snapshot, "
                "is_active, created_at FROM agent_prompts "
                "WHERE client_account_id = %s AND version = %s",
                (context.client_account.id, number),
            )
        except Exception:
            rows = []
        if not rows:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        row = rows[0]
        return _console_ok(correlation, "prompt", {
            "id": str(row["id"]), "version": row["version"],
            "tone": row["tone"], "instructions": row["instructions"],
            "composed_snapshot": row["composed_snapshot"],
            "is_active": bool(row["is_active"]),
            "created_at": _iso(row["created_at"]),
        })

    @router.post(f"{AGENT_PREFIX}/prompts/{{version}}/revert")
    async def revert_prompt(request: Request, version: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _REVERT_KEYS)
        if error is not None:
            return error
        try:
            number = int(version)
        except (TypeError, ValueError):
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        account_id = context.client_account.id
        try:
            outcome = await prompt_cmd.execute_revert(
                context, version=number,
                idempotency_key=_console_key(
                    account_id, "prompt", raw,
                    {"revert_from": number}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "prompt", outcome.payload,
                           replayed=outcome.replayed)

    @router.get(f"{AGENT_PREFIX}/kb/articles")
    async def list_kb_articles(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        category = request.query_params.get("category")
        if category is not None and category not in KB_CATEGORIES:
            return envelope(400, "invalid_command",
                            "category is invalid", correlation)
        try:
            if category is None:
                rows = await runner.query(
                    "SELECT id, category, question, keywords, answer, "
                    "deactivated_at, created_at FROM knowledge_base_articles "
                    "WHERE client_account_id = %s ORDER BY created_at, id",
                    (context.client_account.id,),
                )
            else:
                rows = await runner.query(
                    "SELECT id, category, question, keywords, answer, "
                    "deactivated_at, created_at FROM knowledge_base_articles "
                    "WHERE client_account_id = %s AND category = %s "
                    "ORDER BY created_at, id",
                    (context.client_account.id, category),
                )
        except Exception:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        return _console_ok(correlation, "articles", {
            "count": len(rows),
            "items": [{
                "id": str(row["id"]), "category": row["category"],
                "question": row["question"],
                "keywords": list(row["keywords"] or []),
                "answer": row["answer"],
                "deactivated_at": _iso(row["deactivated_at"]),
                "created_at": _iso(row["created_at"]),
            } for row in rows],
        })

    @router.post(f"{AGENT_PREFIX}/kb/articles")
    async def create_kb_article(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _KB_WRITE_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await kb_create_cmd.execute_create(
                context, category=raw.get("category"),
                question=raw.get("question"), keywords=raw.get("keywords"),
                answer=raw.get("answer"),
                idempotency_key=_console_key(
                    account_id, "kb", raw,
                    {"category": raw.get("category"),
                     "question": raw.get("question"),
                     "keywords": raw.get("keywords"),
                     "answer": raw.get("answer")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "article", outcome.payload,
                           replayed=outcome.replayed)

    @router.put(f"{AGENT_PREFIX}/kb/articles/{{article_id}}")
    async def update_kb_article(request: Request, article_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _KB_WRITE_KEYS)
        if error is not None:
            return error
        if _uuid_or_none(article_id) is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        account_id = context.client_account.id
        try:
            outcome = await kb_update_cmd.execute_update(
                context, article_id=str(article_id),
                category=raw.get("category"), question=raw.get("question"),
                keywords=raw.get("keywords"), answer=raw.get("answer"),
                idempotency_key=_console_key(
                    account_id, "kb", raw,
                    {"article_id": str(article_id),
                     "category": raw.get("category"),
                     "question": raw.get("question"),
                     "keywords": raw.get("keywords"),
                     "answer": raw.get("answer")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "article", outcome.payload,
                           replayed=outcome.replayed)

    @router.delete(f"{AGENT_PREFIX}/kb/articles/{{article_id}}")
    async def deactivate_kb_article(request: Request, article_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation,
                                         _KB_DELETE_KEYS)
        if error is not None:
            return error
        if _uuid_or_none(article_id) is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        account_id = context.client_account.id
        try:
            outcome = await kb_deactivate_cmd.execute_deactivate(
                context, article_id=str(article_id),
                idempotency_key=_console_key(
                    account_id, "kb", raw,
                    {"deactivate": str(article_id)}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "article", outcome.payload,
                           replayed=outcome.replayed)

    @router.post(f"{AGENT_PREFIX}/kb/self-test")
    async def kb_self_test(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _SELFTEST_KEYS)
        if error is not None:
            return error
        question = raw.get("question")
        if (not isinstance(question, str) or not question.strip()
                or len(question) > 4000):
            return envelope(400, "invalid_command",
                            "question must be a non-empty string",
                            correlation)
        question = question.strip()
        try:
            match = await kb_repo.find_best_match(context, question)
        except Exception:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        if match is None:
            return _console_ok(correlation, "probe", {"match": False})
        try:
            rows = await runner.query(
                "SELECT keywords FROM knowledge_base_articles "
                "WHERE id = %s AND client_account_id = %s",
                (match["id"], context.client_account.id),
            )
        except Exception:
            rows = []
        keywords = list(rows[0]["keywords"] or []) if rows else []
        return _console_ok(correlation, "probe", {
            "match": True,
            "article": {"id": match["id"], "category": match["category"],
                        "question": match["question"]},
            "hits": score_question(keywords, normalize_tokens(question)),
        })

    @router.get(f"{AGENT_PREFIX}/calendar-config")
    async def get_calendar_config(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            config = await load_calendar_config(context, runner)
        except Exception:
            config = default_calendar_config()
        return _console_ok(correlation, "calendar_config", {
            "window_start": f"{config.start_hour:02d}:00",
            "window_end": f"{config.end_hour:02d}:00",
            "slot_minutes": config.slot_minutes,
            "working_days": sorted(config.working_days),
            "destination_label": config.destination_label,
            "timezone": config.tz_name,
            "account_id": context.client_account.id,
        })

    @router.put(f"{AGENT_PREFIX}/calendar-config")
    async def put_calendar_config(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _CALENDAR_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await calendar_cmd.execute_update(
                context, window_start=raw.get("window_start"),
                window_end=raw.get("window_end"),
                slot_minutes=raw.get("slot_minutes"),
                working_days=raw.get("working_days"),
                **({"destination_label": raw.get("destination_label")}
                   if "destination_label" in raw else {}),
                **({"timezone": raw.get("timezone")}
                   if "timezone" in raw else {}),
                idempotency_key=_console_key(
                    account_id, "calendar", raw,
                    {"window_start": raw.get("window_start"),
                     "window_end": raw.get("window_end"),
                     "slot_minutes": raw.get("slot_minutes"),
                     "working_days": raw.get("working_days"),
                     "destination_label": raw.get("destination_label"),
                     "timezone": raw.get("timezone")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "calendar_config", outcome.payload,
                           replayed=outcome.replayed)

    @router.get(f"{AGENT_PREFIX}/lead-fields")
    async def get_lead_fields(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        allowed = await _console_allowlist_or_default(context, runner)
        return _console_ok(correlation, "allowlist", {
            "allowed": sorted(allowed),
            "account_id": context.client_account.id,
        })

    @router.put(f"{AGENT_PREFIX}/lead-fields")
    async def put_lead_fields(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation,
                                         _ALLOWLIST_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await allowlist_cmd.execute_update(
                context, allowed=raw.get("allowed"),
                idempotency_key=_console_key(
                    account_id, "allowlist", raw,
                    {"allowed": raw.get("allowed")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "allowlist", outcome.payload,
                           replayed=outcome.replayed)

    @router.get(f"{AGENT_PREFIX}/custom-fields")
    async def list_custom_fields(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            definitions = await load_custom_field_definitions(context, runner)
        except Exception:
            definitions = []
        return _console_ok(correlation, "custom_fields", {
            "count": len(definitions),
            "items": [{"id": d["id"], "name": d["name"], "type": d["type"],
                       "choices": d["choices"]} for d in definitions],
        })

    @router.post(f"{AGENT_PREFIX}/custom-fields")
    async def create_custom_field(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _CUSTOM_FIELD_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await custom_field_create_cmd.execute_define(
                context, name=raw.get("name"), type=raw.get("type"),
                choices=raw.get("choices"),
                idempotency_key=_console_key(
                    account_id, "custom-field", raw,
                    {"name": raw.get("name"), "type": raw.get("type"),
                     "choices": raw.get("choices")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "custom_field", outcome.payload,
                           replayed=outcome.replayed)

    @router.put(f"{AGENT_PREFIX}/custom-fields/{{field_id}}")
    async def update_custom_field(request: Request, field_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation, _CUSTOM_FIELD_KEYS)
        if error is not None:
            return error
        if _uuid_or_none(field_id) is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        account_id = context.client_account.id
        try:
            outcome = await custom_field_update_cmd.execute_update(
                context, field_id=str(field_id), name=raw.get("name"),
                type=raw.get("type"), choices=raw.get("choices"),
                idempotency_key=_console_key(
                    account_id, "custom-field", raw,
                    {"field_id": str(field_id), "name": raw.get("name"),
                     "type": raw.get("type"), "choices": raw.get("choices")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "custom_field", outcome.payload,
                           replayed=outcome.replayed)

    @router.delete(f"{AGENT_PREFIX}/custom-fields/{{field_id}}")
    async def delete_custom_field(request: Request, field_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation,
                                         _CUSTOM_FIELD_DELETE_KEYS)
        if error is not None:
            return error
        if _uuid_or_none(field_id) is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        account_id = context.client_account.id
        try:
            outcome = await custom_field_delete_cmd.execute_delete(
                context, field_id=str(field_id),
                idempotency_key=_console_key(
                    account_id, "custom-field", raw,
                    {"delete": str(field_id)}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "custom_field", outcome.payload,
                           replayed=outcome.replayed)

    @router.get(f"{AGENT_PREFIX}/leads/{{lead_id}}/custom-values")
    async def get_lead_custom_values(request: Request, lead_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        if _uuid_or_none(lead_id) is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        rows = await runner.query(
            "SELECT id FROM leads WHERE id = %s AND client_account_id = %s",
            (lead_id, context.client_account.id))
        if not rows:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        values = await load_custom_field_values(context, runner, lead_id)
        return _console_ok(correlation, "custom_values", {
            "lead_id": lead_id,
            "values": [{"field_id": v["field_id"], "name": v["name"],
                        "type": v["type"], "value": v["value"]} for v in values],
        })

    @router.put(f"{AGENT_PREFIX}/leads/{{lead_id}}/custom-values")
    async def put_lead_custom_values(request: Request, lead_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        if _uuid_or_none(lead_id) is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        raw, error = await _console_body(request, correlation, _CUSTOM_VALUES_KEYS)
        if error is not None:
            return error
        values = raw.get("values")
        if not isinstance(values, dict):
            return envelope(400, "invalid_command",
                            "values must be an object", correlation)
        account_id = context.client_account.id
        try:
            definitions = await load_custom_field_definitions(context, runner)
        except Exception:
            definitions = []
        by_name = {d["name"]: d for d in definitions}
        unknown = sorted(name for name in values if name not in by_name)
        if unknown:
            return envelope(400, "invalid_command",
                            f"undefined custom field: {unknown}", correlation)
        for name, value in values.items():
            field_id = by_name[name]["id"]
            try:
                await custom_field_set_cmd.execute_set(
                    context, lead_id=lead_id, field_id=field_id, value=value,
                    idempotency_key=_custom_value_key(
                        account_id, raw, lead_id, field_id, value))
            except CrmCommandDenied as problem:
                return _denial(problem, correlation)
            except CrmCommandValidationError as problem:
                return envelope(400, "invalid_command", str(problem), correlation)
            except IdempotencyConflict:
                return envelope(409, "tenant_idempotency_conflict",
                                "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        stored = await load_custom_field_values(context, runner, lead_id)
        return _console_ok(correlation, "custom_values", {
            "lead_id": lead_id,
            "values": [{"field_id": v["field_id"], "name": v["name"],
                        "type": v["type"], "value": v["value"]} for v in stored],
        })

    @router.get(f"{AGENT_PREFIX}/contacts")
    async def list_contacts_by_tag_route(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        tag = request.query_params.get("tag")
        if tag is None or not tag.strip():
            return envelope(400, "invalid_command",
                            "tag query parameter is required", correlation)
        try:
            contacts = await list_contacts_by_tag(context, runner, tag.strip())
        except Exception:
            contacts = []
        return _console_ok(correlation, "contacts", {
            "count": len(contacts),
            "items": [{"id": c["id"], "name": c["name"], "tags": c["tags"]}
                      for c in contacts],
        })

    @router.post(f"{AGENT_PREFIX}/contacts/{{contact_id}}/tags")
    async def add_contact_tag(request: Request, contact_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        if _uuid_or_none(contact_id) is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        raw, error = await _console_body(request, correlation, _TAGS_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await add_tag_cmd.execute_add(
                context, contact_id=str(contact_id), tag=raw.get("tag"),
                idempotency_key=_console_key(
                    account_id, "contact-tag", raw,
                    {"contact_id": str(contact_id), "tag": raw.get("tag")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "tags", {
            "contact_id": str(contact_id), "tags": outcome.payload["tags"]},
            replayed=outcome.replayed)

    @router.delete(f"{AGENT_PREFIX}/contacts/{{contact_id}}/tags")
    async def remove_contact_tag(request: Request, contact_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        if _uuid_or_none(contact_id) is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        raw, error = await _console_body(request, correlation, _TAGS_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await remove_tag_cmd.execute_remove(
                context, contact_id=str(contact_id), tag=raw.get("tag"),
                idempotency_key=_console_key(
                    account_id, "contact-tag", raw,
                    {"contact_id": str(contact_id), "tag": raw.get("tag")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "tags", {
            "contact_id": str(contact_id), "tags": outcome.payload["tags"]},
            replayed=outcome.replayed)

    # --- Agent signal bindings (WU18, additive; "Fuentes del agente") ---

    @router.get(f"{AGENT_PREFIX}/signal-bindings")
    async def list_signal_bindings(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        try:
            bound = await load_signal_bindings(context, runner)
        except Exception:
            bound = {}
        try:
            definitions = await load_custom_field_definitions(context, runner)
        except Exception:
            definitions = []
        by_id = {str(d["id"]): d for d in definitions if d.get("id")}
        items = []
        for signal_key in sorted(bound):
            field_id = str(bound[signal_key])
            definition = by_id.get(field_id)
            if definition is None:
                continue
            items.append({"signal_key": signal_key,
                          "field_definition_id": field_id,
                          "field_name": definition["name"],
                          "field_type": definition["type"]})
        return _console_ok(correlation, "signal_bindings", {
            "count": len(items), "items": items,
        })

    @router.post(f"{AGENT_PREFIX}/signal-bindings")
    async def create_signal_binding(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _console_body(request, correlation,
                                         _SIGNAL_BINDING_KEYS)
        if error is not None:
            return error
        signal_key = raw.get("signal_key")
        if not isinstance(signal_key, str) or not signal_key.strip():
            return envelope(400, "invalid_command",
                            "signal_key must be a non-empty string",
                            correlation)
        signal_key = signal_key.strip()
        if signal_key not in SIGNAL_KEYS:
            return envelope(400, "invalid_command",
                            f"signal_key is invalid: {signal_key}",
                            correlation)
        account_id = context.client_account.id
        try:
            outcome = await signal_binding_create_cmd.execute_create(
                context, signal_key=signal_key,
                field_definition_id=raw.get("field_definition_id"),
                idempotency_key=_console_key(
                    account_id, f"signal-binding:{signal_key}", raw,
                    {"signal_key": signal_key,
                     "field_definition_id":
                         raw.get("field_definition_id")}))
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        try:
            definitions = await load_custom_field_definitions(context, runner)
        except Exception:
            definitions = []
        definition = next(
            (d for d in definitions
             if str(d.get("id")) == outcome.payload["field_definition_id"]),
            None)
        if definition is None:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope",
                            correlation)
        return _console_ok(correlation, "signal_binding", {
            "signal_key": outcome.payload["signal_key"],
            "field_definition_id": outcome.payload["field_definition_id"],
            "field_name": definition["name"],
            "field_type": definition["type"],
        }, replayed=outcome.replayed)

    @router.delete(f"{AGENT_PREFIX}/signal-bindings/{{signal_key}}")
    async def delete_signal_binding(request: Request, signal_key: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        if signal_key not in SIGNAL_KEYS:
            return envelope(400, "invalid_command",
                            f"signal_key is invalid: {signal_key}",
                            correlation)
        account_id = context.client_account.id
        try:
            outcome = await signal_binding_delete_cmd.execute_delete(
                context, signal_key=signal_key,
                idempotency_key=f"console:{account_id}:signal-binding:"
                                f"{signal_key}")
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, account_id)
        return _console_ok(correlation, "signal_binding", {
            "signal_key": outcome.payload["signal_key"], "deleted": True,
        }, replayed=outcome.replayed)

    async def _kapso_agent_flow(context: Any, event: NormalizedInboundEvent):
        """Run the same analyze/auto saga as the HTTP route for one conversation."""
        account_id = context.client_account.id
        if event.client_account_id != account_id:
            raise TenantAccessDenied()
        conversation_id = event.conversation_id
        contact_id = await conversation_contact(context, runner, conversation_id)
        if contact_id is None:
            raise CrmCommandDenied("conversation_not_found_in_scope")
        lead_id = await lead_for_contact(context, runner, contact_id)
        settings = await _console_settings_or_default(context, runner)
        allowed_fields = await _console_allowlist_or_default(context, runner)
        prompt_text = await _console_prompt_or_default(context, runner)
        plan = await agent.analyze(
            context, event.body, contact_id=contact_id, lead_id=lead_id,
            now=datetime.now(timezone.utc), auto_types=settings.auto_types,
            calendar_allowed=settings.calendar_allowed,
            system_prompt=prompt_text, allowed_lead_fields=allowed_fields)
        outcome = await analyze_cmd.execute_analyze(
            context, body=event.body, conversation_id=conversation_id,
            contact_id=contact_id, plan=plan, lead_id=lead_id,
            message_id=event.message_id, idempotency_key=f"kapso-agent:{event.event_id}")
        if outcome.replayed:
            return outcome
        draft = await drafts.get(context, outcome.payload["resource_id"])
        if draft is None:
            raise CrmCommandDenied("draft_not_found_in_scope")
        draft = await _enrich_draft(runner, account_id, draft)
        mode_rows = await runner.query(
            "SELECT agent_mode FROM conversations WHERE id = %s AND client_account_id = %s",
            (conversation_id, account_id))
        agent_mode = mode_rows[0]["agent_mode"] if mode_rows else "manual"
        verdict = (plan.get("policy") or {}).get("verdict")
        indexed = list(enumerate(draft.proposed_actions))
        auto_indexed = None
        if settings.global_auto and agent_mode == "auto" and verdict == "auto":
            allowed_types = set((plan.get("policy") or {}).get(
                "auto_execute_types") or [a.type for _, a in indexed])
            auto_indexed = [(i, a) for i, a in indexed if a.type in allowed_types]
        elif (settings.global_auto and agent_mode == "auto" and verdict == "escalate"
              and draft.intent == "handoff"):
            auto_indexed = [(i, a) for i, a in indexed if a.type in ESCALATION_MECHANICS]
        if auto_indexed is None:
            return outcome
        await decide_cmd.execute_decision(
            context, draft_id=draft.id, decision="auto",
            idempotency_key=f"agent-decide:{draft.id}")
        results = await _dispatch_actions(
            context=context, draft=draft, indexed=auto_indexed,
            account_id=account_id, follow_up_cmd=follow_up_cmd,
            interest_cmd=interest_cmd, create_event_cmd=create_event_cmd,
            update_event_cmd=update_event_cmd, reply_cmd=reply_cmd,
            mode_cmd=mode_cmd, transition_cmd=transition_cmd,
            custom_field_set_cmd=custom_field_set_cmd,
            allowed_lead_fields=allowed_fields)
        await finalize_cmd.execute_finalize(
            context, draft_id=draft.id, execution_result=results,
            idempotency_key=f"agent-finalize:{draft.id}")
        return outcome

    router.kapso_orchestrator = KapsoAgentOrchestrator(_kapso_agent_flow)
    return router
