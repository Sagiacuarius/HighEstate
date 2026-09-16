"""ConversationAgent pure-orchestration unit tests (WU14, no DB, no network).

Injected fakes make the deterministic decisions fully testable: slot honoring,
search/action mapping, reschedule resolution, and the fail-closed behavior when
a preferred slot is occupied. Proves the agent never performs a write (it has no
DB handle — only the injected provider + three read callables).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from api.application.conversation_agent import ConversationAgent
from api.domain.calendar_event import CalendarEvent


class FakeModel:
    name = "stub"
    model = "fake-v1"

    def __init__(self, response):
        self._response = response

    async def generate(self, system, user):
        return self._response


def _now():
    return datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)


def _slot(day, hour):
    start = datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)
    return (start, start + timedelta(hours=1))


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name, ok, detail=""):
        results.append((name, ok, str(detail)))

    async def _search(ctx, criteria):
        return ["P1", "P2"]

    async def _avail(ctx, **kw):
        return [_slot(22, 12), _slot(23, 12)]

    async def _upcoming_none(ctx, **kw):
        return None

    async def _upcoming_event(ctx, **kw):
        return CalendarEvent(id="E1", client_account_id="A", title="Visita",
                             starts_at=_slot(22, 12)[0], ends_at=_slot(22, 12)[1])

    # U1. search_properties -> register_interest(top) + create_follow_up.
    ag = ConversationAgent(FakeModel({"intent": "search_properties",
                                      "criteria": {"operation_type": "rent"},
                                      "reply_text": "x"}),
                           search=_search, availability=_avail, upcoming=_upcoming_none)
    plan = asyncio.run(ag.analyze(None, "quiero alquilar", contact_id="C1", lead_id=None, now=_now()))
    types = [a["type"] for a in plan["proposed_actions"]]
    interest = next((a for a in plan["proposed_actions"] if a["type"] == "register_interest"), None)
    ok = (plan["matched_property_ids"] == ["P1", "P2"] and types[0] == "register_interest"
          and interest["params"]["property_id"] == "P1"
          and interest["params"]["origin"] == "ai_agent"
          and "create_follow_up" in types and plan["model_provider"] == "stub")
    record("U1 search -> interest(top)+follow_up", ok, str(types))

    # U2. schedule_visit with a free slot -> create_event + follow_up.
    ag2 = ConversationAgent(FakeModel({"intent": "schedule_visit", "criteria": {},
                                       "reply_text": "x"}),
                            search=_search, availability=_avail, upcoming=_upcoming_none)
    plan2 = asyncio.run(ag2.analyze(None, "visita", contact_id="C1", lead_id="L1", now=_now()))
    ce = next((a for a in plan2["proposed_actions"] if a["type"] == "create_event"), None)
    ok = ce is not None and ce["params"]["starts_at"].startswith("2026-09-22") and ce["params"]["contact_id"] == "C1"
    record("U2 schedule_visit -> create_event at free slot", ok, str(ce))

    # U3. schedule_visit with NO slots -> follow-up only (no invented event).
    async def _none_avail(ctx, **kw):
        return []
    ag3 = ConversationAgent(FakeModel({"intent": "schedule_visit", "criteria": {},
                                       "reply_text": "x"}),
                            search=_search, availability=_none_avail, upcoming=_upcoming_none)
    plan3 = asyncio.run(ag3.analyze(None, "visita", contact_id="C1", lead_id=None, now=_now()))
    types3 = [a["type"] for a in plan3["proposed_actions"]]
    record("U3 no availability -> follow_up only (never invents a slot)",
           types3 == ["create_follow_up"], str(types3))

    # U4. reschedule: event resolved + free slot -> reschedule_event(E1).
    ag4 = ConversationAgent(FakeModel({"intent": "reschedule", "criteria": {}, "reply_text": "x"}),
                            search=_search, availability=_avail, upcoming=_upcoming_event)
    plan4 = asyncio.run(ag4.analyze(None, "reprogramar visita", contact_id="C1", lead_id=None, now=_now()))
    re_ = next((a for a in plan4["proposed_actions"] if a["type"] == "reschedule_event"), None)
    record("U4 reschedule -> reschedule_event(resolved id)",
           re_ is not None and re_["params"]["event_id"] == "E1", str(re_))

    # U5. preferred_time honored: BA slots 09:00 (12Z) and 11:00 (14Z);
    # request says 10:00 -> first slot at/after it is 11:00 (14:00Z).
    async def _avail_multi(ctx, **kw):
        return [_slot(22, 12), _slot(22, 14)]
    ag5 = ConversationAgent(FakeModel({"intent": "schedule_visit",
                                       "criteria": {"preferred_date": "2026-09-22",
                                                    "preferred_time": "10:00"},
                                       "reply_text": "x"}),
                            search=_search, availability=_avail_multi, upcoming=_upcoming_none)
    plan5 = asyncio.run(ag5.analyze(None, "visita", contact_id="C1", lead_id=None, now=_now()))
    ce5 = next(a for a in plan5["proposed_actions"] if a["type"] == "create_event")
    record("U5 preferred_time picks first at/after match",
           ce5["params"]["starts_at"] == "2026-09-22T14:00:00.000Z",
           ce5["params"]["starts_at"])

    # U6. preferred day fully booked (outside offered) -> follow_up only.
    ag6 = ConversationAgent(FakeModel({"intent": "schedule_visit",
                                       "criteria": {"preferred_date": "2026-09-30"},
                                       "reply_text": "x"}),
                            search=_search, availability=_avail, upcoming=_upcoming_none)
    plan6 = asyncio.run(ag6.analyze(None, "visita", contact_id="C1", lead_id=None, now=_now()))
    types6 = [a["type"] for a in plan6["proposed_actions"]]
    record("U6 preferred day with no offered slot -> follow_up only",
           types6 == ["create_follow_up"], str(types6))

    return results


def test_conversation_agent():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"CONVERSATION-AGENT WU14 UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
