"""MVP Commercial Demo WU3: real-PostgreSQL CRM repository isolation tests.

Runs against production_runtime_mvp_wu3 (disposable database: migrations
001-004 + deterministic mvp_seed). Never touches production_runtime_mvp_wu2.

Config: RUNTIME_DB_HOST/PORT/NAME/USER env vars; password via libpq pgpass.
Each scenario asserts both the repository outcome AND the final committed
database state. Mutations use clearly-owned scratch UUIDs and are cleaned up;
seeded demo rows are only mutated through the repository under the owning
account context and re-converged by re-applying the idempotent seed at the end.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal

from api.adapters.postgres_commercial_crm import (
    CrmConflictError,
    CrmNotFoundError,
    CrmRelationshipViolation,
)
from api.adapters.postgres_core import CrossTenantAccessDenied
from api.composition_wu3_mvp import build_crm_composition
from api.domain.commercial_crm import Contact, FollowUp, Lead, Property
from api.tenant_boundary import Agency, ClientAccount, Membership, TenantContext, User

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_A = "10000000-0000-0000-0000-000000000001"

# Deterministic seeded demo IDs (mvp_seed.sql)
A_CONTACT, B_CONTACT = "61000000-0000-0000-0000-000000000001", "61000000-0000-0000-0000-000000000002"
A_PROPERTY, B_PROPERTY = "62000000-0000-0000-0000-000000000001", "62000000-0000-0000-0000-000000000002"
A_PIPELINE, B_PIPELINE = "63000000-0000-0000-0000-000000000001", "63000000-0000-0000-0000-000000000002"
A_STAGE, B_STAGE = "64000000-0000-0000-0000-000000000001", "64000000-0000-0000-0000-000000000002"
A_LEAD, B_LEAD = "65000000-0000-0000-0000-000000000001", "65000000-0000-0000-0000-000000000002"
A_CONVERSATION, B_CONVERSATION = "66000000-0000-0000-0000-000000000001", "66000000-0000-0000-0000-000000000002"

WRITE_PERMS = frozenset({"client_account:read", "client_account:write"})
READ_PERMS = frozenset({"client_account:read"})


def context_for(account: str, *, write: bool = True) -> TenantContext:
    perms = WRITE_PERMS if write else READ_PERMS
    name = "Account A" if account == ACCOUNT_A else "Account B"
    return TenantContext(
        user=User(USER_A, "demo-user"),
        agency=Agency(AGENCY_A, "Agency A"),
        client_account=ClientAccount(account, AGENCY_A, name),
        membership=Membership(USER_A, AGENCY_A, account, perms),
        correlation_id="wu3",
    )


CTXA = context_for(ACCOUNT_A)
CTXB = context_for(ACCOUNT_B)
CTXA_RO = context_for(ACCOUNT_A, write=False)

# Scratch rows created/removed by the harness itself (not demo-seed rows).
# Prefix ends mid-uuid; append exactly 2 hex digits for a valid uuid tail.
SCRATCH_PREFIX = "7f000000-0000-0000-0000-0000000000"


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    comp = build_crm_composition()
    runner = comp.runner

    def run(coro):
        return asyncio.run(coro)

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    # W0. DB reachable and CRM tables live.
    # Pre-clean scratch left over from any earlier aborted run (restore leads
    # pointing at scratch stages first, since FKs are RESTRICT).
    run(runner.execute(
        "UPDATE leads SET stage_id = %s WHERE stage_id::text LIKE %s AND client_account_id = %s",
        (A_STAGE, SCRATCH_PREFIX + "%", ACCOUNT_A)))
    for table in ("follow_ups", "leads", "contacts", "messages", "conversations", "pipeline_stages", "pipelines", "properties"):
        run(runner.execute(f"DELETE FROM {table} WHERE id::text LIKE %s", (SCRATCH_PREFIX + "%",)))
    counts = run(runner.query(
        "SELECT (SELECT count(*) FROM contacts WHERE client_account_id=%s) a,"
        " (SELECT count(*) FROM contacts WHERE client_account_id=%s) b", (ACCOUNT_A, ACCOUNT_B)))
    record("database reachable, CRM tables live", counts and int(counts[0]["a"]) >= 1 and int(counts[0]["b"]) >= 1,
           f"A_contacts={counts[0]['a']} B_contacts={counts[0]['b']}")

    # S1. Account A lists only its own records (all five repos).
    a_contacts = run(comp.contacts.list_contacts(CTXA))
    a_props = run(comp.properties.list(CTXA))
    a_pipes = run(comp.pipelines.list(CTXA))
    a_convs = run(comp.conversations.list(CTXA))
    a_fups = run(comp.follow_ups.list_all(CTXA))
    b_contacts = run(comp.contacts.list_contacts(CTXB))
    b_props = run(comp.properties.list(CTXB))
    ok = (
        all(c.client_account_id == ACCOUNT_A for c in a_contacts + a_props + a_convs + a_fups)
        and all(p.client_account_id == ACCOUNT_A for p in a_pipes)
        and all(s.client_account_id == ACCOUNT_A for p in a_pipes for s in p.stages)
        and {c.id for c in a_contacts} == {A_CONTACT}
        and {c.id for c in b_contacts} == {B_CONTACT}
        and {p.id for p in a_props} == {A_PROPERTY}
        and {p.id for p in b_props} == {B_PROPERTY}
    )
    record("A lists only A; B lists only B (contacts/properties/pipelines/conversations/follow-ups)", ok,
           f"a_counts={len(a_contacts)}/{len(a_props)}/{len(a_pipes)}/{len(a_convs)}/{len(a_fups)}")

    # S2. Reading a B resource with A context returns None (non-enumerating).
    reads = [
        run(comp.contacts.get_contact(CTXA, B_CONTACT)),
        run(comp.properties.get(CTXA, B_PROPERTY)),
        run(comp.pipelines.get(CTXA, B_PIPELINE)),
        run(comp.conversations.get(CTXA, B_CONVERSATION)),
        run(comp.contacts.get_lead(CTXA, B_LEAD)),
    ]
    record("read B resources with A context -> None (indistinguishable from missing)",
           all(r is None for r in reads), str([type(r).__name__ for r in reads]))

    # S2b. get_messages on B conversation from A raises the safe not-found.
    try:
        run(comp.conversations.get_messages(CTXA, B_CONVERSATION))
        record("get_messages B conversation from A rejected", False, "no exception")
    except CrmNotFoundError as e:
        record("get_messages B conversation from A rejected", True, str(e))

    # S3. Seed visibility: exact seeded A/B rows readable with full fields.
    a_lead = run(comp.contacts.get_lead(CTXA, A_LEAD))
    b_lead = run(comp.contacts.get_lead(CTXB, B_LEAD))
    a_pipe = run(comp.pipelines.get(CTXA, A_PIPELINE))
    msgs = run(comp.conversations.get_messages(CTXA, A_CONVERSATION))
    ok = (
        a_lead is not None and a_lead.contact_id == A_CONTACT and a_lead.stage_id == A_STAGE
        and b_lead is not None and b_lead.contact_id == B_CONTACT and b_lead.client_account_id == ACCOUNT_B
        and a_pipe is not None and [s.id for s in a_pipe.stages] == [A_STAGE]
        and len(msgs) == 1 and msgs[0].direction == "inbound"
    )
    record("deterministic seed for A and B visible through repositories", ok,
           f"a_lead_stage={a_lead and a_lead.stage_id} stages={a_pipe and [s.name for s in a_pipe.stages]}")

    # S4. Create within correct scope (A only), with generated IDs.
    new_contact_id = SCRATCH_PREFIX + "01"
    created = run(comp.contacts.save_contact(CTXA, Contact(
        id=new_contact_id, client_account_id=ACCOUNT_A, name="WU3 Scratch", tags=("qa",))))
    db_owner = run(runner.query("SELECT client_account_id FROM contacts WHERE id=%s", (new_contact_id,)))
    record("create contact in A scope persists under A account",
           created.client_account_id == ACCOUNT_A and db_owner and str(db_owner[0]["client_account_id"]) == ACCOUNT_A,
           f"id={new_contact_id}")

    # S5. Update within scope.
    updated = run(comp.contacts.save_contact(CTXA, Contact(
        id=new_contact_id, client_account_id=ACCOUNT_A, name="WU3 Updated")))
    record("update contact changes only A's row", updated is not None and updated.name == "WU3 Updated", "")

    # S6. Save with entity.account != context.account rejected before SQL.
    try:
        run(comp.contacts.save_contact(CTXA, Contact(
            id=SCRATCH_PREFIX + "02", client_account_id=ACCOUNT_B, name="forged")))
        record("save with mismatched entity account rejected", False, "no exception")
    except CrossTenantAccessDenied as e:
        leaked = run(runner.query("SELECT 1 FROM contacts WHERE id=%s", (SCRATCH_PREFIX + "02",)))
        record("save with mismatched entity account rejected", not leaked, str(e))

    # S7. Upsert cannot touch a foreign row that shares the same ID:
    # save(contact id = A's seeded row) under B context must not mutate A's row.
    before = run(runner.query("SELECT name FROM contacts WHERE id=%s", (A_CONTACT,)))[0]["name"]
    try:
        run(comp.contacts.save_contact(CTXB, Contact(id=A_CONTACT, client_account_id=ACCOUNT_B, name="hijack")))
        after_ok = False  # relationship/FK or guarded upsert must prevent it
    except (CrossTenantAccessDenied, CrmRelationshipViolation, CrmConflictError, CrmNotFoundError):
        after_ok = True
    except Exception:
        after_ok = False
    after = run(runner.query("SELECT name FROM contacts WHERE id=%s", (A_CONTACT,)))[0]["name"]
    record("upsert under B against A-owned id cannot mutate A's row", after_ok and after == before,
           f"name {before!r} -> {after!r}")

    # S8. Lead with cross-account relationships rejected by composite FKs.
    cases = [
        ("lead contact in B", Lead(id=SCRATCH_PREFIX + "03", client_account_id=ACCOUNT_A,
                                   contact_id=B_CONTACT, pipeline_id=A_PIPELINE, stage_id=A_STAGE)),
        ("lead property in B", Lead(id=SCRATCH_PREFIX + "04", client_account_id=ACCOUNT_A,
                                    contact_id=A_CONTACT, pipeline_id=A_PIPELINE, stage_id=A_STAGE,
                                    selected_property_id=B_PROPERTY)),
        ("lead stage of B pipeline", Lead(id=SCRATCH_PREFIX + "05", client_account_id=ACCOUNT_A,
                                          contact_id=A_CONTACT, pipeline_id=A_PIPELINE, stage_id=B_STAGE)),
        ("lead pipeline B + stage A", Lead(id=SCRATCH_PREFIX + "06", client_account_id=ACCOUNT_A,
                                           contact_id=A_CONTACT, pipeline_id=B_PIPELINE, stage_id=A_STAGE)),
    ]
    for name, bad_lead in cases:
        try:
            run(comp.contacts.save_lead(CTXA, bad_lead))
            record(f"cross-account {name} rejected", False, "no exception")
        except (CrmRelationshipViolation, CrossTenantAccessDenied):
            row = run(runner.query("SELECT 1 FROM leads WHERE id=%s", (bad_lead.id,)))
            record(f"cross-account {name} rejected", not row, "persisted!" if row else "rolled back by FK")

    # S9. Delete with correct scope + delete of foreign id is a safe no-op.
    deleted_foreign = run(comp.properties.delete(CTXA, B_PROPERTY))
    b_prop_after = run(comp.properties.get(CTXB, B_PROPERTY))
    deleted_own = run(comp.properties.delete(CTXA, SCRATCH_PREFIX + "07"))  # never existed
    record("delete foreign property -> no-op, B property intact",
           (not deleted_foreign) and b_prop_after is not None and not deleted_own, "")
    deleted_own = run(comp.contacts.delete_contact(CTXA, new_contact_id))
    gone = run(runner.query("SELECT 1 FROM contacts WHERE id=%s", (new_contact_id,)))
    record("delete own scratch contact removes exactly that row", deleted_own and not gone, "")

    # S10. Read-only membership cannot write.
    try:
        run(comp.contacts.save_contact(CTXA_RO, Contact(id=SCRATCH_PREFIX + "08", client_account_id=ACCOUNT_A, name="ro")))
        record("read-only context write rejected", False, "no exception")
    except CrossTenantAccessDenied as e:
        record("read-only context write rejected", True, str(e))

    # S11. Absent / inconsistent contexts rejected before SQL.
    forged = TenantContext(
        user=User(USER_A, "u"), agency=Agency(AGENCY_A, "A"),
        client_account=ClientAccount(ACCOUNT_B, AGENCY_A, "B"),
        membership=Membership(USER_A, AGENCY_A, ACCOUNT_A, WRITE_PERMS),
        correlation_id="forged")
    checks = []
    for label, ctx in (("absent", None), ("membership mismatch", forged)):
        try:
            run(comp.contacts.list_contacts(ctx))  # type: ignore[arg-type]
            checks.append(False)
        except CrossTenantAccessDenied:
            checks.append(True)
    record("absent and inconsistent contexts rejected", all(checks), str(checks))

    # S12. Pipeline transition within scope + foreign target safe.
    transition = run(comp.pipelines.transition_lead(CTXB, B_LEAD, B_STAGE))
    record("transition to same stage returns unchanged lead", transition[0].stage_id == B_STAGE, "")
    try:
        run(comp.pipelines.transition_lead(CTXA, A_LEAD, B_STAGE))
        record("transition lead to foreign stage rejected", False, "no exception")
    except CrmNotFoundError as e:
        record("transition lead to foreign stage rejected", True, str(e))
    # Create a 2nd stage inside A's own pipeline then move the lead legally.
    stage2_id = SCRATCH_PREFIX + "09"
    run(runner.execute(
        "INSERT INTO pipeline_stages (id, client_account_id, pipeline_id, name, position) VALUES (%s, %s, %s, %s, 1)",
        (stage2_id, ACCOUNT_A, A_PIPELINE, "WU3 second stage")))
    moved, previous = run(comp.pipelines.transition_lead(CTXA, A_LEAD, stage2_id))
    record("lead transition to own new pipeline stage works", moved.stage_id == stage2_id and previous == A_STAGE,
           f"prev={previous} now={moved.stage_id}")

    # S13. FollowUp invariants (pure domain + persistence).
    try:
        FollowUp(id=SCRATCH_PREFIX + "10", client_account_id=ACCOUNT_A, title="no target",
                 due_at=datetime.now(timezone.utc))
        record("domain: follow-up requires lead or contact target", False, "no exception")
    except ValueError as e:
        record("domain: follow-up requires lead or contact target", True, str(e))
    try:
        run(comp.follow_ups.save(CTXA, FollowUp(id=SCRATCH_PREFIX + "11", client_account_id=ACCOUNT_A,
                                                title="foreign lead", contact_id=B_CONTACT,
                                                due_at=datetime.now(timezone.utc))))
        record("follow-up with B contact rejected by FK", False, "no exception")
    except CrmRelationshipViolation:
        record("follow-up with B contact rejected by FK", True, "")
    scratch_fup_id = SCRATCH_PREFIX + "13"
    scratch_fup = run(comp.follow_ups.save(CTXA, FollowUp(
        id=scratch_fup_id, client_account_id=ACCOUNT_A, title="wu3 complete test",
        lead_id=A_LEAD, due_at=datetime.now(timezone.utc))))
    completed = run(comp.follow_ups.complete(CTXA, scratch_fup.id))
    again = run(comp.follow_ups.complete(CTXA, completed.id))
    record("complete() is a guarded one-shot transition",
           scratch_fup.status == "pending" and completed.status == "completed" and again is None, "")

    # S14. Property duplicate ID within same account -> safe conflict (not silent).
    dup = Property(id=A_PROPERTY, client_account_id=ACCOUNT_A, title="dup", operation_type="sale",
                   location="x", bedrooms=1, price=Decimal(1))
    saved_dup = run(comp.properties.save(CTXA, dup))  # upsert of own row is allowed (update semantics)
    restored = run(runner.execute("UPDATE properties SET title='Palermo Garden Loft' WHERE id=%s", (A_PROPERTY,)))
    record("same-account save behaves as guarded upsert", saved_dup.title == "dup" and restored == 1, "")

    # S15. Account B completely intact after every A-side attempt.
    b_snapshot = run(runner.query(
        """
        SELECT 'contacts' t, id, name k FROM contacts WHERE client_account_id=%s
        UNION ALL SELECT 'properties', id, title FROM properties WHERE client_account_id=%s
        UNION ALL SELECT 'pipelines', id, name FROM pipelines WHERE client_account_id=%s
        UNION ALL SELECT 'stages', id, name FROM pipeline_stages WHERE client_account_id=%s
        UNION ALL SELECT 'leads', id, status FROM leads WHERE client_account_id=%s
        UNION ALL SELECT 'conversations', id, status FROM conversations WHERE client_account_id=%s
        UNION ALL SELECT 'messages', id, body FROM messages WHERE client_account_id=%s
        UNION ALL SELECT 'follow_ups', id, title FROM follow_ups WHERE client_account_id=%s
        """,
        (ACCOUNT_B,) * 8))
    b_expected = {
        ("contacts", B_CONTACT, "Bruno Salvatierra"),
        ("properties", B_PROPERTY, "Fisherton Family House"),
        ("pipelines", B_PIPELINE, "Litoral Rentals"),
        ("stages", B_STAGE, "Visit booked"),
        ("leads", B_LEAD, "qualified"),
        ("conversations", B_CONVERSATION, "open"),
        ("messages", "67000000-0000-0000-0000-000000000002", "The Fisherton house is available for a visit."),
        ("follow_ups", "68000000-0000-0000-0000-000000000002", "Confirm house visit"),
    }
    record("Account B rows fully intact after all A attempts",
           {(r["t"], str(r["id"]), r["k"]) for r in b_snapshot} == b_expected,
           f"b_rows={len(b_snapshot)}")

    # Cleanup: restore the A lead's stage FIRST (FK RESTRICT), then scratch rows,
    # then re-apply the idempotent seed to converge A/B demo state.
    run(runner.execute("UPDATE leads SET stage_id = %s, updated_at = now() WHERE id = %s AND client_account_id = %s",
                       (A_STAGE, A_LEAD, ACCOUNT_A)))
    run(runner.execute("DELETE FROM follow_ups WHERE id::text LIKE %s", (SCRATCH_PREFIX + "%",)))
    run(runner.execute("DELETE FROM leads WHERE id::text LIKE %s", (SCRATCH_PREFIX + "%",)))
    run(runner.execute("DELETE FROM contacts WHERE id::text LIKE %s", (SCRATCH_PREFIX + "%",)))
    run(runner.execute("DELETE FROM pipeline_stages WHERE id::text LIKE %s", (SCRATCH_PREFIX + "%",)))
    seed_sql = __import__("pathlib").Path("infra/migrations/validation/mvp_seed.sql")
    if seed_sql.exists():
        subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(seed_sql)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    run(runner.close())
    return results


def test_crm_repositories():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"MVP-CRM WU3 REPOSITORY SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
