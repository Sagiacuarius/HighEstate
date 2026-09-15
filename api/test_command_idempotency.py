"""Real PostgreSQL Work Unit 5 tests.

Database: production_runtime_wu4. No passwords are read by this module;
psycopg/libpq uses the local pgpass configuration.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from api.adapters.postgres_core import CrossTenantAccessDenied, PostgresRunner, load_database_configuration, ConnectionPool
from api.adapters.postgres_idempotency import (
    CommandResourceNotFound,
    IdempotencyConflict,
    PostgresCommandConnection,
    TransactionalUpdateTenantResource,
)
from api.composition_wu5 import create_wu5_application
from api.tenant_boundary import Agency, ClientAccount, InMemoryIdentityAdapter, Membership, TenantContext, User

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
USER_A = "11000000-0000-0000-0000-000000000001"
RESOURCE_A = "40000000-0000-0000-0000-000000000001"
PERMISSIONS = frozenset({"client_account:read", "client_account:write"})


def context_a() -> TenantContext:
    return TenantContext(
        user=User(USER_A, "A-only"),
        agency=Agency(AGENCY_A, "Agency A"),
        client_account=ClientAccount(ACCOUNT_A, AGENCY_A, "Account A"),
        membership=Membership(USER_A, AGENCY_A, ACCOUNT_A, PERMISSIONS),
        correlation_id="wu5",
    )


async def query(runner: PostgresRunner, sql: str, params: tuple[Any, ...] = ()):
    return await runner.query(sql, params)


async def reset(runner: PostgresRunner) -> None:
    await runner.execute("TRUNCATE command_idempotency")
    await runner.execute(
        "UPDATE tenant_resource SET data = %s::jsonb WHERE id = %s",
        ('{"value":"A"}', RESOURCE_A),
    )
    await runner.execute("DELETE FROM tenant_resource WHERE resource_key LIKE %s", ("wu5-%",))


async def main() -> list[tuple[str, bool, str]]:
    cfg = load_database_configuration()
    runner = PostgresRunner(ConnectionPool(cfg))
    await reset(runner)
    command = TransactionalUpdateTenantResource(PostgresCommandConnection(cfg))
    results: list[tuple[str, bool, str]] = []
    ctx = context_a()

    # Direct new command.
    first = await command.execute(ctx, resource_id=RESOURCE_A, request_hash="hash-one", data={"value": "first"}, idempotency_key="direct-key")
    results.append(("new command mutates and commits", not first.replayed and first.rows_affected == 1 and first.payload["data"] == {"value": "first"}, str(first)))

    replay = await command.execute(ctx, resource_id=RESOURCE_A, request_hash="hash-one", data={"value": "different-but-not-used"}, idempotency_key="direct-key")
    rows = await query(runner, "SELECT data::text AS data FROM tenant_resource WHERE id=%s", (RESOURCE_A,))
    results.append(("same key and hash replays stored result without mutation", replay.replayed and replay.rows_affected == 0 and replay.payload == first.payload and rows[0]["data"] == '{"value": "first"}', str(replay)))

    try:
        await command.execute(ctx, resource_id=RESOURCE_A, request_hash="hash-two", data={"value": "conflict"}, idempotency_key="direct-key")
        results.append(("same key different hash conflicts", False, "not rejected"))
    except IdempotencyConflict:
        rows = await query(runner, "SELECT data::text AS data FROM tenant_resource WHERE id=%s", (RESOURCE_A,))
        results.append(("same key different hash conflicts without mutation", rows[0]["data"] == '{"value": "first"}', rows[0]["data"]))

    # Concurrency: five independent transactions, one reservation/mutation.
    await reset(runner)
    async def same_request(i: int):
        return await command.execute(ctx, resource_id=RESOURCE_A, request_hash="parallel-hash", data={"value": "parallel"}, idempotency_key="parallel-key")
    concurrent = await asyncio.gather(*(same_request(i) for i in range(5)))
    final = await query(runner, "SELECT data::text AS data FROM tenant_resource WHERE id=%s", (RESOURCE_A,))
    idem = await query(runner, "SELECT count(*) AS count, count(*) FILTER (WHERE status='completed') AS completed FROM command_idempotency WHERE actor_id=%s AND operation=%s AND idempotency_key=%s", (USER_A, command.operation, "parallel-key"))
    fresh = sum(not item.replayed for item in concurrent)
    replays = sum(item.replayed for item in concurrent)
    results.append(("five concurrent same key yields one mutation and four replays", fresh == 1 and replays == 4 and idem[0]["count"] == 1 and idem[0]["completed"] == 1 and final[0]["data"] == '{"value": "parallel"}', f"fresh={fresh} replays={replays} idem={idem[0]}"))

    # Concurrent conflicting hashes: one wins, the others conflict; none mutate after winner.
    await reset(runner)
    async def conflicting(i: int):
        try:
            result = await command.execute(ctx, resource_id=RESOURCE_A, request_hash=f"hash-{i}", data={"value": f"conflict-{i}"}, idempotency_key="conflict-key")
            return ("winner", result)
        except IdempotencyConflict:
            return ("conflict", None)
    conflict_results = await asyncio.gather(*(conflicting(i) for i in range(5)))
    conflict_count = sum(kind == "conflict" for kind, _ in conflict_results)
    winner_count = sum(kind == "winner" for kind, _ in conflict_results)
    final_conflict = await query(runner, "SELECT data::text AS data FROM tenant_resource WHERE id=%s", (RESOURCE_A,))
    idem_conflict = await query(runner, "SELECT count(*) AS count FROM command_idempotency WHERE actor_id=%s AND operation=%s AND idempotency_key=%s", (USER_A, command.operation, "conflict-key"))
    results.append(("same key different hashes yields one winner and four conflicts", winner_count == 1 and conflict_count == 4 and idem_conflict[0]["count"] == 1 and final_conflict[0]["data"] in {f'{{"value": "conflict-{i}"}}' for i in range(5)}, f"winner={winner_count} conflicts={conflict_count} idem={idem_conflict[0]}"))

    # HTTP replay and conflict envelope, with idempotency key sourced from header.
    await reset(runner)
    app, http_runner = create_wu5_application(InMemoryIdentityAdapter(User(USER_A, "A-only")), cfg, ACCOUNT_A)
    with TestClient(app) as client:
        first_http = client.put(f"/commands/tenant-resources/{RESOURCE_A}", json={"data": {"value": "http"}}, headers={"X-Correlation-Id": "http-one", "X-Idempotency-Key": "http-key"})
        second_http = client.put(f"/commands/tenant-resources/{RESOURCE_A}", json={"data": {"value": "http"}}, headers={"X-Correlation-Id": "http-two", "X-Idempotency-Key": "http-key"})
        conflict_http = client.put(f"/commands/tenant-resources/{RESOURCE_A}", json={"data": {"value": "http-conflict"}}, headers={"X-Correlation-Id": "http-three", "X-Idempotency-Key": "http-key"})
        first_body, second_body, conflict_body = first_http.json(), second_http.json(), conflict_http.json()
        http_ok = (
            first_http.status_code == 200 and first_body["replayed"] is False
            and second_http.status_code == 200 and second_body["replayed"] is True
            and second_body["result"] == first_body["result"]
            and conflict_http.status_code == 409
            and conflict_body["error"]["code"] == "tenant_idempotency_conflict"
            and conflict_body["error"]["correlation_id"] == "http-three"
            and conflict_http.headers.get("x-correlation-id") == "http-three"
        )
        results.append(("HTTP new/replay/conflict behavior and 409 envelope", http_ok, f"{first_http.status_code}/{second_http.status_code}/{conflict_http.status_code}"))
    await http_runner.close()

    # Missing idempotency header is rejected before command execution.
    app_missing, http_runner = create_wu5_application(InMemoryIdentityAdapter(User(USER_A, "A-only")), cfg, ACCOUNT_A)
    with TestClient(app_missing) as client:
        missing = client.put(f"/commands/tenant-resources/{RESOURCE_A}", json={"data": {"value": "missing"}}, headers={"X-Correlation-Id": "missing-key"})
    results.append(("missing idempotency header rejected", missing.status_code == 400 and missing.json()["error"]["code"] == "invalid_idempotency_key", str(missing.json())))
    await http_runner.close()

    await runner.close()
    return results


def test_command_idempotency():
    outcomes = asyncio.run(main())
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = asyncio.run(main())
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"COMMAND IDEMPOTENCY SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
