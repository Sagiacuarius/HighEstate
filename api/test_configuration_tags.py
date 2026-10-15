"""Configuration contact-tags units (change: configuration, WU17).

Phase 4: `contacts.tags text[]` add/remove with trim+lower canonicalization and
dedupe (idempotent), the `= ANY(tags)` read filter, and account scoping
(cross-account contact id -> non-enumerating denial). Runs against account B's
contact; its tags are restored in try/finally. Idempotency keys carry a
per-run uuid so re-runs never replay a prior command.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from api.adapters.postgres_configuration import list_contacts_by_tag
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.application.configuration_commands_wu17 import (
    AddContactTagCommand,
    RemoveContactTagCommand,
)
from api.application.crm_commands_wu5 import CrmCommandDenied
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    Membership,
    TenantContext,
    User,
)

AGENCY = "00000000-0000-0000-0000-000000000001"
USER = "10000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
A_CONTACT = "61000000-0000-0000-0000-000000000001"
B_CONTACT = "61000000-0000-0000-0000-000000000002"
PERMS = frozenset({"client_account:read", "client_account:write"})


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=tag,
    )


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    async def _run() -> None:
        cfg = load_database_configuration()
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        tag = str(uuid4())
        ctx_b = _ctx(ACCOUNT_B, "tg")
        ctx_a = _ctx(ACCOUNT_A, "tg")
        add_cmd = AddContactTagCommand(dict(cfg))
        remove_cmd = RemoveContactTagCommand(dict(cfg))

        original = await runner.query(
            "SELECT tags FROM contacts WHERE id = %s AND client_account_id = %s",
            (B_CONTACT, ACCOUNT_B))
        assert original, "contact seed missing for B"

        try:
            # TG1 add canonicalizes to lowercase.
            out = await add_cmd.execute_add(
                ctx_b, contact_id=B_CONTACT, tag="VIP",
                idempotency_key=f"tg-add-vip-{tag}")
            tags = set(out.payload["tags"])
            record("TG1 add lowercases (VIP -> vip)",
                   "vip" in tags and "VIP" not in tags, f"tags={sorted(tags)}")

            # TG2 idempotent re-add (spaces + case) is a no-op.
            before = out.payload["tags"]
            out = await add_cmd.execute_add(
                ctx_b, contact_id=B_CONTACT, tag="  Vip ",
                idempotency_key=f"tg-readd-vip-{tag}")
            record("TG2 idempotent re-add dedupes",
                   out.payload["tags"] == before, f"tags={out.payload['tags']}")

            # TG3 second distinct tag appends (still deduped/sorted).
            out = await add_cmd.execute_add(
                ctx_b, contact_id=B_CONTACT, tag="Premium",
                idempotency_key=f"tg-add-prem-{tag}")
            tags = out.payload["tags"]
            record("TG3 distinct tag appends without duplicates",
                   "premium" in tags and tags.count("vip") == 1,
                   f"tags={tags}")

            # TG4 filter `= ANY(tags)` round-trips.
            hits = await list_contacts_by_tag(ctx_b, runner, "vip")
            hit_ids = {h["id"] for h in hits}
            record("TG4 filter by tag returns the tagged contact",
                   B_CONTACT in hit_ids, f"hits={sorted(hit_ids)}")

            # TG5 remove canonicalizes (remove "VIP" removes "vip").
            out = await remove_cmd.execute_remove(
                ctx_b, contact_id=B_CONTACT, tag="VIP",
                idempotency_key=f"tg-rm-vip-{tag}")
            record("TG5 remove case-normalizes (VIP removes vip)",
                   "vip" not in out.payload["tags"], f"tags={out.payload['tags']}")
            hits_after = await list_contacts_by_tag(ctx_b, runner, "vip")
            record("TG5 filter empty after removal", hits_after == [],
                   f"hits={hits_after}")

            # TG6 account scoping: B's tag is invisible to A, and a cross-account
            # contact id is a non-enumerating denial.
            await add_cmd.execute_add(
                ctx_b, contact_id=B_CONTACT, tag="vip",
                idempotency_key=f"tg-re-vip-{tag}")
            a_hits = await list_contacts_by_tag(ctx_a, runner, "vip")
            record("TG6 B's tag invisible to account A",
                   all(h["id"] != B_CONTACT for h in a_hits),
                   f"a_hits={[h['id'] for h in a_hits]}")
            cross = False
            try:
                await add_cmd.execute_add(
                    ctx_b, contact_id=A_CONTACT, tag="vip",
                    idempotency_key=f"tg-cross-{tag}")
            except CrmCommandDenied:
                cross = True
            record("TG6 cross-account contact id denied", cross, "")
        finally:
            await runner.execute(
                "UPDATE contacts SET tags = %s::text[] "
                "WHERE id = %s AND client_account_id = %s",
                (list(original[0]["tags"] or ()), B_CONTACT, ACCOUNT_B))

        restored = await runner.query(
            "SELECT tags FROM contacts WHERE id = %s", (B_CONTACT,))
        record("TG7 B contact tags restored",
               list(restored[0]["tags"] or ()) == list(original[0]["tags"] or ()),
               f"tags={restored[0]['tags']}")

    asyncio.run(_run())
    return results


def test_configuration_tags():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"CONFIGURATION-TAGS UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
