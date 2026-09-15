"""CRM commercial domain entities (MVP Commercial Demo WU3).

A dedicated vertical module: the accepted tenant_resource table is NOT reused.
Entities mirror the accepted WU1 contracts in
src/application/commercial-demo.ts (branded-ID constructor semantics) and map
one-to-one to the migration-004 tables.

Invariants enforced here (pure, before any SQL):
- every entity carries client_account_id derived by the caller from the
  server-owned TenantContext, never from payload claims (adapters re-check);
- identifiers follow the WU1 constructor rules: non-empty, no surrounding
  whitespace, bounded length, [A-Za-z0-9][A-Za-z0-9:_-]* (UUIDs match);
- follow-ups require a lead or contact target, and terminal follow-ups are
  never rescheduled by an in-memory mutation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

MAX_ID_LENGTH = 256
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]*$")

CONTACT_STATUSES = frozenset({"active", "archived"})
LEAD_STATUSES = frozenset({"new", "qualified", "won", "lost"})
OPERATION_TYPES = frozenset({"sale", "rent"})
PROPERTY_AVAILABILITIES = frozenset({"available", "reserved", "unavailable"})
FOLLOWUP_STATUSES = frozenset({"pending", "completed", "cancelled"})
TERMINAL_FOLLOWUP_STATUSES = frozenset({"completed", "cancelled"})
MESSAGE_DIRECTIONS = frozenset({"inbound", "outbound"})
CONVERSATION_STATUSES = frozenset({"open", "closed", "archived"})


class CrmDomainError(ValueError):
    """Raised when an entity violates a pure CRM invariant."""


def validate_crm_id(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise CrmDomainError(f"{label} must be a string")
    if value != value.strip() or not (1 <= len(value) <= MAX_ID_LENGTH):
        raise CrmDomainError(f"{label} is invalid")
    if not _ID_PATTERN.match(value):
        raise CrmDomainError(f"{label} is invalid")
    return value


def _validate_choice(value: str, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise CrmDomainError(f"{label} must be one of {sorted(allowed)}")
    return value


def _validate_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CrmDomainError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True)
class Contact:
    id: str
    client_account_id: str
    name: str
    email: str | None = None
    phone: str | None = None
    tags: tuple[str, ...] = ()
    status: str = "active"

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "Contact.id")
        validate_crm_id(self.client_account_id, "Contact.client_account_id")
        _validate_text(self.name, "Contact.name")
        _validate_choice(self.status, CONTACT_STATUSES, "Contact.status")


@dataclass(frozen=True)
class Lead:
    id: str
    client_account_id: str
    contact_id: str
    pipeline_id: str
    stage_id: str
    status: str = "new"
    selected_property_id: str | None = None
    source: str = "manual"

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "Lead.id")
        validate_crm_id(self.client_account_id, "Lead.client_account_id")
        validate_crm_id(self.contact_id, "Lead.contact_id")
        validate_crm_id(self.pipeline_id, "Lead.pipeline_id")
        validate_crm_id(self.stage_id, "Lead.stage_id")
        if self.selected_property_id is not None:
            validate_crm_id(self.selected_property_id, "Lead.selected_property_id")
        _validate_choice(self.status, LEAD_STATUSES, "Lead.status")
        _validate_text(self.source, "Lead.source")


@dataclass(frozen=True)
class Property:
    id: str
    client_account_id: str
    title: str
    operation_type: str
    location: str
    bedrooms: int
    price: Decimal
    currency: str = "USD"
    availability: str = "available"

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "Property.id")
        validate_crm_id(self.client_account_id, "Property.client_account_id")
        _validate_text(self.title, "Property.title")
        _validate_choice(self.operation_type, OPERATION_TYPES, "Property.operation_type")
        _validate_text(self.location, "Property.location")
        if not isinstance(self.bedrooms, int) or self.bedrooms < 0:
            raise CrmDomainError("Property.bedrooms must be >= 0")
        if not isinstance(self.price, Decimal) or self.price < 0:
            raise CrmDomainError("Property.price must be a non-negative Decimal")
        _validate_text(self.currency, "Property.currency")
        _validate_choice(self.availability, PROPERTY_AVAILABILITIES, "Property.availability")


@dataclass(frozen=True)
class Stage:
    id: str
    pipeline_id: str
    client_account_id: str
    name: str
    position: int

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "Stage.id")
        validate_crm_id(self.pipeline_id, "Stage.pipeline_id")
        validate_crm_id(self.client_account_id, "Stage.client_account_id")
        _validate_text(self.name, "Stage.name")
        if not isinstance(self.position, int) or self.position < 0:
            raise CrmDomainError("Stage.position must be >= 0")


@dataclass(frozen=True)
class Pipeline:
    id: str
    client_account_id: str
    name: str
    stages: tuple[Stage, ...] = ()

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "Pipeline.id")
        validate_crm_id(self.client_account_id, "Pipeline.client_account_id")
        _validate_text(self.name, "Pipeline.name")
        for stage in self.stages:
            if stage.pipeline_id != self.id:
                raise CrmDomainError("Pipeline contains a Stage from another Pipeline")
            if stage.client_account_id != self.client_account_id:
                raise CrmDomainError("Pipeline contains a Stage from another ClientAccount")


@dataclass(frozen=True)
class Conversation:
    id: str
    client_account_id: str
    contact_id: str
    unread_count: int = 0
    status: str = "open"

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "Conversation.id")
        validate_crm_id(self.client_account_id, "Conversation.client_account_id")
        validate_crm_id(self.contact_id, "Conversation.contact_id")
        if not isinstance(self.unread_count, int) or self.unread_count < 0:
            raise CrmDomainError("Conversation.unread_count must be >= 0")
        _validate_choice(self.status, CONVERSATION_STATUSES, "Conversation.status")


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    client_account_id: str
    direction: str
    body: str
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "Message.id")
        validate_crm_id(self.conversation_id, "Message.conversation_id")
        validate_crm_id(self.client_account_id, "Message.client_account_id")
        _validate_choice(self.direction, MESSAGE_DIRECTIONS, "Message.direction")
        _validate_text(self.body, "Message.body")


@dataclass(frozen=True)
class FollowUp:
    id: str
    client_account_id: str
    title: str
    due_at: datetime
    lead_id: str | None = None
    contact_id: str | None = None
    status: str = "pending"

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "FollowUp.id")
        validate_crm_id(self.client_account_id, "FollowUp.client_account_id")
        _validate_text(self.title, "FollowUp.title")
        if not isinstance(self.due_at, datetime):
            raise CrmDomainError("FollowUp.due_at must be a datetime")
        if self.lead_id is None and self.contact_id is None:
            raise CrmDomainError("FollowUp requires a lead_id or contact_id target")
        if self.lead_id is not None:
            validate_crm_id(self.lead_id, "FollowUp.lead_id")
        if self.contact_id is not None:
            validate_crm_id(self.contact_id, "FollowUp.contact_id")
        _validate_choice(self.status, FOLLOWUP_STATUSES, "FollowUp.status")


def reschedule_follow_up(follow_up: FollowUp, new_due_at: datetime) -> FollowUp:
    """Terminal follow-ups are never rescheduled (WU2/design invariant)."""
    if follow_up.status in TERMINAL_FOLLOWUP_STATUSES:
        raise CrmDomainError("completed/cancelled follow-ups cannot be rescheduled")
    return replace(follow_up, due_at=new_due_at)
