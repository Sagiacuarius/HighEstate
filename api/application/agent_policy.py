"""Autonomous policy verdicts (change: agent-autonomy, WU15).

Pure function over validated facts: `evaluate(signals, actions)` maps the
planner's deterministic signals to AUTO (may execute on `auto`
conversations) or ESCALATE (waits for the advisor). No I/O, no model text,
no DB, no clock — deterministic for identical inputs.

Imports nothing beyond `api.domain.agent_draft` (vocabulary pins) plus the
stdlib (`dataclasses`, `typing`). The Phase-5 vocabulary (`handoff` intent,
`send_reply` / `handoff_to_human` action types) does not exist in
`api.domain.agent_draft` yet, so this module defines its own frozen sets for
those names and does NOT extend the domain (Phase 5's job).

Kill-switch (owner decision 2026-09-16): `AUTO_EVENT_CREATION = True` means
calendar writes (`create_event`, `reschedule_event`) auto-execute inside an
otherwise AUTO verdict. Flipping this constant to `False` re-gates them: any
verdict whose planned actions include a gated calendar type becomes ESCALATE
with reason `calendar_gated_kill_switch`, restoring the human approve click.

"Low confidence" is defined as absent deterministic signals only (no KB
hit, no price target, no match, no slot found): this schema carries no
numeric confidence and the policy deliberately adds none.

Bounded reason codes (stable strings, one per rule):
  faq_kb_hit, price_resolved, search_match, search_match_with_transition,
  visit_slot_found, reschedule_slot_found, visit_no_slot,
  reschedule_no_slot, ask_unresolved, search_no_match, preferences_gated,
  general_gated, handoff_requested, complaint_escalation,
  calendar_gated_kill_switch, unhandled_intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from api.domain.agent_draft import INTENTS, PROPOSED_ACTION_TYPES

# Pins against the accepted WU14 vocabulary (imported, never extended here).
DOMAIN_INTENTS = frozenset(INTENTS)
DOMAIN_ACTION_TYPES = frozenset(PROPOSED_ACTION_TYPES)

# Phase-5 vocabulary (absent from the domain today; owned frozen sets only).
HANDOFF_INTENT = "handoff"
SEND_REPLY_ACTION = "send_reply"
HANDOFF_ACTION = "handoff_to_human"
TRANSITION_ACTION = "transition_lead"

# OD-1 default: calendar writes AUTO-include; the whitelist covers grounded
# replies, interest, follow-ups, resolved transitions, calendar writes, and
# (change: agent-custom-field-writes) deterministically extracted custom-field
# values, which ride the intent's existing verdict.
AUTO_ACTION_TYPES = frozenset({
    SEND_REPLY_ACTION,
    "register_interest",
    "create_follow_up",
    TRANSITION_ACTION,
    "create_event",
    "reschedule_event",
    "set_custom_field_value",
})

# Calendar writes are the gated subset: re-gated by the kill-switch below.
GATED_ACTION_TYPES = frozenset({"create_event", "reschedule_event"})

# Handoff escalation runs mechanics only (flag/pause/follow-up); a reply to
# the lead never auto-dispatches.
ESCALATION_MECHANICS = frozenset({HANDOFF_ACTION, "create_follow_up"})

# Owner decision 2026-09-16: True = calendar writes auto-execute.
# False = kill-switch: calendar verdicts escalate back to the human gate.
AUTO_EVENT_CREATION = True


@dataclass(frozen=True)
class PolicySignals:
    intent: str
    kb_match: bool = False
    price_resolved: bool = False
    matched_count: int = 0
    slot_found: bool = False
    handoff_requested: bool = False
    complaint: bool = False
    transition_stage_id: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    verdict: str  # "auto" | "escalate"
    reason: str  # one of the bounded codes documented above
    auto_execute_types: frozenset = frozenset()
    escalation_execute_types: frozenset = frozenset()


def _action_types(actions: Iterable[Any]) -> frozenset:
    """Normalize planned actions to a frozenset of type names.

    Accepts bare type strings, ProposedAction-like objects with a `.type`
    attribute, or dicts with a `"type"` key; anything else is ignored.
    """
    out: set[str] = set()
    for action in actions or ():
        if isinstance(action, str):
            if action:
                out.add(action)
        elif isinstance(action, dict):
            name = action.get("type")
            if isinstance(name, str) and name:
                out.add(name)
        else:
            name = getattr(action, "type", None)
            if isinstance(name, str) and name:
                out.add(name)
    return frozenset(out)


def _effective_auto_types(signals: PolicySignals, *, auto_types: frozenset | None = None,
                          calendar_allowed: bool | None = None) -> frozenset:
    """The AUTO whitelist after kill-switch and transition gating.

    Keyword-only overrides (change: agent-console, WU16) let the console
    overlay replace the module constants per account. None (the default)
    late-binds to the constants at call time, so the accepted kill-switch
    pin (which flips AUTO_EVENT_CREATION) keeps working; explicit values
    always win.
    """
    if auto_types is None:
        auto_types = AUTO_ACTION_TYPES
    if calendar_allowed is None:
        calendar_allowed = AUTO_EVENT_CREATION
    effective = set(auto_types)
    if not calendar_allowed:
        effective -= set(GATED_ACTION_TYPES)
    if not signals.transition_stage_id:
        effective.discard(TRANSITION_ACTION)
    return frozenset(effective)


def _auto(reason: str, planned: frozenset, signals: PolicySignals, *,
          auto_types: frozenset | None = None,
          calendar_allowed: bool | None = None) -> PolicyDecision:
    return PolicyDecision(
        verdict="auto",
        reason=reason,
        auto_execute_types=frozenset(
            t for t in planned
            if t in _effective_auto_types(signals, auto_types=auto_types,
                                          calendar_allowed=calendar_allowed)),
        escalation_execute_types=frozenset(),
    )


def _escalate(reason: str, planned: frozenset, *, mechanics: bool = False) -> PolicyDecision:
    return PolicyDecision(
        verdict="escalate",
        reason=reason,
        auto_execute_types=frozenset(),
        escalation_execute_types=(
            frozenset(t for t in planned if t in ESCALATION_MECHANICS)
            if mechanics else frozenset()
        ),
    )


def evaluate(signals: PolicySignals, actions: Iterable[Any], *,
             auto_types: frozenset | None = None,
             calendar_allowed: bool | None = None) -> PolicyDecision:
    """Deterministic verdict over validated signals + planned action types.

    Keyword-only overrides (change: agent-console, WU16) replace the module
    constants per account; None (the default) late-binds to the constants
    at call time (fallback == constants oracle).
    """
    planned = _action_types(actions)
    if auto_types is None:
        auto_types = AUTO_ACTION_TYPES
    if calendar_allowed is None:
        calendar_allowed = AUTO_EVENT_CREATION
    auto_types = frozenset(auto_types)

    def _auto_here(reason: str) -> PolicyDecision:
        return _auto(reason, planned, signals,
                     auto_types=auto_types, calendar_allowed=calendar_allowed)

    # Handoff dominates everything: escalate, mechanics only, never a reply.
    if signals.intent == HANDOFF_INTENT or signals.handoff_requested:
        return _escalate("handoff_requested", planned, mechanics=True)
    if signals.complaint:
        return _escalate("complaint_escalation", planned, mechanics=True)

    # Calendar intents: a found slot auto-executes (calendar included);
    # without a slot there is nothing to book; the kill-switch re-gates.
    if signals.intent in ("schedule_visit", "reschedule"):
        is_visit = signals.intent == "schedule_visit"
        if not signals.slot_found:
            return _escalate("visit_no_slot" if is_visit else "reschedule_no_slot", planned)
        if not calendar_allowed and (planned & GATED_ACTION_TYPES):
            return _escalate("calendar_gated_kill_switch", planned)
        return _auto_here("visit_slot_found" if is_visit else "reschedule_slot_found")

    # FAQ / price: grounded answers auto-execute; unresolved asks wait.
    if signals.intent == "ask_question":
        if signals.kb_match:
            return _auto_here("faq_kb_hit")
        if signals.price_resolved:
            return _auto_here("price_resolved")
        return _escalate("ask_unresolved", planned)

    # Search: matches auto-execute (transition rides along only when its
    # stage resolved); no match waits for the advisor.
    if signals.intent == "search_properties":
        if signals.matched_count >= 1:
            if signals.transition_stage_id and TRANSITION_ACTION in planned:
                return _auto_here("search_match_with_transition")
            return _auto_here("search_match")
        return _escalate("search_no_match", planned)

    if signals.intent == "update_preferences":
        return _escalate("preferences_gated", planned)
    if signals.intent == "general":
        return _escalate("general_gated", planned)
    return _escalate("unhandled_intent", planned)
