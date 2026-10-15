"""Agent signal-binding units (change: agent-custom-field-writes, WU18).

Runs against `production_runtime_mvp_wu18` (disposable: runner 001-011 +
mvp_seed + 005 + 008 + 009 + 010 + 011; refuses any other base): binding CRUD
+ unknown signal/field rejection + cascade on field delete + the D4
allowlist-write close (fixed U custom names, unknown custom name 400) +
pre-011 tolerant read + the Phase-4 policy AUTO pin. Every probe row is
removed in try/finally and the seeded allowlist snapshot is restored
byte-identically, so the shared wu18 base is left clean. Idempotency keys
carry a per-run uuid so re-runs never replay a prior command.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from api.adapters.postgres_agent_console import (
    default_verdict,
    load_field_allowlist,
    load_signal_bindings,
)
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.application.agent_commands_wu16 import UpdateFieldAllowlistCommand
from api.application.agent_policy import PolicySignals, evaluate
from api.application.agent_signal_bindings_commands import (
    CreateSignalBindingCommand,
    DeleteSignalBindingCommand,
)
from api.application.configuration_commands_wu17 import (
    CreateCustomFieldDefinitionCommand,
    DeleteCustomFieldDefinitionCommand,
)
from api.application.crm_commands_wu5 import (
    CrmCommandDenied,
    CrmCommandValidationError,
)
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
PERMS = frozenset({"client_account:read", "client_account:write"})


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=tag,
    )


class _BoomRunner:
    """Pre-011 stand-in: every read raises (no bindings table)."""

    async def query(self, *args, **kwargs):
        raise Exception("relation agent_signal_bindings does not exist")


def _fake_ctx():
    return SimpleNamespace(
        client_account=SimpleNamespace(id=ACCOUNT_B, agency_id=AGENCY),
        agency=SimpleNamespace(id=AGENCY),
        membership=SimpleNamespace(client_account_id=ACCOUNT_B,
                                   agency_id=AGENCY),
    )


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    async def _run() -> None:
        cfg = load_database_configuration()
        assert cfg.get("dbname") == "production_runtime_mvp_wu18", (
            f"refusing to run on {cfg.get('dbname')}")
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        tag = str(uuid4())
        ctx_b = _ctx(ACCOUNT_B, "bindings")
        ctx_a = _ctx(ACCOUNT_A, "bindings-a")
        create_def = CreateCustomFieldDefinitionCommand(dict(cfg))
        delete_def = DeleteCustomFieldDefinitionCommand(dict(cfg))
        bind = CreateSignalBindingCommand(dict(cfg))
        unbind = DeleteSignalBindingCommand(dict(cfg))
        allow = UpdateFieldAllowlistCommand(dict(cfg))

        # Seeded allowlist snapshot (byte-identical restore in finally).
        seed_rows = await runner.query(
            "SELECT id, column_name, field_definition_id, demo_seed_key "
            "FROM agent_field_allowlist WHERE client_account_id = %s "
            "ORDER BY column_name",
            (ACCOUNT_B,),
        )
        created_defs_b: list[str] = []
        created_defs_a: list[str] = []

        async def cleanup() -> None:
            await runner.execute(
                "DELETE FROM agent_signal_bindings "
                "WHERE client_account_id = %s", (ACCOUNT_B,))
            await runner.execute(
                "DELETE FROM agent_signal_bindings "
                "WHERE client_account_id = %s", (ACCOUNT_A,))
            await runner.execute(
                "DELETE FROM agent_field_allowlist "
                "WHERE client_account_id = %s", (ACCOUNT_B,))
            for row in seed_rows:
                await runner.execute(
                    "INSERT INTO agent_field_allowlist "
                    "(id, client_account_id, column_name, "
                    "field_definition_id, demo_seed_key) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (row["id"], ACCOUNT_B, row["column_name"],
                     row["field_definition_id"], row["demo_seed_key"]),
                )
            for fid in created_defs_b:
                await runner.execute(
                    "DELETE FROM lead_custom_field_values "
                    "WHERE client_account_id = %s AND field_id = %s",
                    (ACCOUNT_B, fid))
                await runner.execute(
                    "DELETE FROM custom_field_definitions "
                    "WHERE id = %s AND client_account_id = %s",
                    (fid, ACCOUNT_B))
            for fid in created_defs_a:
                await runner.execute(
                    "DELETE FROM custom_field_definitions "
                    "WHERE id = %s AND client_account_id = %s",
                    (fid, ACCOUNT_A))

        try:
            phone_name = f"telefono-{tag}"
            out = await create_def.execute_define(
                ctx_b, name=phone_name, type="text",
                idempotency_key=f"bind-def-phone-{tag}")
            phone_id = out.payload["resource_id"]
            created_defs_b.append(phone_id)
            email_name = f"correo-{tag}"
            out = await create_def.execute_define(
                ctx_b, name=email_name, type="text",
                idempotency_key=f"bind-def-email-{tag}")
            email_id = out.payload["resource_id"]
            created_defs_b.append(email_id)

            # B1 create + read round-trip.
            out = await bind.execute_create(
                ctx_b, signal_key="phone", field_definition_id=phone_id,
                idempotency_key=f"bind-create-phone-{tag}")
            bindings = await load_signal_bindings(ctx_b, runner)
            record("B1 binding create round-trips through the read adapter",
                   bindings.get("phone") == phone_id, f"bindings={bindings}")

            # B2 upsert same signal -> new field.
            await bind.execute_create(
                ctx_b, signal_key="phone", field_definition_id=email_id,
                idempotency_key=f"bind-upsert-phone-{tag}")
            bindings = await load_signal_bindings(ctx_b, runner)
            record("B2 upsert moves the signal to the new field",
                   bindings.get("phone") == email_id, f"bindings={bindings}")

            # B3 unknown signal rejected on create + delete.
            unknown_create = unknown_delete = False
            try:
                await bind.execute_create(
                    ctx_b, signal_key="fax", field_definition_id=phone_id,
                    idempotency_key=f"bind-bad-signal-{tag}")
            except CrmCommandValidationError:
                unknown_create = True
            try:
                await unbind.execute_delete(
                    ctx_b, signal_key="fax",
                    idempotency_key=f"bind-del-bad-{tag}")
            except CrmCommandValidationError:
                unknown_delete = True
            record("B3 unknown signal_key rejected (create + delete)",
                   unknown_create and unknown_delete, "")

            # B4 unknown + foreign field rejected (non-enumerating denial).
            ghost = foreign = False
            try:
                await bind.execute_create(
                    ctx_b, signal_key="email",
                    field_definition_id=str(uuid4()),
                    idempotency_key=f"bind-ghost-{tag}")
            except CrmCommandDenied as error:
                ghost = error.reason == "custom_field_not_found_in_scope"
            out = await create_def.execute_define(
                ctx_a, name=f"ajeno-{tag}", type="text",
                idempotency_key=f"bind-def-foreign-{tag}")
            created_defs_a.append(out.payload["resource_id"])
            try:
                await bind.execute_create(
                    ctx_b, signal_key="email",
                    field_definition_id=out.payload["resource_id"],
                    idempotency_key=f"bind-foreign-{tag}")
            except CrmCommandDenied as error:
                foreign = error.reason == "custom_field_not_found_in_scope"
            record("B4 ghost + cross-account field ids denied",
                   ghost and foreign, "")

            # B5 deleting the bound definition cascades the binding away.
            await bind.execute_create(
                ctx_b, signal_key="barrio", field_definition_id=phone_id,
                idempotency_key=f"bind-barrio-{tag}")
            await delete_def.execute_delete(
                ctx_b, field_id=phone_id,
                idempotency_key=f"bind-del-def-{tag}")
            created_defs_b.remove(phone_id)
            bindings = await load_signal_bindings(ctx_b, runner)
            record("B5 definition delete cascades its bindings",
                   "barrio" not in bindings, f"bindings={bindings}")

            # B6 allowlist accepts fixed + defined custom names (D4 close).
            await allow.execute_update(
                ctx_b, allowed=["stage_id", "status", email_name],
                idempotency_key=f"console:{ACCOUNT_B}:allowlist:{tag}")
            names = await load_field_allowlist(ctx_b, runner)
            stored = await runner.query(
                "SELECT column_name, field_definition_id "
                "FROM agent_field_allowlist WHERE client_account_id = %s",
                (ACCOUNT_B,))
            custom_rows = [r for r in stored
                           if r["column_name"] == email_name]
            record("B6 allowlist stores custom name + field_definition_id",
                   email_name in names and len(custom_rows) == 1
                   and str(custom_rows[0]["field_definition_id"]) == email_id,
                   f"names={sorted(names)}")

            # B7 undefined custom name fails closed 400.
            failed = False
            try:
                await allow.execute_update(
                    ctx_b, allowed=["stage_id", f"fantasma-{tag}"],
                    idempotency_key=f"bind-allow-ghost-{tag}")
            except CrmCommandValidationError:
                failed = True
            record("B7 unknown custom allowlist name rejected",
                   failed, "")

            # B8 pre-011 tolerant read.
            tolerant = await load_signal_bindings(_fake_ctx(), _BoomRunner())
            record("B8 missing bindings table reads as {}",
                   tolerant == {}, f"bindings={tolerant}")

            # B9 policy AUTO pin (Phase 4; agent_policy tests stay untouched).
            decision = evaluate(
                PolicySignals(intent="search_properties", matched_count=2),
                ["register_interest", "set_custom_field_value"])
            record("B9 evaluate() auto-executes set_custom_field_value",
                   decision.verdict == "auto"
                   and "set_custom_field_value" in decision.auto_execute_types
                   and default_verdict("set_custom_field_value") == "auto",
                   f"verdict={decision.verdict} "
                   f"auto={sorted(decision.auto_execute_types)}")
        finally:
            await cleanup()

        # B10 base clean: no probe rows leaked, seeded allowlist restored.
        bindings = await load_signal_bindings(ctx_b, runner)
        names = await load_field_allowlist(ctx_b, runner)
        current = await runner.query(
            "SELECT id, column_name, field_definition_id, demo_seed_key "
            "FROM agent_field_allowlist WHERE client_account_id = %s "
            "ORDER BY column_name",
            (ACCOUNT_B,),
        )
        defs = await runner.query(
            "SELECT id FROM custom_field_definitions "
            "WHERE client_account_id = %s", (ACCOUNT_B,))
        restored = (
            len(current) == len(seed_rows)
            and all(dict(r) == dict(s) for r, s in zip(current, seed_rows)))
        record("B10 no bindings/definitions leaked, allowlist restored",
               bindings == {} and defs == [] and restored
               and names == frozenset(r["column_name"] for r in seed_rows),
               f"bindings={bindings} defs={len(defs)} "
               f"names={sorted(names)}")

    asyncio.run(_run())
    return results


def test_agent_signal_bindings():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT-SIGNAL-BINDINGS UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
