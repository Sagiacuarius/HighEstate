"""Configuration custom-field units (change: configuration, WU17).

Phase 3: definition CRUD over `custom_field_definitions`, type coercion /
select-choice validation in `SetLeadCustomFieldValueCommand`, and the
allowlist widening (`load_field_allowlist` returns fixed columns plus defined
custom-field names). Runs against account B and its seeded lead; every
definition/value/allowlist row created here is removed in try/finally so the
wu17 base is left clean. Idempotency keys carry a per-run uuid so re-runs
never replay a prior command.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from api.adapters.postgres_agent_console import load_field_allowlist
from api.adapters.postgres_configuration import (
    load_custom_field_definitions,
    load_custom_field_values,
)
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.application.configuration_commands_wu17 import (
    CreateCustomFieldDefinitionCommand,
    DeleteCustomFieldDefinitionCommand,
    SetLeadCustomFieldValueCommand,
    UpdateCustomFieldDefinitionCommand,
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
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
B_LEAD = "65000000-0000-0000-0000-000000000002"
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
        ctx_b = _ctx(ACCOUNT_B, "cf")
        create_cmd = CreateCustomFieldDefinitionCommand(dict(cfg))
        update_cmd = UpdateCustomFieldDefinitionCommand(dict(cfg))
        delete_cmd = DeleteCustomFieldDefinitionCommand(dict(cfg))
        set_cmd = SetLeadCustomFieldValueCommand(dict(cfg))

        number_name = f"presupuesto-{tag}"
        date_name = f"cierre-{tag}"
        select_name = f"interes-{tag}"
        text_name = f"notas-{tag}"
        created_ids: list[str] = []

        async def cleanup() -> None:
            for fid in created_ids:
                await runner.execute(
                    "DELETE FROM agent_field_allowlist "
                    "WHERE client_account_id = %s AND field_definition_id = %s",
                    (ACCOUNT_B, fid))
                await runner.execute(
                    "DELETE FROM lead_custom_field_values "
                    "WHERE client_account_id = %s AND field_id = %s",
                    (ACCOUNT_B, fid))
                await runner.execute(
                    "DELETE FROM custom_field_definitions "
                    "WHERE id = %s AND client_account_id = %s", (fid, ACCOUNT_B))

        try:
            # CF1 create text definition + read back.
            out = await create_cmd.execute_define(
                ctx_b, name=text_name, type="text",
                idempotency_key=f"cf-create-text-{tag}")
            text_id = out.payload["resource_id"]
            created_ids.append(text_id)
            names = {d["name"] for d in await load_custom_field_definitions(ctx_b, runner)}
            record("CF1 create text definition visible", text_name in names,
                   f"name={text_name!r}")

            # CF2 invalid type and select-without-choices fail closed.
            invalid_type = False
            try:
                await create_cmd.execute_define(
                    ctx_b, name=f"bad-{tag}", type="bool",
                    idempotency_key=f"cf-create-bad-{tag}")
            except CrmCommandValidationError:
                invalid_type = True
            record("CF2 invalid type rejected", invalid_type, "")
            no_choices = False
            try:
                await create_cmd.execute_define(
                    ctx_b, name=f"sel-nc-{tag}", type="select",
                    idempotency_key=f"cf-create-selnc-{tag}")
            except CrmCommandValidationError:
                no_choices = True
            record("CF2 select without choices rejected", no_choices, "")

            # CF3 update renames a definition.
            out = await create_cmd.execute_define(
                ctx_b, name=select_name, type="select",
                choices=["bajo", "medio", "alto"],
                idempotency_key=f"cf-create-sel-{tag}")
            sel_id = out.payload["resource_id"]
            created_ids.append(sel_id)
            renamed = f"interes-v2-{tag}"
            up = await update_cmd.execute_update(
                ctx_b, field_id=sel_id, name=renamed,
                idempotency_key=f"cf-upd-sel-{tag}")
            record("CF3 update renames definition",
                   up.payload["name"] == renamed, f"name={up.payload['name']!r}")
            select_name = renamed

            # CF4 type coercion matrix.
            num_out = await create_cmd.execute_define(
                ctx_b, name=number_name, type="number",
                idempotency_key=f"cf-create-num-{tag}")
            num_id = num_out.payload["resource_id"]
            created_ids.append(num_id)
            date_out = await create_cmd.execute_define(
                ctx_b, name=date_name, type="date",
                idempotency_key=f"cf-create-date-{tag}")
            date_id = date_out.payload["resource_id"]
            created_ids.append(date_id)

            v = await set_cmd.execute_set(
                ctx_b, lead_id=B_LEAD, field_id=num_id, value=195000,
                idempotency_key=f"cf-set-num-{tag}")
            record("CF4 number coerced to numeric string",
                   v.payload["value"] == "195000", f"value={v.payload['value']!r}")
            v = await set_cmd.execute_set(
                ctx_b, lead_id=B_LEAD, field_id=date_id, value="2026-09-21",
                idempotency_key=f"cf-set-date-{tag}")
            record("CF4 date coerced to ISO-8601",
                   v.payload["value"] == "2026-09-21", f"value={v.payload['value']!r}")
            v = await set_cmd.execute_set(
                ctx_b, lead_id=B_LEAD, field_id=text_id, value="  Hola mundo  ",
                idempotency_key=f"cf-set-text-{tag}")
            record("CF4 text trimmed free string",
                   v.payload["value"] == "Hola mundo", f"value={v.payload['value']!r}")
            v = await set_cmd.execute_set(
                ctx_b, lead_id=B_LEAD, field_id=sel_id, value="medio",
                idempotency_key=f"cf-set-sel-{tag}")
            record("CF4 select in choices accepted",
                   v.payload["value"] == "medio", f"value={v.payload['value']!r}")
            sel_bad = False
            try:
                await set_cmd.execute_set(
                    ctx_b, lead_id=B_LEAD, field_id=sel_id, value="maximo",
                    idempotency_key=f"cf-set-selbad-{tag}")
            except CrmCommandValidationError:
                sel_bad = True
            record("CF4 select outside choices rejected", sel_bad, "")

            # CF5 undefined field rejected.
            undef = False
            try:
                await set_cmd.execute_set(
                    ctx_b, lead_id=B_LEAD, field_id=str(uuid4()), value="x",
                    idempotency_key=f"cf-set-undef-{tag}")
            except CrmCommandDenied:
                undef = True
            record("CF5 undefined field rejected", undef, "")

            # CF6 allowlist includes a defined custom field.
            await runner.execute(
                "INSERT INTO agent_field_allowlist "
                "(id, client_account_id, column_name, field_definition_id, "
                "demo_seed_key) VALUES (%s, %s, %s, %s, NULL)",
                (str(uuid4()), ACCOUNT_B, text_name, text_id))
            allow = await load_field_allowlist(ctx_b, runner)
            record("CF6 allowlist includes defined custom field",
                   text_name in allow and "stage_id" in allow and "status" in allow,
                   f"allow={sorted(allow)}")

            # CF7 delete clears values + refs + definition.
            await delete_cmd.execute_delete(
                ctx_b, field_id=sel_id, idempotency_key=f"cf-del-sel-{tag}")
            created_ids.remove(sel_id)
            remaining = {d["name"] for d in await load_custom_field_definitions(ctx_b, runner)}
            values = await load_custom_field_values(ctx_b, runner, B_LEAD)
            record("CF7 delete removes definition + its values",
                   select_name not in remaining
                   and all(v["field_id"] != sel_id for v in values),
                   f"remaining={sorted(remaining)}")
        finally:
            await cleanup()

        # CF8 base clean: no test rows leaked.
        leftover = await load_custom_field_definitions(ctx_b, runner)
        record("CF8 no custom field definitions leaked", leftover == [],
               f"count={len(leftover)}")

    asyncio.run(_run())
    return results


def test_configuration_custom_fields():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"CONFIGURATION-CUSTOM-FIELDS UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
