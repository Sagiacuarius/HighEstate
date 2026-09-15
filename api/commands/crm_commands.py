"""Pure CRM command intents and canonical hashing (MVP Commercial Demo WU5).

Provider-neutral port layer: no FastAPI, no psycopg. The atomic PostgreSQL
adapters live in api/application/crm_commands_wu5.py. Canonical hashing mirrors
the accepted technical pattern (api/command_http._hash_body): JSON with sorted
keys and tight separators, then sha256 — so byte-identical semantics hash
identically across retries.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_INBOUND_DELAY = timedelta(hours=48)


def canonical_hash(payload: dict[str, Any]) -> str:
    """Deterministic request hash: sorted keys, compact separators, sha256 hex."""
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _default_due_at(now: datetime | None = None) -> str:
    base = now or datetime.now(timezone.utc)
    return (base + DEFAULT_INBOUND_DELAY).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class LeadRef:
    """Snapshot of the lead a transition applies to."""
    lead_id: str
    pipeline_id: str
    current_stage_id: str
    status: str


@dataclass(frozen=True)
class LeadSummary:
    """Minimal lead view used by follow-up effects."""
    lead_id: str
    contact_id: str
    status: str


@dataclass(frozen=True)
class LeadStageTransition:
    lead_id: str
    to_stage_id: str
    new_status: str = "qualified"


@dataclass(frozen=True)
class SaveFollowUp:
    """Follow-up spec produced by an effect (id and persistence decided by the
    command adapter; the domain FollowUp enforces the lead/contact target)."""
    title: str
    due_at: str
    status: str = "pending"


@dataclass(frozen=True)
class CreateFollowUp:
    follow_up_id: str
    title: str
    due_at: datetime
    status: str = "pending"
    lead_id: str | None = None
    contact_id: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("follow-up title is required")
        if self.lead_id is None and self.contact_id is None:
            raise ValueError("follow-up requires a lead or contact target")


@dataclass(frozen=True)
class CompleteFollowUp:
    follow_up_id: str


@dataclass(frozen=True)
class ApplyLeadStageTransition:
    """Client-facing intent alias for the explicit transition command."""
    lead_id: str
    to_stage_id: str
    new_status: str = "qualified"


@dataclass(frozen=True)
class ProcessInboundMessage:
    """Inbound command input: the normalized event plus optional due hint."""
    event: Any
    follow_up_due_at: str | None = None


class InboundCrmEffects:
    """Provider-neutral deterministic effects: inbound -> stage advance + follow-up.

    No LLM, no automation builder: the demo rule is fixed (advance to the next
    declared stage in position order, schedule one follow-up). It is a plain
    default port, swappable in composition.
    """

    def __init__(self, follow_up_prefix: str = "Seguimiento entrante",
                 due_at_factory=_default_due_at):
        self._prefix = follow_up_prefix
        self._due_at_factory = due_at_factory

    def stage_transition(self, lead: LeadRef,
                         ordered_stage_ids: tuple[str, ...]) -> LeadStageTransition | None:
        """Next declared stage in position order; None when already terminal."""
        try:
            index = ordered_stage_ids.index(lead.current_stage_id)
        except ValueError:
            return None
        if index + 1 >= len(ordered_stage_ids):
            return None
        return LeadStageTransition(lead_id=lead.lead_id,
                                   to_stage_id=ordered_stage_ids[index + 1],
                                   new_status="qualified")

    def follow_up(self, lead: LeadSummary) -> SaveFollowUp:
        return SaveFollowUp(title=f"{self._prefix} ({lead.lead_id[:8]})",
                            due_at=self._due_at_factory(), status="pending")


__all__ = [
    "ApplyLeadStageTransition",
    "CompleteFollowUp",
    "CreateFollowUp",
    "InboundCrmEffects",
    "LeadRef",
    "LeadStageTransition",
    "LeadSummary",
    "ProcessInboundMessage",
    "SaveFollowUp",
    "canonical_hash",
]
