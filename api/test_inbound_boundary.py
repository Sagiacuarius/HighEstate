"""MVP Commercial Demo WU4: inbound-boundary tests over FastAPI + real PostgreSQL.

Runs against production_runtime_mvp_wu4 (disposable database: migrations
001-004 + deterministic mvp_seed). Never touches production_runtime_mvp_wu2/wu3.

Config: RUNTIME_DB_HOST/PORT/NAME/USER env vars; password via libpq pgpass.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from api.adapters.postgres_core import load_database_configuration
from api.composition_wu4_mvp import INBOUND_ROUTE, build_mvp_wu4_application
from api.inbound_boundary import parse_inbound_message_event, InboundPayloadInvalid
from api.tenant_boundary import InMemoryIdentityAdapter, User

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_SEEDED = "10000000-0000-0000-0000-000000000001"   # member of A and B (seed)
USER_OUTSIDER = "1f000000-0000-0000-0000-000000000001"  # no memberships
A_CONVERSATION = "66000000-0000-0000-0000-000000000001"
B_CONVERSATION = "66000000-0000-0000-0000-000000000002"


def canonical_payload(client_account_id: str = ACCOUNT_A, conversation_id: str = A_CONVERSATION,
                      **overrides) -> dict:
    event_id = overrides.pop("eventId", f"dev-{uuid4().hex[:8]}")
    message_id = overrides.pop("messageId", str(uuid4()))
    body = overrides.pop("body", "Simulated inbound message for WU4.")
    direction = overrides.pop("direction", "inbound")
    received_at = overrides.pop("receivedAt", "2026-09-01T12:00:00-03:00")
    account = overrides.pop("clientAccountId", client_account_id)
    conversation = overrides.pop("conversationId", conversation_id)
    payload = {
        "eventId": event_id,
        "clientAccountId": account,
        "conversationId": conversation,
        "message": {
            "id": message_id,
            "conversationId": conversation,
            "clientAccountId": account,
            "direction": direction,
            "body": body,
            "receivedAt": received_at,
        },
    }
    payload.update(overrides)
    return payload


def client_for(user_id: str | None, target_account: str):
    identity = InMemoryIdentityAdapter(User(user_id, user_id) if user_id else None)
    app = build_mvp_wu4_application(load_database_configuration(), target_account, identity)
    return TestClient(app), app


def envelope_ok(response, expected_status: int, correlation: str) -> bool:
    body = response.json()
    return (
        response.status_code == expected_status
        and body.get("error", {}).get("correlation_id") == correlation
        and response.headers.get("x-correlation-id") == correlation
    )


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    # I1. Valid canonical payload accepted (202), event normalized, correlation echoed.
    correlation = "wu4-accept-1"
    c_a, app_a = client_for(USER_SEEDED, ACCOUNT_A)
    with c_a:
        payload = canonical_payload()
        response = c_a.post(INBOUND_ROUTE, json=payload, headers={"X-Correlation-Id": correlation})
        journal = app_a.state.demo_composition.inbound_handler.journal
        body = response.json()
        ok = (
            response.status_code == 202
            and body["accepted"] is True
            and body["environment"] == "dev-only-inbound-boundary"
            and body["event"]["eventId"] == payload["eventId"]
            and body["event"]["message"]["receivedAt"] == "2026-09-01T15:00:00.000Z"
            and body["event"]["message"]["conversationId"] == A_CONVERSATION
            and response.headers.get("x-correlation-id") == correlation
            and len(journal) == 1 and journal[0].event_id == payload["eventId"]
        )
        record("canonical payload accepted with normalized event", ok,
               f"status={response.status_code} receivedAt={body.get('event', {}).get('message', {}).get('receivedAt')}")

        # I2. Malformed payloads -> 400, consistent envelope, no mutation.
        before = len(journal)
        malformed = {
            "non-dict body": None,
            "bad direction": canonical_payload(direction="outbound"),
            "empty body text": canonical_payload(body="   "),
            "inconsistent message scope": canonical_payload(message={"id": str(uuid4()),
                                                                      "conversationId": "conversation-x",
                                                                      "clientAccountId": ACCOUNT_A,
                                                                      "direction": "inbound", "body": "hi",
                                                                      "receivedAt": "2026-09-01T12:00:00Z"}),
            "invalid timestamp": canonical_payload(receivedAt="not-a-date"),
            "extra top-level key": {**canonical_payload(), "unexpected": 1},
        }
        all_400 = True
        for label, bad in malformed.items():
            corr = f"wu4-400-{abs(hash(label))}"
            r2 = c_a.post(INBOUND_ROUTE, json=bad, headers={"X-Correlation-Id": corr})
            code_ok = envelope_ok(r2, 400, corr) and r2.json()["error"]["code"] == "invalid_payload"
            if not code_ok:
                all_400 = False
                record(f"malformed ({label}) -> 400", False, f"status={r2.status_code} body={r2.text[:120]}")
        record("all malformed payload variants -> 400 envelope, no journal growth",
               all_400 and len(journal) == before, f"journal={len(journal)}")

        # I3. Missing scope field -> 400.
        no_scope = canonical_payload()
        del no_scope["clientAccountId"]
        r = c_a.post(INBOUND_ROUTE, json=no_scope, headers={"X-Correlation-Id": "wu4-noscope"})
        record("missing clientAccountId -> 400", envelope_ok(r, 400, "wu4-noscope"), f"status={r.status_code}")

        # I4. Session A, payload claims B -> 403 non-enumerating; B untouched.
        r = c_a.post(INBOUND_ROUTE, json=canonical_payload(client_account_id=ACCOUNT_B),
                     headers={"X-Correlation-Id": "wu4-cross"})
        leaked = ACCOUNT_B in r.text or "membership" in r.text.lower()
        ok = envelope_ok(r, 403, "wu4-cross") and r.json()["error"]["code"] == "tenant_access_denied" and not leaked
        record("payload claims foreign account -> 403 non-enumerating", ok, f"status={r.status_code} leak={leaked}")

        # I5. Session A, conversation owned by B -> 403 via scoped repository.
        r = c_a.post(INBOUND_ROUTE, json=canonical_payload(conversation_id=B_CONVERSATION),
                     headers={"X-Correlation-Id": "wu4-fconv"})
        ok = envelope_ok(r, 403, "wu4-fconv") and r.json()["error"]["code"] == "tenant_access_denied"
        record("conversation of B under A session -> 403", ok, f"status={r.status_code}")
        record("denied attempts recorded nothing", len(journal) == 1, f"journal={len(journal)}")

        # I6. Identity absent -> 401.
        c_none, _ = client_for(None, ACCOUNT_A)
        with c_none:
            r = c_none.post(INBOUND_ROUTE, json=canonical_payload(), headers={"X-Correlation-Id": "wu4-anon"})
            ok = envelope_ok(r, 401, "wu4-anon") and r.json()["error"]["code"] == "authentication_required"
            record("absent server identity -> 401", ok, f"status={r.status_code}")

        # I6b. User without membership to the session account -> 403 (same shape as foreign).
        c_out, _ = client_for(USER_OUTSIDER, ACCOUNT_A)
        with c_out:
            r = c_out.post(INBOUND_ROUTE, json=canonical_payload(), headers={"X-Correlation-Id": "wu4-out"})
            ok = envelope_ok(r, 403, "wu4-out") and r.json()["error"]["code"] == "tenant_access_denied"
            record("user without membership -> 403 same non-enumerating shape", ok, f"status={r.status_code}")

        # I7. Normalized event equals pure-parser contract output (same object shape).
        ev = parse_inbound_message_event(canonical_payload())
        ok = ev.direction == "inbound" and ev.received_at.endswith("Z") and "+00:00" not in ev.received_at
        try:
            parse_inbound_message_event({"eventId": " ", "clientAccountId": ACCOUNT_A,
                                         "conversationId": A_CONVERSATION, "message": {}})
            ok = False
        except InboundPayloadInvalid:
            pass
        record("parser normalizes and rejects per WU1 contract rules", ok, ev.received_at)

        # I8. Replay without idempotency: accepted twice (documented WU5 gap),
        # each with its own correlation echoed consistently.
        r1 = c_a.post(INBOUND_ROUTE, json=payload, headers={"X-Correlation-Id": "wu4-replay-1"})
        r2 = c_a.post(INBOUND_ROUTE, json=payload, headers={"X-Correlation-Id": "wu4-replay-2"})
        ok = (r1.status_code == 202 and r2.status_code == 202
              and r1.json()["correlation_id"] == "wu4-replay-1"
              and r2.json()["correlation_id"] == "wu4-replay-2"
              and len(journal) == 3)  # 1 from I1 + 2 replays
        record("replay accepted twice with per-request correlation (idempotency deferred to WU5)",
               ok, f"journal={len(journal)}")

    # I9. Session B only sees/records B's conversation; A's is 403-safe.
    c_b, app_b = client_for(USER_SEEDED, ACCOUNT_B)
    with c_b:
        r = c_b.post(INBOUND_ROUTE, json=canonical_payload(client_account_id=ACCOUNT_A),
                     headers={"X-Correlation-Id": "wu4-b-forged"})
        r_ok = c_b.post(INBOUND_ROUTE, json=canonical_payload(client_account_id=ACCOUNT_B,
                                                              conversation_id=B_CONVERSATION),
                        headers={"X-Correlation-Id": "wu4-b-ok"})
        journal_b = app_b.state.demo_composition.inbound_handler.journal
        ok = (envelope_ok(r, 403, "wu4-b-forged") and r_ok.status_code == 202
              and len(journal_b) == 1 and journal_b[0].conversation_id == B_CONVERSATION)
        record("B session: A claim 403, own conversation accepted", ok, f"journal_b={len(journal_b)}")

    # I10. DB state: inbound boundary created no CRM rows (seed = 2 messages + 2 leads).
    counts = subprocess.run(
        ["psql", "-tA", "-c", "select (select count(*) from messages)+(select count(*) from leads)"],
        capture_output=True, text=True, env=os.environ.copy())
    ok = counts.returncode == 0 and counts.stdout.strip() == "4"  # seeded rows only
    record("boundary persisted nothing (no CRM commands yet)", ok, counts.stdout.strip())
    return results


def test_inbound_boundary():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"MVP-CRM WU4 INBOUND SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
