"""ConversationAgent orchestration (WU14 + WU15 autonomy extensions).

interpret (injected ModelProvider) -> match (injected search) -> schedule
(injected availability/upcoming) -> ground (KB lookup / live price read /
stage-name resolution, all injected) -> propose (deterministic mapping, in
here) -> policy verdict (pure `agent_policy.evaluate` over validated facts).

The agent imports no SQL, no HTTP, no FastAPI: the model output is validated
against the bounded schema, and every EFFECT is expressed as data
(ProposedAction) that the approval/auto saga later executes through accepted
commands. Slot selection, grounding and action mapping are pure given the
resolved inputs, and unit-testable with fakes.

Grounding rule: a `send_reply` action is emitted only with a deterministic
ground source (KB article or live catalog fields). The model's free-form
`reply_text` is carried on the plan for the human advisor and is NEVER
auto-sent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from api.application.agent_persona import build_system_prompt
from api.application.agent_policy import PolicySignals, evaluate as evaluate_policy
from api.application.model_provider import validate_interpretation
from api.domain.agent_draft import ProposedAction

SLOT_MINUTES = 60
WINDOW_DAYS = 7

# Body-level handoff triggers (explicit request or complaint signal). The
# model may return intent "handoff" directly; these keywords make the
# escalation deterministic even when it does not (fail-safe direction:
# handoff verdicts escalate with mechanics only, never a reply).
HANDOFF_KEYWORDS = (
    "humano", "humana", "asesor", "asesora", "gerente",
    "persona real", "hablar con", "hablen con",
    "reclamo", "reclam", "queja", "quejas",
    "decepcionad", "decepciono",
)
COMPLAINT_KEYWORDS = (
    "reclamo", "reclam", "queja", "quejas",
    "decepcionad", "decepciono", "insatisfech",
    "estafa", "denuncia", "pesimo", "pésimo",
)
PRICE_SIGNAL_KEYWORDS = (
    "cuánto", "cuanto", "precio", "sale", "salen",
    "cuesta", "cuestan", "valor", "vale", "price",
)
FILTER_KEYS = ("operation_type", "property_type", "city", "budget_max",
               "rooms_min", "bathrooms_min", "parking")

# transition_lead targets, resolved by name inside the lead's pipeline.
QUALIFY_STAGE_NAMES = ("Calificado", "Simulador: visita calificada")
VISIT_STAGE_NAME = "Visita agendada"

REPLY_MAX_CHARS = 2000


def _has_filters(criteria: dict[str, Any]) -> bool:
    for key in FILTER_KEYS:
        value = criteria.get(key)
        if value is None or value is False or value == "":
            continue
        if isinstance(value, bool):
            continue
        return True
    return False


def _is_price_question(body: str) -> bool:
    lowered = body.lower()
    return any(keyword in lowered for keyword in PRICE_SIGNAL_KEYWORDS)


def _handoff_detected(intent: str, body: str) -> bool:
    if intent == "handoff":
        return True
    lowered = body.lower()
    return any(keyword in lowered for keyword in HANDOFF_KEYWORDS)


def _is_complaint(body: str) -> bool:
    lowered = body.lower()
    return any(keyword in lowered for keyword in COMPLAINT_KEYWORDS)


def _bounded_reply(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()[:REPLY_MAX_CHARS]


def _price_reply_text(price: dict[str, Any]) -> str | None:
    title = price.get("title") or "La propiedad"
    amount = price.get("price_amount")
    currency = (price.get("price_currency") or "").strip()
    operation = (price.get("operation_type") or "").strip()
    if amount is None:
        return _bounded_reply(str(title))
    parts = f"{title}: {amount}"
    if currency:
        parts += f" {currency}"
    if operation:
        parts += f" ({operation})"
    return _bounded_reply(parts)


class ConversationAgent:
    def __init__(self, model: Any, *, search: Callable[..., Any],
                 availability: Callable[..., Any], upcoming: Callable[..., Any],
                 kb_lookup: Callable[..., Any] | None = None,
                 price_read: Callable[..., Any] | None = None,
                 last_interest: Callable[..., Any] | None = None,
                 stage_id_for_name: Callable[..., Any] | None = None):
        self._model = model
        self._search = search      # (context, criteria) -> list[property_id]
        self._availability = availability  # (context, from, to, exclude?) -> [(start,end)]
        self._upcoming = upcoming  # (context, contact_id, now) -> CalendarEvent | None
        self._kb_lookup = kb_lookup  # (context, text) -> article dict | None
        self._price_read = price_read  # (context, property_id) -> fields dict | None
        self._last_interest = last_interest  # (context, contact_id) -> property_id | None
        self._stage_id_for_name = stage_id_for_name  # (context, lead_id, name) -> stage_id | None

    async def analyze(self, context, body: str, *, contact_id: str,
                      lead_id: str | None, now: datetime,
                      auto_types: frozenset | None = None,
                      calendar_allowed: bool | None = None,
                      system_prompt: str | None = None) -> dict[str, Any]:
        """Produce the bounded draft plan for one inbound message body.

        Raises InterpretationError/ProviderError — the caller persists nothing.
        The returned plan carries `policy` (verdict dict) for persistence and
        for the route's auto/escalate decision.

        Additive console overrides (change: agent-console, WU16):
        `auto_types` / `calendar_allowed` flow straight into the pure
        `evaluate()` overrides (None late-binds to the module constants, so
        bare calls behave exactly as before; no new intents); `system_prompt`
        replaces the composed system text (None/blank falls back to
        `build_system_prompt()` defaults).
        """
        prompt_text = (system_prompt if isinstance(system_prompt, str)
                       and system_prompt.strip() else build_system_prompt())
        raw = await self._model.generate(prompt_text, body)
        interp = validate_interpretation(raw)
        intent = interp["intent"]
        criteria = interp["criteria"]

        handoff = _handoff_detected(intent, body)
        complaint = _is_complaint(body)

        matched: list[str] = []
        if intent == "search_properties" and not handoff:
            try:
                matched = await self._search(context, criteria)
            except Exception:
                matched = []
            if matched is None:
                matched = []

        slot: tuple[datetime, datetime] | None = None
        event_id: str | None = None
        if intent in ("schedule_visit", "reschedule") and not handoff:
            from_dt = now.astimezone(timezone.utc)
            to_dt = from_dt + timedelta(days=WINDOW_DAYS)
            if intent == "reschedule":
                event = await self._upcoming(context, contact_id=contact_id, now_dt=from_dt)
                if event is not None and event.starts_at is not None:
                    event_id = event.id
            slots = await self._availability(context, from_dt=from_dt, to_dt=to_dt,
                                             exclude_event_id=event_id)
            slot = self._pick_slot(slots, criteria.get("preferred_date"),
                                   criteria.get("preferred_time"))

        kb_article: dict[str, Any] | None = None
        if intent == "ask_question" and not handoff and self._kb_lookup is not None:
            kb_article = await self._kb_lookup(context, body)

        price: dict[str, Any] | None = None
        if intent == "ask_question" and not handoff and _is_price_question(body):
            price = await self._resolve_price(context, criteria, contact_id)

        qual_stage_id: str | None = None
        if (intent == "search_properties" and not handoff and matched and lead_id
                and isinstance(criteria.get("city"), str) and criteria["city"].strip()
                and isinstance(criteria.get("budget_max"), (int, float))
                and not isinstance(criteria.get("budget_max"), bool)
                and self._stage_id_for_name is not None):
            for name in QUALIFY_STAGE_NAMES:
                resolved = await self._stage_id_for_name(context, lead_id, name)
                if resolved:
                    qual_stage_id = str(resolved)
                    break

        visit_stage_id: str | None = None
        if (intent == "schedule_visit" and not handoff and slot is not None
                and lead_id and self._stage_id_for_name is not None):
            resolved = await self._stage_id_for_name(context, lead_id, VISIT_STAGE_NAME)
            if resolved:
                visit_stage_id = str(resolved)

        handoff_reason: str | None = None
        if handoff:
            handoff_reason = "complaint" if complaint else "explicit_request"

        actions = self._derive_actions(
            intent=intent, criteria=criteria, matched=matched, slot=slot,
            reschedule_event_id=event_id, contact_id=contact_id, lead_id=lead_id,
            now=now, kb_article=kb_article, price=price,
            qual_stage_id=qual_stage_id, visit_stage_id=visit_stage_id,
            handoff_reason=handoff_reason)

        transition_stage_id = qual_stage_id or visit_stage_id
        signals = PolicySignals(
            intent="handoff" if handoff else intent,
            kb_match=kb_article is not None,
            price_resolved=price is not None,
            matched_count=len(matched),
            slot_found=slot is not None,
            handoff_requested=handoff,
            complaint=complaint,
            transition_stage_id=transition_stage_id,
        )
        decision = evaluate_policy(signals, actions,
                                     auto_types=auto_types,
                                     calendar_allowed=calendar_allowed)
        return {
            "intent": "handoff" if handoff else intent,
            "criteria": criteria,
            "matched_property_ids": matched,
            "proposed_actions": [a.to_dict() for a in actions],
            "reply_text": interp["reply_text"],
            "model_provider": getattr(self._model, "name", "unknown"),
            "model_name": getattr(self._model, "model", "unknown"),
            "policy": {
                "verdict": decision.verdict,
                "reason": decision.reason,
                "auto_execute_types": sorted(decision.auto_execute_types),
                "escalation_execute_types": sorted(decision.escalation_execute_types),
            },
        }

    async def _resolve_price(self, context, criteria: dict[str, Any],
                             contact_id: str) -> dict[str, Any] | None:
        """Price resolution chain: criteria filters -> top criteria match,
        else the newest property_interests row; no target -> None (escalate,
        never guess). The live read re-validates availability."""
        target_id: str | None = None
        if _has_filters(criteria):
            try:
                found = await self._search(context, criteria)
            except Exception:
                found = []
            if found:
                target_id = str(found[0])
        if target_id is None and self._last_interest is not None:
            target_id = await self._last_interest(context, contact_id)
        if target_id is None or self._price_read is None:
            return None
        return await self._price_read(context, target_id)

    # --- deterministic helpers (pure given resolved inputs) -----------------

    @staticmethod
    def _pick_slot(slots: list[tuple[datetime, datetime]], preferred_date: Any,
                   preferred_time: Any) -> tuple[datetime, datetime] | None:
        """Honor preferred_date (local business tz) first; within it prefer an
        exact preferred_time match, then the first slot at/after it; without a
        preferred_date the earliest free slot of the window wins."""
        if not slots:
            return None
        candidates = slots
        from api.domain.calendar_event import BUSINESS_TIMEZONE
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(BUSINESS_TIMEZONE)
        if isinstance(preferred_date, str):
            day = [s for s in slots if s[0].astimezone(tz).date().isoformat() == preferred_date]
            if not day:
                return None  # preferred day fully booked / outside window
            candidates = day
        if isinstance(preferred_time, str):
            exact = [s for s in candidates if s[0].astimezone(tz).strftime("%H:%M") == preferred_time]
            if exact:
                return exact[0]
            after = [s for s in candidates
                     if s[0].astimezone(tz).strftime("%H:%M") >= preferred_time]
            if after:
                return after[0]
            return None
        return candidates[0]

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _follow_up(self, title: str, contact_id: str, lead_id: str | None,
                   now: datetime) -> ProposedAction:
        return ProposedAction("create_follow_up", {
            "title": title, "contact_id": contact_id, "lead_id": lead_id,
            "due_at": self._iso(now + timedelta(hours=24)),
        })

    def _derive_actions(self, *, intent: str, criteria: dict[str, Any],
                        matched: list[str], slot: tuple[datetime, datetime] | None,
                        reschedule_event_id: str | None, contact_id: str,
                        lead_id: str | None, now: datetime,
                        kb_article: dict[str, Any] | None = None,
                        price: dict[str, Any] | None = None,
                        qual_stage_id: str | None = None,
                        visit_stage_id: str | None = None,
                        handoff_reason: str | None = None) -> list[ProposedAction]:
        actions: list[ProposedAction] = []
        if handoff_reason is not None:
            actions.append(ProposedAction("handoff_to_human", {"reason": handoff_reason}))
            actions.append(self._follow_up(
                "Derivar la conversacion con el lead a un asesor humano",
                contact_id, lead_id, now))
            return actions
        if intent == "search_properties":
            if matched:
                actions.append(ProposedAction("register_interest", {
                    "property_id": matched[0], "contact_id": contact_id, "origin": "ai_agent"}))
            if qual_stage_id is not None and lead_id is not None:
                actions.append(ProposedAction("transition_lead", {
                    "lead_id": lead_id, "to_stage_id": qual_stage_id,
                    "new_status": "qualified"}))
            title = ("Enviar propiedades al lead" if matched
                     else "Buscar propiedades que calcen (sin coincidencias aún)")
            actions.append(self._follow_up(title, contact_id, lead_id, now))
        elif intent == "schedule_visit":
            if slot is not None:
                actions.append(ProposedAction("create_event", {
                    "title": "Visita solicitada por el lead",
                    "starts_at": self._iso(slot[0]), "ends_at": self._iso(slot[1]),
                    "contact_id": contact_id}))
                if visit_stage_id is not None and lead_id is not None:
                    actions.append(ProposedAction("transition_lead", {
                        "lead_id": lead_id, "to_stage_id": visit_stage_id,
                        "new_status": "qualified"}))
                actions.append(self._follow_up("Preparar visita", contact_id, lead_id, now))
            else:
                actions.append(self._follow_up(
                    "Coordinar visita (sin disponibilidad en la ventana)",
                    contact_id, lead_id, now))
        elif intent == "reschedule":
            if reschedule_event_id is not None and slot is not None:
                actions.append(ProposedAction("reschedule_event", {
                    "event_id": reschedule_event_id,
                    "starts_at": self._iso(slot[0]), "ends_at": self._iso(slot[1])}))
            elif reschedule_event_id is not None:
                actions.append(self._follow_up(
                    "Reprogramar visita (sin franja libre en la ventana)",
                    contact_id, lead_id, now))
            else:
                actions.append(self._follow_up(
                    "Consultar qué visita quiere reprogramar el lead",
                    contact_id, lead_id, now))
        elif intent == "ask_question":
            if price is not None:
                reply = _price_reply_text(price)
                if reply is not None:
                    actions.append(ProposedAction("send_reply", {
                        "source": "price_catalog",
                        "property_id": str(price.get("id")),
                        "reply_text": reply}))
            elif kb_article is not None:
                reply = _bounded_reply(kb_article.get("answer"))
                if reply is not None:
                    actions.append(ProposedAction("send_reply", {
                        "source": "kb_article",
                        "article_id": str(kb_article.get("id")),
                        "reply_text": reply}))
            actions.append(self._follow_up("Responder consulta del lead", contact_id, lead_id, now))
        elif intent == "update_preferences":
            actions.append(self._follow_up("Actualizar preferencias del lead", contact_id, lead_id, now))
        else:  # general (and any unknown intent — fail soft, follow up only)
            actions.append(self._follow_up("Dar seguimiento al lead", contact_id, lead_id, now))
        return actions
