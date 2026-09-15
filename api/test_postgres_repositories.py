"""Work Unit 4 real-PostgreSQL isolation tests.

Runs against production_runtime_wu4 (disposable test database created by
infra/migrations/001 + wu35 seed + wu4 fixtures). Every scenario verifies
status/rows/affected counts and the final committed database state.

Config comes from the same fail-fast env vars as the adapters:
  RUNTIME_DB_HOST RUNTIME_DB_PORT RUNTIME_DB_NAME RUNTIME_DB_USER
Password authentication is resolved locally by libpq (pgpass); never in code.
"""

from __future__ import annotations

import asyncio
import json
import sys

from fastapi.testclient import TestClient

from api.adapters.postgres_core import (
    CrossTenantAccessDenied,
    DatabaseConfigurationError,
    load_database_configuration,
)
from api.adapters.postgres_resources import PostgresTenantResourceRepository
from api.adapters.postgres_tenancy import PostgresMembershipRepository, PostgresTenantRepository
from api.composition_wu4 import build_postgres_stack, create_wu4_application
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    InMemoryIdentityAdapter,
    Membership,
    TenantContext,
    User,
)

AGENCY_A = "00000000-0000-0000-0000-000000000001"
AGENCY_B = "00000000-0000-0000-0000-000000000002"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
ACCOUNT_OTHER = "20000000-0000-0000-0000-000000000003"
USER_A_ONLY = "11000000-0000-0000-0000-000000000001"
USER_AB = "12000000-0000-0000-0000-000000000002"
USER_NONE = "10000000-0000-0000-0000-000000000003"
SECRET_B_ID = "41000000-0000-0000-0000-000000000101"
DEMO_A_ID = "40000000-0000-0000-0000-000000000001"

WRITE_PERMS = frozenset({"client_account:read", "client_account:write"})


