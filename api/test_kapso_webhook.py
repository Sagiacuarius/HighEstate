from __future__ import annotations

import hashlib
import hmac
import json
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.adapters.kapso_inbound import (
    InMemoryKapsoTenantMapping,
    parse_kapso_payload,
    verify_kapso_signature,
)
from api.routes.kapso_webhook import KAPSO_ROUTE, build_kapso_router
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    Membership,
    InMemoryIdentityAdapter,
    InMemoryTenantRepository,
    TenantAccessDenied,
    TenantAuthenticationRequired,
    TenantContext,
    TenantContextResolver,
    User,
)

ACCOUNT = "account-a"
PHONE = "phone-a"
SECRET = "test-secret"


class Command:
    def __init__(self):
        self.calls = []
        self.keys = set()

    async def execute_event(self, context, *, event, idempotency_key):
        replayed = idempotency_key in self.keys
        self.keys.add(idempotency_key)
        self.calls.append((context.client_account.id, event, idempotency_key))
        return type("Outcome", (), {"replayed": replayed})()


class Resolver:
    async def resolve(self, request, account_id):
        raise TenantAuthenticationRequired()

    async def resolve_service(self, account_id, correlation_id):
        return TenantContext(User("service:tenant-webhook", "Tenant Webhook Service"),
                             Agency("agency", "agency"),
                             ClientAccount(account_id, "agency", account_id),
                             Membership("service:tenant-webhook", "agency", account_id,
                                        frozenset({"client_account:write"})), correlation_id)


def payload(message_id="m-1", body="hello", phone=PHONE):
    return {"message": {"id": message_id, "from": "+5411", "type": "text",
                         "text": {"body": body}, "timestamp": "2026-10-17T12:00:00Z"},
            "conversation": {"id": "conversation-a"}, "phone_number_id": phone}


def signed(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def app(command=None):
    application = FastAPI()
    application.state.demo_composition = type("Composition", (), {"resolver": Resolver()})()
    router = build_kapso_router({}, InMemoryKapsoTenantMapping({PHONE: ACCOUNT}), SECRET, command=command)
    application.include_router(router)
    return application


def test_signature_and_text_normalization():
    body = json.dumps(payload()).encode()
    assert verify_kapso_signature(body, signed(body), SECRET)
    assert not verify_kapso_signature(body, "bad", SECRET)
    assert not verify_kapso_signature(body, None, SECRET)
    event = parse_kapso_payload(payload(), account_id=ACCOUNT)[0]
    assert event.event.body == "hello"
    assert event.sender == "+5411"
    assert event.phone_number_id == PHONE


def test_route_accepts_single_batch_and_ignores_unsupported_or_unmapped():
    command = Command()
    client = TestClient(app(command))
    body = json.dumps({**payload(), "client_account_id": "forged-account"}).encode()
    headers = {"X-Webhook-Event": "whatsapp.message.received", "X-Webhook-Signature": signed(body)}
    assert client.post(KAPSO_ROUTE, content=body, headers=headers).status_code == 200
    batch = json.dumps({"batch": True, "data": [payload("m-2"), payload("m-3")] }).encode()
    headers["X-Webhook-Signature"] = signed(batch)
    assert client.post(KAPSO_ROUTE, content=batch, headers=headers).status_code == 200
    headers["X-Webhook-Event"] = "other.event"
    assert client.post(KAPSO_ROUTE, content=body, headers={**headers, "X-Webhook-Signature": signed(body)}).status_code == 202
    unmapped = json.dumps(payload(phone="unknown")).encode()
    assert client.post(KAPSO_ROUTE, content=unmapped, headers={**headers, "X-Webhook-Event": "whatsapp.message.received",
        "X-Webhook-Signature": signed(unmapped)}).status_code == 202
    assert len(command.calls) == 3
    replay = client.post(KAPSO_ROUTE, content=body, headers={**headers,
        "X-Webhook-Event": "whatsapp.message.received", "X-Webhook-Signature": signed(body)})
    assert replay.status_code == 200 and replay.json()["results"][0]["replayed"] is True
    assert {call[0] for call in command.calls} == {ACCOUNT}


def test_route_rejects_bad_signature_and_malformed_or_unsupported_messages():
    client = TestClient(app(Command()))
    body = json.dumps(payload()).encode()
    assert client.post(KAPSO_ROUTE, content=body).status_code == 401
    bad = json.dumps({**payload(), "message": {"id": "m", "type": "image"}}).encode()
    headers = {"X-Webhook-Event": "whatsapp.message.received", "X-Webhook-Signature": signed(bad)}
    assert client.post(KAPSO_ROUTE, content=bad, headers=headers).status_code == 202


def test_service_context_uses_trusted_account_and_fails_closed():
    agency = Agency("agency", "Agency")
    account = ClientAccount(ACCOUNT, agency.id, "Account")
    resolver = TenantContextResolver(
        InMemoryIdentityAdapter(None),
        InMemoryTenantRepository([agency], [account]),
        memberships=None,
    )
    context = asyncio.run(resolver.resolve_service(ACCOUNT, "kapso-correlation"))
    assert context.user.id == "service:tenant-webhook"
    assert context.membership.permissions == frozenset({"client_account:write"})
    try:
        asyncio.run(resolver.resolve_service("missing-account", "kapso-correlation"))
        assert False, "missing mapped account must be denied"
    except TenantAccessDenied:
        pass
