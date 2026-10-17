"""Kapso v2 inbound webhook adapter.

This module owns only transport verification, provider parsing, and the
phone-number-to-account configuration seam. CRM persistence remains behind the
provider-neutral inbound command.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from api.inbound_boundary import NormalizedInboundEvent

KAPSO_EVENT = "whatsapp.message.received"


class KapsoPayloadInvalid(ValueError):
    """The signed payload is not a supported, safe Kapso message."""


class KapsoTenantMapping(Protocol):
    def account_for(self, phone_number_id: str) -> str | None: ...


class InMemoryKapsoTenantMapping:
    def __init__(self, mapping: Mapping[str, str] | None = None):
        self._mapping = dict(mapping or {})

    def account_for(self, phone_number_id: str) -> str | None:
        return self._mapping.get(phone_number_id)


class EnvKapsoTenantMapping(InMemoryKapsoTenantMapping):
    """Configuration-backed mapping; invalid configuration fails closed."""

    @classmethod
    def from_environment(cls, value: str | None = None) -> "EnvKapsoTenantMapping":
        raw = value if value is not None else os.getenv("KAPSO_PHONE_ACCOUNT_MAP", "{}")
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("KAPSO_PHONE_ACCOUNT_MAP must be JSON") from error
        if not isinstance(parsed, dict) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(account, str) or not account.strip()
            for key, account in parsed.items()
        ):
            raise ValueError("KAPSO_PHONE_ACCOUNT_MAP must map strings to strings")
        return cls(parsed)


@dataclass(frozen=True)
class KapsoInboundMessage:
    event: NormalizedInboundEvent
    external_message_id: str
    sender: str
    phone_number_id: str


def verify_kapso_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not isinstance(signature, str) or not signature or not isinstance(secret, str) or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 256:
        raise KapsoPayloadInvalid(f"{label} is invalid")
    return value


def _timestamp(value: Any) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return _required_string(value, "message.timestamp")


def _text(message: dict[str, Any]) -> str:
    if message.get("type", "text") != "text":
        raise KapsoPayloadInvalid("unsupported message type")
    text = message.get("text")
    if isinstance(text, dict):
        text = text.get("body")
    if not isinstance(text, str) or not text.strip():
        raise KapsoPayloadInvalid("message text is missing")
    return text


def parse_kapso_item(item: Any, *, account_id: str) -> KapsoInboundMessage:
    if not isinstance(item, dict):
        raise KapsoPayloadInvalid("message item is malformed")
    message = item.get("message")
    conversation = item.get("conversation")
    metadata = item.get("metadata")
    if not isinstance(message, dict) or not isinstance(conversation, dict):
        raise KapsoPayloadInvalid("message or conversation is missing")
    metadata = metadata if isinstance(metadata, dict) else {}
    external_id = _required_string(message.get("id") or item.get("message_id"), "message.id")
    conversation_id = _required_string(conversation.get("id"), "conversation.id")
    phone_number_id = _required_string(
        item.get("phone_number_id") or metadata.get("phone_number_id"), "phone_number_id"
    )
    sender = message.get("from") or message.get("sender")
    if isinstance(sender, dict):
        sender = sender.get("phone") or sender.get("bsuid") or sender.get("id")
    sender = _required_string(sender, "message.sender")
    body = _text(message)
    event = NormalizedInboundEvent(
        event_id=external_id,
        client_account_id=_required_string(account_id, "client_account_id"),
        conversation_id=conversation_id,
        message_id=external_id,
        direction="inbound",
        body=body,
        received_at=_timestamp(message.get("timestamp") or item.get("received_at")),
    )
    return KapsoInboundMessage(event, external_id, sender, phone_number_id)


def parse_kapso_payload(payload: Any, *, account_id: str) -> list[KapsoInboundMessage]:
    if not isinstance(payload, dict):
        raise KapsoPayloadInvalid("payload is malformed")
    items = payload.get("data") if payload.get("batch") is True else [payload]
    if not isinstance(items, list) or not items:
        raise KapsoPayloadInvalid("payload data is malformed")
    return [parse_kapso_item(item, account_id=account_id) for item in items]
