"""Kapso Meta-compatible outbound WhatsApp adapter.

HTTP delivery is deliberately outside command transactions. The outbox event
is the durable hand-off and its message id is reused as callback data on every
retry.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

import httpx

from api.application.audit_outbox_ports import OutboxConsumer, OutboxEvent

DEFAULT_KAPSO_API_BASE_URL = "https://api.kapso.ai/meta/whatsapp/v24.0"


class KapsoDeliveryClass(str, Enum):
    SUCCESS = "success"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class KapsoResponse:
    status_code: int
    body: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class KapsoSendResult:
    classification: KapsoDeliveryClass
    status_code: int


@dataclass(frozen=True)
class KapsoOutboundConfig:
    api_key: str
    base_url: str = DEFAULT_KAPSO_API_BASE_URL
    timeout_seconds: float = 10.0

    @classmethod
    def from_environment(cls) -> "KapsoOutboundConfig":
        api_key = os.getenv("KAPSO_API_KEY", "").strip()
        if not api_key:
            raise ValueError("KAPSO_API_KEY is required")
        base_url = os.getenv("KAPSO_API_BASE_URL", DEFAULT_KAPSO_API_BASE_URL).strip().rstrip("/")
        if not base_url:
            raise ValueError("KAPSO_API_BASE_URL must not be empty")
        try:
            timeout = float(os.getenv("KAPSO_API_TIMEOUT_SECONDS", "10"))
        except ValueError as error:
            raise ValueError("KAPSO_API_TIMEOUT_SECONDS must be a number") from error
        if timeout <= 0:
            raise ValueError("KAPSO_API_TIMEOUT_SECONDS must be positive")
        return cls(api_key=api_key, base_url=base_url, timeout_seconds=timeout)


class KapsoHttpTransport(Protocol):
    async def post(self, url: str, *, headers: Mapping[str, str],
                   json_body: Mapping[str, Any], timeout: float) -> KapsoResponse: ...


class HttpxKapsoTransport:
    async def post(self, url: str, *, headers: Mapping[str, str],
                   json_body: Mapping[str, Any], timeout: float) -> KapsoResponse:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=dict(headers), json=json_body)
        except httpx.HTTPError as error:
            raise KapsoTransportError("Kapso transport failed") from error
        try:
            body = response.json()
        except ValueError:
            body = None
        return KapsoResponse(response.status_code, body if isinstance(body, dict) else None)


class FakeKapsoTransport:
    """Deterministic transport for tests; it never opens a network connection."""

    def __init__(self, responses: list[KapsoResponse] | None = None):
        self.responses = list(responses or [KapsoResponse(200, {})])
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, *, headers: Mapping[str, str],
                   json_body: Mapping[str, Any], timeout: float) -> KapsoResponse:
        self.requests.append({"url": url, "headers": dict(headers), "json": dict(json_body),
                              "timeout": timeout})
        return self.responses.pop(0) if self.responses else KapsoResponse(200, {})


class KapsoTransportError(RuntimeError):
    """Network/transport errors are retryable by the outbox dispatcher."""


class KapsoOutboundClient:
    def __init__(self, config: KapsoOutboundConfig, transport: KapsoHttpTransport | None = None):
        self._config = config
        self._transport = transport or HttpxKapsoTransport()

    @staticmethod
    def text_payload(to: str, body: str, callback_reference: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        if callback_reference:
            payload["biz_opaque_callback_data"] = callback_reference
        return payload

    async def send_text(self, *, phone_number_id: str, to: str, body: str,
                        callback_reference: str) -> KapsoSendResult:
        if not phone_number_id.strip() or not to.strip() or not body.strip():
            raise ValueError("phone_number_id, to and body are required")
        response = await self._transport.post(
            f"{self._config.base_url}/{phone_number_id}/messages",
            headers={"X-API-Key": self._config.api_key, "Content-Type": "application/json"},
            json_body=self.text_payload(to, body, callback_reference),
            timeout=self._config.timeout_seconds,
        )
        if 200 <= response.status_code < 300:
            classification = KapsoDeliveryClass.SUCCESS
        elif response.status_code == 429 or response.status_code >= 500:
            classification = KapsoDeliveryClass.RETRYABLE
        else:
            classification = KapsoDeliveryClass.PERMANENT
        return KapsoSendResult(classification, response.status_code)


class KapsoDestinationError(RuntimeError):
    pass


@dataclass(frozen=True)
class KapsoDestination:
    phone_number_id: str
    phone: str


class KapsoPhoneNumberMapping:
    def __init__(self, mapping: Mapping[str, str]):
        self._mapping = dict(mapping)

    @classmethod
    def from_environment(cls) -> "KapsoPhoneNumberMapping":
        raw = os.getenv("KAPSO_PHONE_ACCOUNT_MAP", "{}")
        try:
            mapping = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("KAPSO_PHONE_ACCOUNT_MAP must be JSON") from error
        if not isinstance(mapping, dict) or any(
            not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
            for k, v in mapping.items()
        ):
            raise ValueError("KAPSO_PHONE_ACCOUNT_MAP must map strings to strings")
        return cls(mapping)

    def phone_number_id_for(self, account_id: str) -> str:
        matches = [phone_id for phone_id, mapped_account in self._mapping.items()
                   if mapped_account == account_id]
        if len(matches) != 1:
            raise KapsoDestinationError("phone_number_id is not uniquely configured for account")
        return matches[0]


class PostgresKapsoDestinationResolver:
    def __init__(self, runner: Any, phone_mapping: KapsoPhoneNumberMapping):
        self._runner = runner
        self._phone_mapping = phone_mapping

    async def resolve(self, event: OutboxEvent) -> KapsoDestination:
        resource_id = event.payload.get("resource_id")
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise KapsoDestinationError("outbound message resource_id is missing")
        rows = await self._runner.query(
            "SELECT c.phone FROM messages m "
            "JOIN conversations v ON v.id = m.conversation_id "
            "JOIN contacts c ON c.id = v.contact_id "
            "WHERE m.id = %s AND m.client_account_id = %s "
            "AND v.client_account_id = m.client_account_id "
            "AND c.client_account_id = m.client_account_id",
            (resource_id, event.client_account_id),
        )
        phone = rows[0].get("phone") if rows else None
        if not isinstance(phone, str) or not phone.strip():
            raise KapsoDestinationError("trusted contact phone is missing")
        return KapsoDestination(self._phone_mapping.phone_number_id_for(event.client_account_id), phone.strip())

    async def body_for(self, event: OutboxEvent) -> str | None:
        resource_id = event.payload.get("resource_id")
        if not isinstance(resource_id, str) or not resource_id.strip():
            return None
        rows = await self._runner.query(
            "SELECT body FROM messages WHERE id = %s AND client_account_id = %s "
            "AND direction = 'outbound'",
            (resource_id, event.client_account_id),
        )
        return rows[0].get("body") if rows else None


class KapsoOutboundConsumer(OutboxConsumer):
    def __init__(self, client: KapsoOutboundClient, resolver: Any):
        self._client = client
        self._resolver = resolver

    async def consume(self, event: OutboxEvent) -> None:
        if event.event_type != "AgentReplyPersisted":
            return
        if event.payload.get("direction") != "outbound":
            raise KapsoDestinationError("outbound reply payload has invalid direction")
        destination = await self._resolver.resolve(event)
        result = await self._client.send_text(
            phone_number_id=destination.phone_number_id,
            to=destination.phone,
            body=await self._body(event),
            callback_reference=str(event.payload.get("resource_id") or event.id),
        )
        if result.classification != KapsoDeliveryClass.SUCCESS:
            raise KapsoDeliveryError(result)

    async def _body(self, event: OutboxEvent) -> str:
        body = event.payload.get("body")
        if isinstance(body, str) and body.strip():
            return body
        loader = getattr(self._resolver, "body_for", None)
        if loader is None:
            raise KapsoDestinationError("outbound reply body is absent from payload")
        body = await loader(event)
        if not isinstance(body, str) or not body.strip():
            raise KapsoDestinationError("trusted outbound reply body is missing")
        return body


class KapsoDeliveryError(RuntimeError):
    def __init__(self, result: KapsoSendResult):
        super().__init__(f"Kapso delivery {result.classification.value}: HTTP {result.status_code}")
        self.result = result


def build_kapso_outbound_consumer(runner: Any, *,
                                  config: KapsoOutboundConfig | None = None,
                                  phone_mapping: KapsoPhoneNumberMapping | None = None,
                                  transport: KapsoHttpTransport | None = None
                                  ) -> KapsoOutboundConsumer:
    """Build the consumer passed to the existing ``OutboxDispatcher`` seam."""
    resolved_config = config or KapsoOutboundConfig.from_environment()
    resolved_mapping = phone_mapping or KapsoPhoneNumberMapping.from_environment()
    return KapsoOutboundConsumer(
        KapsoOutboundClient(resolved_config, transport),
        PostgresKapsoDestinationResolver(runner, resolved_mapping),
    )
