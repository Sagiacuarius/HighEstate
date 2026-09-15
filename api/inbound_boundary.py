"""Provider-neutral normalized inbound boundary (MVP Commercial Demo WU4).

Mirrors the accepted WU1 contracts (src/application/commercial-demo.ts):
parseInboundMessageEvent + InboundMessageHandler. Simulated dev messages only
— no Kapso, no WhatsApp, no LLM.

Security rules:
- the payload clientAccountId is a claim, never authority; the FastAPI route
  resolves the TenantContext server-side and rejects any event whose scope (or
  whose embedded conversation) does not belong to the authorized account;
- denials are non-enumerating: foreign and missing share one safe outcome;
- nothing is recorded when validation or scope checks fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from api.adapters.postgres_core import CrossTenantAccessDenied
from api.tenant_boundary import TenantContext

ID_MAX_LENGTH = 256
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]*$")
_MESSAGE_ALLOWED_KEYS = frozenset(
    {"id", "conversationId", "clientAccountId", "direction", "body", "receivedAt"}
)
_EVENT_ALLOWED_KEYS = frozenset(
    {"eventId", "clientAccountId", "conversationId", "message"}
)


class InboundPayloadInvalid(Exception):
    """400-class: shape/identifier/body/timestamp problem."""


class InboundScopeDenied(Exception):
    """403-class, non-enumerating: account/conversation scope mismatch."""


@dataclass(frozen=True)
class NormalizedInboundEvent:
    event_id: str
    client_account_id: str
    conversation_id: str
    message_id: str
    direction: str
    body: str
    received_at: str  # canonical ISO-8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "clientAccountId": self.client_account_id,
            "conversationId": self.conversation_id,
            "message": {
                "id": self.message_id,
                "conversationId": self.conversation_id,
                "clientAccountId": self.client_account_id,
                "direction": self.direction,
                "body": self.body,
                "receivedAt": self.received_at,
            },
        }


def _validate_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not (1 <= len(value) <= ID_MAX_LENGTH):
        raise InboundPayloadInvalid(f"{label} is invalid")
    if not _ID_PATTERN.match(value):
        raise InboundPayloadInvalid(f"{label} is invalid")
    return value


def _normalize_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InboundPayloadInvalid("receivedAt is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InboundPayloadInvalid("receivedAt is invalid") from error
    if parsed.tzinfo is None:
        raise InboundPayloadInvalid("receivedAt must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_inbound_message_event(payload: Any) -> NormalizedInboundEvent:
    """Python mirror of parseInboundMessageEvent: fully validated, normalized,
    freshly constructed (the original payload object is never returned)."""
    if not isinstance(payload, dict):
        raise InboundPayloadInvalid("malformed inbound message payload")
    extra_event_keys = set(payload.keys()) - _EVENT_ALLOWED_KEYS
    if extra_event_keys:
        raise InboundPayloadInvalid(f"unsupported keys: {sorted(extra_event_keys)}")
    event_id = _validate_id(payload.get("eventId"), "eventId")
    client_account_id = _validate_id(payload.get("clientAccountId"), "clientAccountId")
    conversation_id = _validate_id(payload.get("conversationId"), "conversationId")
    message = payload.get("message")
    if not isinstance(message, dict):
        raise InboundPayloadInvalid("malformed inbound message payload")
    nested_keys = set(message.keys()) - _MESSAGE_ALLOWED_KEYS
    if nested_keys:
        raise InboundPayloadInvalid(f"message contains unsupported keys: {sorted(nested_keys)}")
    message_id = _validate_id(message.get("id"), "message.id")
    message_account = _validate_id(message.get("clientAccountId"), "message.clientAccountId")
    message_conversation = _validate_id(message.get("conversationId"), "message.conversationId")
    direction = message.get("direction")
    if direction != "inbound":
        raise InboundPayloadInvalid("malformed inbound message payload")
    body = message.get("body")
    if not isinstance(body, str) or not body.strip():
        raise InboundPayloadInvalid("malformed inbound message payload")
    # cross-field equality BEFORE anything is accepted (mirrors WU1 contract)
    if message_account != client_account_id or message_conversation != conversation_id:
        raise InboundPayloadInvalid("inbound message scope does not match event scope")
    received_at = _normalize_timestamp(message.get("receivedAt"))
    return NormalizedInboundEvent(
        event_id=event_id,
        client_account_id=client_account_id,
        conversation_id=conversation_id,
        message_id=message_id,
        direction="inbound",
        body=body,
        received_at=received_at,
    )


class InboundMessageHandler(Protocol):
    """Port mirroring the WU1 InboundMessageHandler contract."""

    async def handle(self, context: TenantContext, event: NormalizedInboundEvent) -> None: ...


class DevJournalInboundHandler:
    """Dev-only local handler: re-checks scope independently of the parser,
    verifies the conversation exists inside the authorized account through the
    tenant-scoped repository, and records the event only when everything
    passes. No persistence, no command, no LLM — that arrives with WU5."""

    def __init__(self, conversations: Any):
        self._conversations = conversations
        self.journal: list[NormalizedInboundEvent] = []

    async def handle(self, context: TenantContext, event: NormalizedInboundEvent) -> None:
        account_id = context.client_account.id
        # Independent re-check: the handler never trusts the parser or the route.
        if event.client_account_id != account_id:
            raise InboundScopeDenied("tenant scope does not match")
        try:
            conversation = await self._conversations.get(context, event.conversation_id)
        except CrossTenantAccessDenied:
            # Inconsistent/absent context is one non-enumerating denial.
            raise InboundScopeDenied("tenant scope does not match") from None
        if conversation is None:
            # missing and foreign are one non-enumerating outcome
            raise InboundScopeDenied("tenant scope does not match")
        self.journal.append(event)
