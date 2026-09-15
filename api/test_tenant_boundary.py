"""Work Unit 2 provider-neutral identity/context contract scenarios."""

from __future__ import annotations

from fastapi import Request
from fastapi.testclient import TestClient

from api.composition import create_test_application
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    InMemoryIdentityAdapter,
    InMemoryMembershipRepository,
    InMemoryTenantRepository,
    Membership,
    TenantAccessDenied,
    TenantContext,
    TenantContextResolver,
    TenantAuthenticationRequired,
    User,
)


def fixture():
    agency = Agency("agency-a", "Agency A")
    other_agency = Agency("agency-b", "Agency B")
    account_a = ClientAccount("account-a", agency.id, "Account A")
    account_b = ClientAccount("account-b", agency.id, "Account B")
    foreign = ClientAccount("account-foreign", other_agency.id, "Foreign")
    user = User("user-a", "User A")
    only_a_user = User("user-only-a", "Only A")
    membership_a = Membership(user.id, agency.id, account_a.id, frozenset({"client_account:read", "client_account:switch"}))
    membership_b = Membership(user.id, agency.id, account_b.id, frozenset({"client_account:read", "client_account:switch"}))
    only_a_membership = Membership(only_a_user.id, agency.id, account_a.id, frozenset({"client_account:read", "client_account:switch"}))
    tenants = InMemoryTenantRepository([agency, other_agency], [account_a, account_b, foreign])
    memberships = InMemoryMembershipRepository([membership_a, membership_b, only_a_membership])
    identity = InMemoryIdentityAdapter(user)
    resolver = TenantContextResolver(identity, tenants, memberships)
    return locals()


def assert_error_envelope(response, status: int, correlation: str, code: str) -> bool:
    body = response.json()
    return (
        response.status_code == status
        and body.get("error", {}).get("code") == code
        and body.get("error", {}).get("correlation_id") == correlation
        and response.headers.get("x-correlation-id") == correlation
    )


def run_tenant_scenarios() -> list[tuple[str, bool, str]]:
    data = fixture()
    results: list[tuple[str, bool, str]] = []

    # Pure application resolution from server-owned identity.
    request = Request({"type": "http", "headers": [], "state": {}})
    # Request.state is supplied by FastAPI middleware in HTTP tests; this pure
    # check uses a small ASGI request scope and a correlation fallback.
    try:
        context = __import__("asyncio").run(data["resolver"].resolve(request, data["account_a"].id))
        results.append(("valid identity resolves Account A", context.client_account.id == "account-a", context.user.id))
    except Exception as error:
        results.append(("valid identity resolves Account A", False, str(error)))

    # Explicit Agency/ClientAccount consistency invariant.
    class InconsistentTenantRepository:
        async def find_account(self, _client_account_id):
            return data["other_agency"], data["account_a"]

    inconsistent = TenantContextResolver(data["identity"], InconsistentTenantRepository(), data["memberships"])
    try:
        __import__("asyncio").run(inconsistent.resolve(request, data["account_a"].id))
        results.append(("inconsistent Agency/ClientAccount relation rejected", False, "not rejected"))
    except TenantAccessDenied:
        results.append(("inconsistent Agency/ClientAccount relation rejected", True, "fail-closed"))

    # Context is immutable and previous context is not mutated by a denied target.
    context_a = TenantContext(data["user"], data["agency"], data["account_a"], data["membership_a"], "corr")
    try:
        __import__("asyncio").run(data["resolver"].switch(request, context_a, data["foreign"].id))
        results.append(("foreign switch rejected and prior context preserved", False, "not rejected"))
    except TenantAccessDenied:
        results.append(("foreign switch rejected and prior context preserved", context_a.client_account.id == "account-a", "account-a"))

    unconfigured_app = create_test_application(None)
    with TestClient(unconfigured_app) as unconfigured_client:
        unconfigured = unconfigured_client.get("/tenant/context/account-a", headers={"X-Correlation-Id": "unconfigured"})
    results.append(("missing composition returns safe 503", assert_error_envelope(unconfigured, 503, "unconfigured", "tenant_service_not_configured"), str(unconfigured.json())))

    test_app = create_test_application(data["resolver"])
    with TestClient(test_app) as client:
        response_a = client.get("/tenant/context/account-a", headers={"X-Correlation-Id": "valid-a"})
        response_b = client.get("/tenant/context/account-b", headers={"X-Correlation-Id": "valid-b"})
        both_ok = response_a.status_code == 200 and response_b.status_code == 200
        results.append(("authorized user with explicit A+B memberships accesses both", both_ok, f"{response_a.status_code}/{response_b.status_code}"))

        forged_agency = client.get("/tenant/context/account-a?agency_id=agency-forged", headers={"X-Correlation-Id": "forged-agency"})
        results.append(("falsified agency scope rejected with consistent 403 envelope", assert_error_envelope(forged_agency, 403, "forged-agency", "tenant_access_denied"), str(forged_agency.json())))

        missing_account = client.get("/tenant/context/account-does-not-exist", headers={"X-Correlation-Id": "missing-account"})
        foreign_account = client.get("/tenant/context/account-foreign", headers={"X-Correlation-Id": "foreign-account"})
        same_denial = (
            missing_account.status_code == 403
            and foreign_account.status_code == 403
            and missing_account.json()["error"]["code"] == foreign_account.json()["error"]["code"]
            and missing_account.json()["error"]["message"] == foreign_account.json()["error"]["message"]
            and missing_account.json()["error"]["correlation_id"] == "missing-account"
            and foreign_account.json()["error"]["correlation_id"] == "foreign-account"
            and missing_account.headers.get("x-correlation-id") == "missing-account"
            and foreign_account.headers.get("x-correlation-id") == "foreign-account"
        )
        results.append(("restricted/nonexistent accounts are non-enumerating", same_denial, f"{missing_account.json()} / {foreign_account.json()}"))

        absent = InMemoryIdentityAdapter(None)
        unauth_app = create_test_application(TenantContextResolver(absent, data["tenants"], data["memberships"]))
        with TestClient(unauth_app) as unauth_client:
            unauthenticated = unauth_client.get("/tenant/context/account-a", headers={"X-Correlation-Id": "no-identity"})
        body = unauthenticated.json()
        results.append(("absent identity returns 401 envelope with correlation", assert_error_envelope(unauthenticated, 401, "no-identity", "authentication_required"), str(body)))

        only_a_app = create_test_application(TenantContextResolver(InMemoryIdentityAdapter(data["only_a_user"]), data["tenants"], data["memberships"]))
        with TestClient(only_a_app) as only_a_client:
            account_b_denied = only_a_client.get("/tenant/context/account-b", headers={"X-Correlation-Id": "only-a"})
        results.append(("user with only A membership cannot access B", assert_error_envelope(account_b_denied, 403, "only-a", "tenant_access_denied"), str(account_b_denied.json())))

    return results


def test_identity_context_contracts():
    failures = [name for name, ok, _ in run_tenant_scenarios() if not ok]
    assert failures == [], f"failing scenarios: {failures}"


def test_current_identity_never_reads_request_claims():
    data = fixture()
    adapter = InMemoryIdentityAdapter(data["user"])
    request = Request({"type": "http", "headers": [(b"x-user-id", b"user-forged")], "state": {}})
    import asyncio
    assert asyncio.run(adapter.get_authenticated_user(request)) == data["user"]


if __name__ == "__main__":
    outcomes = run_tenant_scenarios()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"TENANT SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)