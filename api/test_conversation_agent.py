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

    # --- WU15 autonomy extensions (grounded planner + policy verdict). ---

    async def _kb_hit(ctx, text):
        return {"id": "KB1", "category": "expensas", "question": "q",
                "answer": "Las expensas cubren los gastos comunes del edificio."}

    async def _kb_miss(ctx, text):
        return None

    async def _price_read(ctx, property_id):
        if property_id == "MISSING":
            return None
        return {"id": property_id, "title": "Depto en Palermo",
                "price_amount": 150000, "price_currency": "USD",
                "operation_type": "sale"}

    async def _interest_px(ctx, contact_id):
        return "PX"

    async def _interest_none(ctx, contact_id):
        return None

    async def _stage_calificado(ctx, lead_id, name):
        return "STAGE-CAL" if name == "Calificado" else None

    async def _stage_visita(ctx, lead_id, name):
        return "STAGE-VIS" if name == "Visita agendada" else None

    async def _stage_none(ctx, lead_id, name):
        return None

    def _agent(response, **kw):
        base = dict(search=_search, availability=_avail, upcoming=_upcoming_none,
                    kb_lookup=_kb_miss, price_read=_price_read,
                    last_interest=_interest_none, stage_id_for_name=_stage_none)
        base.update(kw)
        return ConversationAgent(FakeModel(response), **base)

    # U7. handoff intent -> handoff_to_human{reason} + create_follow_up pair.
    ag7 = _agent({"intent": "handoff", "criteria": {}, "reply_text": "Te derivo."})
    plan7 = asyncio.run(ag7.analyze(
        None, "quiero hablar con un humano", contact_id="C1", lead_id="L1", now=_now()))
    types7 = [a["type"] for a in plan7["proposed_actions"]]
    pair7 = plan7["proposed_actions"]
    ok = (plan7["intent"] == "handoff"
          and types7 == ["handoff_to_human", "create_follow_up"]
          and pair7[0]["params"].get("reason") == "explicit_request"
          and plan7["policy"]["verdict"] == "escalate"
          and sorted(plan7["policy"]["escalation_execute_types"])
          == ["create_follow_up", "handoff_to_human"])
    record("U7 handoff intent emits the handoff pair (escalate+mechanics)", ok, str(types7))

    # U8. complaint body -> reason complaint.
    ag8 = _agent({"intent": "general", "criteria": {}, "reply_text": "x"})
    plan8 = asyncio.run(ag8.analyze(
        None, "estoy decepcionado con el servicio", contact_id="C1",
        lead_id="L1", now=_now()))
    first8 = plan8["proposed_actions"][0]
    ok = (plan8["intent"] == "handoff"
          and first8["type"] == "handoff_to_human"
          and first8["params"].get("reason") == "complaint")
    record("U8 complaint body escalates with reason complaint", ok, str(first8))

    # U9. price via criteria filters -> top match, grounded price_catalog reply.
    async def _search_p9(ctx, criteria):
        return ["P9", "P10"]
    ag9 = _agent({"intent": "ask_question",
                  "criteria": {"city": "Palermo", "budget_max": 200000.0},
                  "reply_text": "sale caro"},
                 search=_search_p9)
    plan9 = asyncio.run(ag9.analyze(
        None, "¿cuánto sale el depto en Palermo?", contact_id="C1",
        lead_id=None, now=_now()))
    types9 = [a["type"] for a in plan9["proposed_actions"]]
    sr9 = next((a for a in plan9["proposed_actions"] if a["type"] == "send_reply"), None)
    ok = (types9 == ["send_reply", "create_follow_up"]
          and sr9["params"].get("source") == "price_catalog"
          and sr9["params"].get("property_id") == "P9"
          and "150000" in sr9["params"].get("reply_text", "")
          and 1 <= len(sr9["params"]["reply_text"]) <= 2000
          and plan9["policy"]["verdict"] == "auto"
          and plan9["policy"]["reason"] == "price_resolved")
    record("U9 price resolves via criteria top match (grounded AUTO)", ok, str(sr9))

    # U10. bare "¿cuánto sale?" -> last-interest fallback.
    ag10 = _agent({"intent": "ask_question", "criteria": {}, "reply_text": "x"},
                  last_interest=_interest_px)
    plan10 = asyncio.run(ag10.analyze(
        None, "¿cuánto sale?", contact_id="C1", lead_id=None, now=_now()))
    sr10 = next((a for a in plan10["proposed_actions"] if a["type"] == "send_reply"), None)
    ok = (sr10 is not None and sr10["params"].get("property_id") == "PX"
          and plan10["policy"]["verdict"] == "auto")
    record("U10 bare price question resolves via last-interest fallback", ok, str(sr10))

    # U11. no criteria match, no interest -> escalate, never guess.
    ag11 = _agent({"intent": "ask_question", "criteria": {}, "reply_text": "x"})
    plan11 = asyncio.run(ag11.analyze(
        None, "¿cuánto sale?", contact_id="C1", lead_id=None, now=_now()))
    types11 = [a["type"] for a in plan11["proposed_actions"]]
    ok = (types11 == ["create_follow_up"]
          and plan11["policy"]["verdict"] == "escalate"
          and plan11["policy"]["reason"] == "ask_unresolved")
    record("U11 unresolvable price escalates (never guesses)", ok, str(types11))

    # U12. KB hit -> grounded kb_article reply, model text never auto-sent.
    ag12 = _agent({"intent": "ask_question", "criteria": {},
                   "reply_text": "MODELTEXT-UNIQUE-7"},
                  kb_lookup=_kb_hit)
    plan12 = asyncio.run(ag12.analyze(
        None, "¿las expensas incluyen el abl?", contact_id="C1",
        lead_id=None, now=_now()))
    sr12 = next((a for a in plan12["proposed_actions"] if a["type"] == "send_reply"), None)
    bodies = [a["params"].get("reply_text", "") for a in plan12["proposed_actions"]]
    ok = (sr12 is not None and sr12["params"].get("source") == "kb_article"
          and sr12["params"].get("article_id") == "KB1"
          and all("MODELTEXT-UNIQUE-7" not in b for b in bodies)
          and plan12["policy"]["verdict"] == "auto"
          and plan12["policy"]["reason"] == "faq_kb_hit")
    record("U12 KB hit grounds send_reply; model text never an action", ok, str(sr12))

    # U13. search with city+budget+lead -> transition_lead when resolvable.
    async def _search_city(ctx, criteria):
        return ["PC"]
    ag13 = _agent({"intent": "search_properties",
                   "criteria": {"city": "Palermo", "budget_max": 200000.0},
                   "reply_text": "x"},
                  search=_search_city, stage_id_for_name=_stage_calificado)
    plan13 = asyncio.run(ag13.analyze(
        None, "busco depto en Palermo hasta 200000", contact_id="C1",
        lead_id="L1", now=_now()))
    tr13 = next((a for a in plan13["proposed_actions"]
                 if a["type"] == "transition_lead"), None)
    ok = (tr13 is not None and tr13["params"].get("to_stage_id") == "STAGE-CAL"
          and tr13["params"].get("lead_id") == "L1"
          and plan13["policy"]["reason"] == "search_match_with_transition")
    record("U13 search+city+budget emits transition_lead (Calificado)", ok, str(tr13))

    # U14. unresolvable stage name -> no transition action (never a guessed id).
    ag14 = _agent({"intent": "search_properties",
                   "criteria": {"city": "Palermo", "budget_max": 200000.0},
                   "reply_text": "x"},
                  search=_search_city)
    plan14 = asyncio.run(ag14.analyze(
        None, "busco depto en Palermo hasta 200000", contact_id="C1",
        lead_id="L1", now=_now()))
    types14 = [a["type"] for a in plan14["proposed_actions"]]
    ok = ("transition_lead" not in types14
          and plan14["policy"]["reason"] == "search_match")
    record("U14 unresolvable stage -> no transition action", ok, str(types14))

    # U15. schedule_visit + slot + lead -> Visita agendada when it exists.
    ag15 = _agent({"intent": "schedule_visit", "criteria": {}, "reply_text": "x"},
                  stage_id_for_name=_stage_visita)
    plan15 = asyncio.run(ag15.analyze(
        None, "quiero una visita", contact_id="C1", lead_id="L9", now=_now()))
    tr15 = next((a for a in plan15["proposed_actions"]
                 if a["type"] == "transition_lead"), None)
    ok = (tr15 is not None and tr15["params"].get("to_stage_id") == "STAGE-VIS"
          and plan15["policy"]["verdict"] == "auto")
    record("U15 schedule+slot emits Visita agendada transition", ok, str(tr15))

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