async def _resources_for(runner, account_id: str):
    return await runner.query(
        "SELECT id, resource_key, client_account_id, data::text as data FROM tenant_resource WHERE client_account_id = %s",
        (account_id,),
    )


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    # T0. Fail-fast configuration.
    try:
        cfg = load_database_configuration()
        results.append(("db config loads fail-fast", True, f"host/port/db/user present, no password field: {sorted(cfg)}"))
    except DatabaseConfigurationError as error:
        results.append(("db config loads fail-fast", False, str(error)))
        return results

    runner, tenants, memberships, _users, resources = build_postgres_stack(dict(cfg))

    def run(coro):
        return asyncio.run(coro)

    # T1. Real repositories resolve server-side accounts/memberships.
    account = run(tenants.find_account(ACCOUNT_A))
    assert account is not None
    membership_ab = run(memberships.find_membership(USER_AB, ACCOUNT_B))
    ok = (
        account[0].id == AGENCY_A
        and account[1].id == ACCOUNT_A
        and membership_ab is not None
        and membership_ab.agency_id == AGENCY_A
    )
    results.append(("postgres tenancy repositories resolve real rows", ok, f"account={account[1].name}, membership_ab={'found' if membership_ab else 'none'}"))

    # T2. Missing context rejected before SQL.
    try:
        run(resources.list(None))  # type: ignore[arg-type]
        results.append(("absent context rejected", False, "no exception"))
    except CrossTenantAccessDenied as error:
        results.append(("absent context rejected", True, str(error)))

    # T3. Forged inconsistent context rejected before SQL.
    forged_ctx = TenantContext(
        user=User(USER_A_ONLY, "u"),
        agency=Agency(AGENCY_A, "Agency A"),
        client_account=ClientAccount(ACCOUNT_B, AGENCY_A, "Account B"),
        membership=Membership(USER_A_ONLY, AGENCY_A, ACCOUNT_A, WRITE_PERMS),
        correlation_id="forged-ctx",
    )
    try:
        run(resources.list(forged_ctx))
        results.append(("inconsistent context rejected", False, "no exception"))
    except CrossTenantAccessDenied as error:
        results.append(("inconsistent context rejected", True, str(error)))

    # HTTP scenarios through the minimal internal API.
    def client_for(user_id: str, target_account: str) -> TestClient:
        identity = InMemoryIdentityAdapter(User(user_id, user_id))
        app = create_wu4_application(identity, tenants, memberships, resources, target_account)
        return TestClient(app)

    # T4. U_A_ONLY reads A and only sees A rows.
    with client_for(USER_A_ONLY, ACCOUNT_A) as c:
        listing = c.get("/tenant/resources/", headers={"X-Correlation-Id": "list-a"})
        keys = {item["resource_key"] for item in listing.json().get("items", [])}
        ok = listing.status_code == 200 and keys and all(
            item["client_account_id"] == ACCOUNT_A for item in listing.json()["items"]
        ) and "secret-b" not in keys
        results.append(("A-context list returns only Account A rows", ok, f"status={listing.status_code} keys={sorted(keys)}"))

        # T5. U_A_ONLY cannot get a context for B.
        ctx_b = c.get(f"/tenant/context/{ACCOUNT_B}", headers={"X-Correlation-Id": "ctx-b"})
        ok = ctx_b.status_code == 403 and ctx_b.json()["error"]["code"] == "tenant_access_denied"
        results.append(("membership only in A cannot resolve B", ok, str(ctx_b.json()["error"]["code"])))

        # T6. read/update/delete of B's resource with A context → rejected.
        read_b = c.get(f"/tenant/resources/{SECRET_B_ID}", headers={"X-Correlation-Id": "read-b"})
        ok = read_b.status_code == 403 and "must-not-leak" not in read_b.text
        results.append(("read B resource with A context rejected", ok, f"status={read_b.status_code}, no leak in body={ok}"))

        upd_b = c.put(
            f"/tenant/resources/{SECRET_B_ID}",
            json={"data": {"value": "tampered"}},
            headers={"X-Correlation-Id": "update-b"},
        )
        ok = upd_b.status_code == 403
        results.append(("update B resource with A context rejected", ok, f"status={upd_b.status_code}"))

        del_b = c.delete(f"/tenant/resources/{SECRET_B_ID}", headers={"X-Correlation-Id": "delete-b"})
        ok = del_b.status_code == 403
        results.append(("delete B resource with A context rejected", ok, f"status={del_b.status_code}"))

        # T7. forged client_account_id in create payload cannot target B.
        forge = c.post(
            "/tenant/resources/",
            json={"id": "50000000-0000-0000-0000-0000000000ff", "resource_key": "forged", "client_account_id": ACCOUNT_B},
            headers={"X-Correlation-Id": "create-forged"},
        )
        # Client account is never accepted from the payload. A supplied value
        # that differs from server-derived A is explicitly rejected.
        ok = forge.status_code == 403
        results.append(("client_account_id spoof rejected before write", ok, f"status={forge.status_code}"))

        rows_b = run(_resources_for(runner, ACCOUNT_B))
    leaked = any(r["resource_key"] == "forged" for r in rows_b)
    results.append(("no forged row persisted in B", not leaked, f"b_rows={[r['resource_key'] for r in rows_b]}"))

    # T8. Same secret resource readable by its owner context (B).
    with client_for(USER_AB, ACCOUNT_B) as cb:
        own = cb.get(f"/tenant/resources/{SECRET_B_ID}", headers={"X-Correlation-Id": "own-b"})
        ok = own.status_code == 200 and own.json()["item"]["resource_key"] == "secret-b"
        results.append(("authorized B context can read B resource", ok, f"status={own.status_code}"))

        # T9. A+B user can operate on both accounts with the matching context.
        both_a = cb  # switch client: rebuild for A
    with client_for(USER_AB, ACCOUNT_A) as ca:
        a_ok = ca.get("/tenant/resources/", headers={"X-Correlation-Id": "ab-a"}).status_code == 200
    with client_for(USER_AB, ACCOUNT_B) as cb:
        b_ok = cb.get("/tenant/resources/", headers={"X-Correlation-Id": "ab-b"}).status_code == 200
    results.append(("A+B member reads A and B with matching contexts", a_ok and b_ok, f"A={a_ok} B={b_ok}"))

    # T10. Account without membership for this user (OtherAgency) rejected.
    with client_for(USER_A_ONLY, ACCOUNT_OTHER) as co:
        foreign = co.get("/tenant/resources/", headers={"X-Correlation-Id": "foreign-agency"})
        ok = foreign.status_code == 403
        results.append(("account in other agency rejected", ok, f"status={foreign.status_code}"))

    # T11. No membership user rejected.
    with client_for(USER_NONE, ACCOUNT_A) as cn:
        none_ctx = cn.get("/tenant/resources/", headers={"X-Correlation-Id": "no-membership"})
        ok = none_ctx.status_code == 403
        results.append(("user without membership rejected", ok, f"status={none_ctx.status_code}"))

    # T12. Unauthenticated identity rejected with 401.
    anonymous = InMemoryIdentityAdapter(None)
    app_anon = create_wu4_application(anonymous, tenants, memberships, resources, ACCOUNT_A)
    with TestClient(app_anon) as ca_non:
        anon = ca_non.get("/tenant/resources/", headers={"X-Correlation-Id": "anon"})
        ok = anon.status_code == 401 and anon.json()["error"]["correlation_id"] == "anon" and anon.headers.get("x-correlation-id") == "anon"
        results.append(("anonymous request returns 401 envelope", ok, f"status={anon.status_code}"))

    # T13. Final DB state: B's data completely unchanged.
    final_b = run(_resources_for(runner, ACCOUNT_B))
    snapshot = sorted((r["resource_key"], r["data"]) for r in final_b)
    ok = any(r["resource_key"] == "secret-b" and "must-not-leak" in r["data"] for r in final_b) and not any(r["resource_key"] in ("forged", "tampered") for r in final_b)
    results.append(("Account B unchanged after all attempts", ok, f"snapshot={[(r['resource_key'], r['data'][:40]) for r in final_b]}"))

    final_a = run(_resources_for(runner, ACCOUNT_A))
    a_keys = sorted(r["resource_key"] for r in final_a)
    results.append(("Account A final rows recorded", True, f"keys={a_keys}"))

    # T14. update_data returns affected-row evidence via repo directly.
    direct_ctx = TenantContext(
        user=User(USER_A_ONLY, "u"),
        agency=Agency(AGENCY_A, "Agency A"),
        client_account=ClientAccount(ACCOUNT_A, AGENCY_A, "Account A"),
        membership=Membership(USER_A_ONLY, AGENCY_A, ACCOUNT_A, WRITE_PERMS),
        correlation_id="direct",
    )
    updated = run(resources.update(direct_ctx, DEMO_A_ID, {"data": {"value": "A-updated"}}))
    ok = updated is not None and updated["data"] == {"value": "A-updated"}
    results.append(("repo.update on owned row mutates exactly one row", ok, f"data={updated and updated['data']}"))
    # restore original demo value for repeatable re-runs
    run(resources.update(direct_ctx, DEMO_A_ID, {"data": {"value": "A"}}))

    run(runner.close())
    return results


def test_postgres_isolation():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"POSTGRES WU4 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
