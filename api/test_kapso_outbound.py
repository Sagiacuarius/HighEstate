from __future__ import annotations

import asyncio
import io
import logging

from api.adapters.kapso_outbound import (
    DEFAULT_KAPSO_API_BASE_URL,
    FakeKapsoTransport,
    KapsoDeliveryClass,
    KapsoDestination,
    KapsoDestinationError,
    KapsoOutboundClient,
    KapsoOutboundConfig,
    KapsoOutboundConsumer,
    KapsoResponse,
    KapsoPhoneNumberMapping,
)
from api.application.audit_outbox_ports import OutboxEvent


def event(body="hello", resource_id="message-1"):
    return OutboxEvent("event-1", "AgentReplyPersisted", "message", resource_id,
                       "agency", "account-a", "corr", "key",
                       {"resource_id": resource_id, "direction": "outbound", "body": body})


def client(transport):
    return KapsoOutboundClient(KapsoOutboundConfig("secret-key", timeout_seconds=3), transport)


def test_payload_shape_and_endpoint():
    transport = FakeKapsoTransport()
    result = asyncio.run(client(transport).send_text(
        phone_number_id="phone-id", to="+5411", body="hello", callback_reference="message-1"))
    request = transport.requests[0]
    assert result.classification == KapsoDeliveryClass.SUCCESS
    assert request["url"] == f"{DEFAULT_KAPSO_API_BASE_URL}/phone-id/messages"
    assert request["json"] == {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": "+5411",
        "type": "text", "text": {"body": "hello"},
        "biz_opaque_callback_data": "message-1",
    }
    assert request["headers"]["X-API-Key"] == "secret-key"


def test_no_secret_or_body_logging():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    transport = FakeKapsoTransport()
    try:
        asyncio.run(client(transport).send_text(
            phone_number_id="phone-id", to="+5411", body="private body",
            callback_reference="message-1"))
    finally:
        root.removeHandler(handler)
    assert "secret-key" not in stream.getvalue()
    assert "private body" not in stream.getvalue()


def test_http_status_classification():
    cases = ((200, KapsoDeliveryClass.SUCCESS), (204, KapsoDeliveryClass.SUCCESS),
             (429, KapsoDeliveryClass.RETRYABLE), (500, KapsoDeliveryClass.RETRYABLE),
             (503, KapsoDeliveryClass.RETRYABLE), (400, KapsoDeliveryClass.PERMANENT),
             (401, KapsoDeliveryClass.PERMANENT), (404, KapsoDeliveryClass.PERMANENT))
    for status, expected in cases:
        result = asyncio.run(client(FakeKapsoTransport([KapsoResponse(status)])).send_text(
            phone_number_id="phone-id", to="+5411", body="hello",
            callback_reference="message-1"))
        assert result.classification == expected


class Resolver:
    async def resolve(self, _event):
        return KapsoDestination("phone-id", "+5411")


def test_consumer_sends_persisted_reply_and_reuses_idempotency_reference():
    transport = FakeKapsoTransport()
    consumer = KapsoOutboundConsumer(client(transport), Resolver())
    asyncio.run(consumer.consume(event()))
    asyncio.run(consumer.consume(event()))
    assert len(transport.requests) == 2
    assert all(r["json"]["biz_opaque_callback_data"] == "message-1"
               for r in transport.requests)


def test_missing_destination_fails_closed_without_http_call():
    class MissingResolver:
        async def resolve(self, _event):
            raise KapsoDestinationError("trusted contact phone is missing")

    transport = FakeKapsoTransport()
    try:
        asyncio.run(KapsoOutboundConsumer(client(transport), MissingResolver()).consume(event()))
    except KapsoDestinationError:
        pass
    else:
        raise AssertionError("missing destination must fail closed")
    assert transport.requests == []


def test_missing_phone_number_configuration_fails_closed():
    try:
        KapsoPhoneNumberMapping({}).phone_number_id_for("account-a")
    except KapsoDestinationError:
        pass
    else:
        raise AssertionError("missing phone_number_id configuration must fail closed")


if __name__ == "__main__":
    tests = [value for name, value in globals().items()
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"KAPSO OUTBOUND SCENARIOS: {len(tests)}/{len(tests)} passed")
