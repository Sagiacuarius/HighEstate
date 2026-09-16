"""Policy verdict-table units (change: agent-autonomy, WU15, no DB, no network).

Pins the design's auto/escalate table through `evaluate`: FAQ/price/search
hits and slot-found calendar intents go auto; unresolved asks, no-match
searches, gated intents, and every handoff escalate (mechanics only); the
AUTO_EVENT_CREATION kill-switch re-gates calendar writes.
"""

from __future__ import annotations

import api.application.agent_policy as agent_policy
from api.application.agent_policy import (
    ESCALATION_MECHANICS,
    PolicyDecision,
    PolicySignals,
)


def _sig(**overrides) -> PolicySignals:
    base = {"intent": "general"}
    base.update(overrides)
    return PolicySignals(**base)


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    def check(name: str, signals: PolicySignals, actions: list[str], *,
              verdict: str, reason: str = "",
              auto_has: frozenset = frozenset(),
              auto_lacks: frozenset = frozenset(),
              esc_is: frozenset | None = None) -> None:
        first = agent_policy.evaluate(signals, actions)
        again = agent_policy.evaluate(signals, actions)
        ok = (isinstance(first, PolicyDecision)
              and first.verdict == verdict
              and (not reason or first.reason == reason)
              and auto_has <= first.auto_execute_types
              and not (auto_lacks & first.auto_execute_types)
              and (esc_is is None or first.escalation_execute_types == esc_is)
              and first == again)  # deterministic
        record(name, ok,
               f"verdict={first.verdict} reason={first.reason} "
               f"auto={sorted(first.auto_execute_types)} esc={sorted(first.escalation_execute_types)}")

    faq = ["send_reply"]
    search = ["register_interest", "create_follow_up", "send_reply"]
    search_t = search + ["transition_lead"]
    visit = ["create_event", "create_follow_up"]
    resched = ["reschedule_event", "create_follow_up"]
    handoff = ["handoff_to_human", "create_follow_up", "send_reply"]

    check("P1 faq kb hit -> auto (reply dispatched)",
          _sig(intent="ask_question", kb_match=True), faq,
          verdict="auto", reason="faq_kb_hit", auto_has=frozenset(faq))
    check("P2 price resolved -> auto (reply dispatched)",
          _sig(intent="ask_question", price_resolved=True), faq,
          verdict="auto", reason="price_resolved", auto_has=frozenset(faq))
    check("P3 ask unresolved -> escalate, nothing dispatched",
          _sig(intent="ask_question"), ["create_follow_up"],
          verdict="escalate", reason="ask_unresolved", esc_is=frozenset())
    check("P4 search with match -> auto (all effects)",
          _sig(intent="search_properties", matched_count=3), search,
          verdict="auto", reason="search_match",
          auto_has=frozenset(search))
    check("P5 search+stage -> auto with transition on the auto side",
          _sig(intent="search_properties", matched_count=2,
               transition_stage_id="stage-1"), search_t,
          verdict="auto", reason="search_match_with_transition",
          auto_has=frozenset(search_t))
    check("P6 search match, transition unresolved -> auto w/o transition",
          _sig(intent="search_properties", matched_count=2), search_t,
          verdict="auto", reason="search_match",
          auto_has=frozenset(search), auto_lacks=frozenset({"transition_lead"}))
    check("P7 search no match -> escalate, nothing dispatched",
          _sig(intent="search_properties", matched_count=0), ["create_follow_up"],
          verdict="escalate", reason="search_no_match", esc_is=frozenset())
    check("P8 schedule_visit + slot -> auto (calendar included)",
          _sig(intent="schedule_visit", slot_found=True), visit,
          verdict="auto", reason="visit_slot_found",
          auto_has=frozenset({"create_event"}))
    check("P9 reschedule + slot -> auto (calendar included)",
          _sig(intent="reschedule", slot_found=True), resched,
          verdict="auto", reason="reschedule_slot_found",
          auto_has=frozenset({"reschedule_event"}))
    check("P10 schedule_visit no slot -> escalate",
          _sig(intent="schedule_visit", slot_found=False), visit,
          verdict="escalate", reason="visit_no_slot", esc_is=frozenset())
    check("P11 update_preferences -> escalate",
          _sig(intent="update_preferences"), ["create_follow_up"],
          verdict="escalate", reason="preferences_gated", esc_is=frozenset())
    check("P12 general -> escalate",
          _sig(intent="general"), ["create_follow_up"],
          verdict="escalate", reason="general_gated", esc_is=frozenset())
    check("P13 handoff requested -> escalate, mechanics only (no reply)",
          _sig(intent="handoff", handoff_requested=True), handoff,
          verdict="escalate", reason="handoff_requested",
          esc_is=frozenset({"handoff_to_human", "create_follow_up"}))
    check("P14 complaint -> escalate, mechanics only (no reply)",
          _sig(intent="general", complaint=True), handoff,
          verdict="escalate", reason="complaint_escalation",
          esc_is=frozenset({"handoff_to_human", "create_follow_up"}))
    record("P15 ESCALATION_MECHANICS pins the handoff pair",
           ESCALATION_MECHANICS == frozenset({"handoff_to_human", "create_follow_up"}),
           f"mechanics={sorted(ESCALATION_MECHANICS)}")

    # P16. Kill-switch pin: flipping AUTO_EVENT_CREATION re-gates calendar.
    saved = agent_policy.AUTO_EVENT_CREATION
    try:
        agent_policy.AUTO_EVENT_CREATION = False
        gated_visit = agent_policy.evaluate(
            _sig(intent="schedule_visit", slot_found=True), visit)
        gated_resched = agent_policy.evaluate(
            _sig(intent="reschedule", slot_found=True), resched)
        ok = (gated_visit.verdict == "escalate"
              and gated_visit.reason == "calendar_gated_kill_switch"
              and gated_resched.verdict == "escalate"
              and gated_resched.reason == "calendar_gated_kill_switch")
        record("P16 kill-switch False re-gates calendar to escalate", ok,
               f"visit={gated_visit.verdict}/{gated_visit.reason} "
               f"resched={gated_resched.verdict}/{gated_resched.reason}")
    finally:
        agent_policy.AUTO_EVENT_CREATION = saved
    record("P17 kill-switch restored (calendar auto again)",
           agent_policy.AUTO_EVENT_CREATION is True
           and agent_policy.evaluate(
               _sig(intent="schedule_visit", slot_found=True), visit).verdict == "auto",
           f"AUTO_EVENT_CREATION={agent_policy.AUTO_EVENT_CREATION}")

    return [r for r in results if r is not None]


def test_agent_policy():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT-POLICY WU15 UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
