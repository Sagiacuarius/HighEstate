"""Agent draft domain (change: llm-conversational-agent, WU14).

The bounded vocabulary the agent operates within: intents the model may return,
draft lifecycle, and the deterministic action types the approval saga may
dispatch. Everything outside these enums fails validation before persistence —
the model never free-forms an action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

INTENTS = ("search_properties", "schedule_visit", "reschedule",
           "ask_question", "update_preferences", "general")

DRAFT_STATUSES = ("proposed", "approved", "rejected", "executed")

# Approval dispatch maps these to the accepted WU5/WU11/WU13 commands.
PROPOSED_ACTION_TYPES = ("create_follow_up", "register_interest",
                         "create_event", "reschedule_event", "transition_lead")

CRITERIA_KEYS = ("operation_type", "property_type", "city", "budget_max",
                 "rooms_min", "bathrooms_min", "parking", "preferred_date",
                 "preferred_time")


@dataclass(frozen=True)
class ProposedAction:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "params": dict(self.params)}


@dataclass(frozen=True)
class AgentDraft:
    id: str
    client_account_id: str
    conversation_id: str
    message_id: str | None
    contact_id: str
    lead_id: str | None
    status: str
    intent: str
    criteria: dict[str, Any]
    matched_property_ids: tuple[str, ...]
    proposed_actions: tuple[ProposedAction, ...]
    reply_text: str | None
    model_provider: str
    model_name: str
    created_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    execution_result: tuple[dict[str, Any], ...] | None = None
