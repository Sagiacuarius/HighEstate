"""ConversationAgent orchestration (WU14).

interpret (injected ModelProvider) → match (injected search) → schedule
(injected availability/upcoming) → propose (deterministic mapping, in here).

The agent imports no SQL, no HTTP, no FastAPI: the model output is validated
against the bounded schema, and every EFFECT is expressed as data
(ProposedAction) that the approval saga later executes through accepted
commands. Slot selection and action mapping are pure and unit-testable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from api.application.agent_persona import build_system_prompt
from api.application.model_provider import validate_interpretation
from api.domain.agent_draft import ProposedAction

SLOT_MINUTES = 60
WINDOW_DAYS = 7


class ConversationAgent:
    def __init__(self, model: Any, *, search: Callable[..., Any],
                 availability: Callable[..., Any], upcoming: Callable[..., Any]):
        self._model = model
        self._search = search      # (context, criteria) -> list[property_id]
        self._availability = availability  # (context, from, to, exclude?) -> [(start,end)]
        self._upcoming = upcoming  # (context, contact_id, now) -> CalendarEvent | None

    async def analyze(self, context, body: str, *, contact_id: str,
                      lead_id: str | None, now: datetime) -> dict[str, Any]:
        """Produce the bounded draft plan for one inbound message body.

        Raises InterpretationError/ProviderError — the caller persists nothing.
        """
        raw = await self._model.generate(build_system_prompt(), body)
        interp = validate_interpretation(raw)
        intent = interp["intent"]
        criteria = interp["criteria"]

        matched: list[str] = []
        if intent == "search_properties":
            matched = await self._search(context, criteria)

        slot: tuple[datetime, datetime] | None = None
        event_id: str | None = None
        if intent in ("schedule_visit", "reschedule"):
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

        actions = self._derive_actions(intent=intent, criteria=criteria,
                                       matched=matched, slot=slot,
                                       reschedule_event_id=event_id,
                                       contact_id=contact_id, lead_id=lead_id,
                                       now=now)
        return {
            "intent": intent,
            "criteria": criteria,
            "matched_property_ids": matched,
            "proposed_actions": [a.to_dict() for a in actions],
            "reply_text": interp["reply_text"],
            "model_provider": getattr(self._model, "name", "unknown"),
            "model_name": getattr(self._model, "model", "unknown"),
        }

    # --- deterministic helpers (pure) ------------------------------------

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
                        lead_id: str | None, now: datetime) -> list[ProposedAction]:
        actions: list[ProposedAction] = []
        if intent == "search_properties":
            if matched:
                actions.append(ProposedAction("register_interest", {
                    "property_id": matched[0], "contact_id": contact_id, "origin": "ai_agent"}))
            title = ("Enviar propiedades al lead" if matched
                     else "Buscar propiedades que calcen (sin coincidencias aún)")
            actions.append(self._follow_up(title, contact_id, lead_id, now))
        elif intent == "schedule_visit":
            if slot is not None:
                actions.append(ProposedAction("create_event", {
                    "title": "Visita solicitada por el lead",
                    "starts_at": self._iso(slot[0]), "ends_at": self._iso(slot[1]),
                    "contact_id": contact_id}))
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
            actions.append(self._follow_up("Responder consulta del lead", contact_id, lead_id, now))
        elif intent == "update_preferences":
            actions.append(self._follow_up("Actualizar preferencias del lead", contact_id, lead_id, now))
        else:  # general
            actions.append(self._follow_up("Dar seguimiento al lead", contact_id, lead_id, now))
        return actions
