"""Pipeline commands WU19 units (change: commercial-pipelines, Phase 1).

Runs against production_runtime_mvp_wu18 (SHARED, migrated 001-011, fully
seeded; NOT migrated further). Every effect goes through the new commands;
probe pipelines/stages are deleted before finishing and the run's own
audit/outbox/idempotency rows are removed, so the base is left clean.
Pre/post counts prove it.

Covers: position swap oracle, append-at-end, reorder edge no-op success,
delete-blocked-with-leads (seeded stage WITH a lead), delete-empty-succeeds,
rename warning matrix, duplicate pipeline names allowed, idempotent
double-execution, forged/cross-account ids denied, blank-name/bad-direction
rejections.
"""

from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.application.crm_commands_wu5 import (
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.application.pipeline_commands_wu19 import (
    CreatePipelineCommand,
    CreateStageCommand,
    DeleteStageCommand,
    RenamePipelineCommand,
    RenameStageCommand,
    ReorderStageCommand,
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
# Seeded stage WITH a lead (account A) + one cross-account stage (account B).
SEEDED_STAGE_WITH_LEAD = "64000000-0000-0000-0000-000000000001"
FOREIGN_STAGE = "64000000-0000-0000-0000-000000000002"
GHOST_PIPELINE = "0000dead-0000-4000-8000-000000000001"
GHOST_STAGE = "0000dead-0000-4000-8000-000000000002"

RUN = uuid4().hex[:8]


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=f"wu19-{RUN}-{tag}",
    )


def _key(tag: str) -> str:
    return f"console:{ACCOUNT_A}:pipeline:wu19-{RUN}-{tag}"


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    async def _run() -> None:
        cfg = load_database_configuration()
        assert cfg.get("dbname") == "production_runtime_mvp_wu18", (
            f"refusing to run on {cfg.get('dbname')}")
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        create = CreatePipelineCommand(dict(cfg))
        rename_pipe = RenamePipelineCommand(dict(cfg))
        create_stage = CreateStageCommand(dict(cfg))
        rename_stage = RenameStageCommand(dict(cfg))
        move = ReorderStageCommand(dict(cfg))
        delete = DeleteStageCommand(dict(cfg))
        created_pipes: list[str] = []

        async def counts() -> dict[str, int]:
            out = {}
            queries: list[tuple[str, str, tuple]] = [
                ("pipelines", "SELECT count(*) AS n FROM pipelines", ()),
                ("stages", "SELECT count(*) AS n FROM pipeline_stages", ()),
                ("definitions",
                 "SELECT count(*) AS n FROM custom_field_definitions", ()),
                ("mine", "SELECT count(*) AS n FROM audit_records "
                         "WHERE correlation_id LIKE %s", (f"wu19-{RUN}-%",)),
            ]
            for label, sql, params in queries:
                rows = await runner.query(sql, params)
                out[label] = int(rows[0]["n"])
            return out

        async def cleanup() -> None:
            if created_pipes:
                await runner.execute(
                    "DELETE FROM pipeline_stages WHERE pipeline_id = ANY(%s)",
                    (created_pipes,))
                await runner.execute(
                    "DELETE FROM pipelines WHERE id = ANY(%s)",
                    (created_pipes,))
                created_pipes.clear()
            await runner.execute(
                "DELETE FROM outbox_events WHERE correlation_id LIKE %s",
                (f"wu19-{RUN}-%",))
            await runner.execute(
                "DELETE FROM audit_records WHERE correlation_id LIKE %s",
                (f"wu19-{RUN}-%",))
            await runner.execute(
                "DELETE FROM command_idempotency WHERE idempotency_key LIKE %s",
                (f"%wu19-{RUN}-%",))

        pre = await counts()
        record("pre pipelines==2", pre["pipelines"] == 2, f"{pre}")
        record("pre stages==4", pre["stages"] == 4, f"{pre}")
        try:
            # T1 create pipeline + duplicate names allowed.
            r1 = await create.execute_create(
                _ctx(ACCOUNT_A, "t1"), name=f"WU19 Probe {RUN}",
                idempotency_key=_key("t1"))
            created_pipes.append(r1.payload["resource_id"])
            r1b = await create.execute_create(
                _ctx(ACCOUNT_A, "t1b"), name=f"WU19 Probe {RUN}",
                idempotency_key=_key("t1b"))
            created_pipes.append(r1b.payload["resource_id"])
            dupes = await runner.query(
                "SELECT count(*) AS n FROM pipelines WHERE client_account_id = %s "
                "AND name = %s", (ACCOUNT_A, f"WU19 Probe {RUN}"))
            record("duplicate pipeline names allowed",
                   int(dupes[0]["n"]) == 2 and r1.payload["resource_id"] != r1b.payload["resource_id"],
                   f"count={dupes[0]['n']}")
            pipe = r1.payload["resource_id"]

            # T2 blank pipeline name rejected; forged rename denied.
            try:
                await create.execute_create(
                    _ctx(ACCOUNT_A, "t2"), name="  ", idempotency_key=_key("t2"))
                record("blank pipeline name rejected", False, "no error raised")
            except CrmCommandValidationError:
                record("blank pipeline name rejected", True, "")
            try:
                await rename_pipe.execute_rename(
                    _ctx(ACCOUNT_A, "t2f"), pipeline_id=GHOST_PIPELINE,
                    name="X", idempotency_key=_key("t2f"))
                record("forged pipeline rename denied", False, "no error raised")
            except CrmCommandDenied as error:
                record("forged pipeline rename denied",
                       error.reason == "pipeline_not_found_in_scope", error.reason)
            rn = await rename_pipe.execute_rename(
                _ctx(ACCOUNT_A, "t2r"), pipeline_id=pipe,
                name=f"WU19 Probe Renamed {RUN}", idempotency_key=_key("t2r"))
            record("pipeline rename succeeds",
                   rn.payload["name"] == f"WU19 Probe Renamed {RUN}"
                   and rn.replayed is False, f"{rn.payload}")

            # T3 append-at-end: three stages land at 0,1,2.
            s_ids = []
            for i, nm in enumerate(("WU19 Alfa", "WU19 Beta", "WU19 Gamma")):
                out = await create_stage.execute_create_stage(
                    _ctx(ACCOUNT_A, f"t3-{i}"), pipeline_id=pipe, name=nm,
                    idempotency_key=_key(f"t3-{i}"))
                s_ids.append(out.payload["resource_id"])
                record(f"stage {nm} appended at {i}",
                       out.payload["position"] == i, f"{out.payload}")
            try:
                await create_stage.execute_create_stage(
                    _ctx(ACCOUNT_A, "t3dup"), pipeline_id=pipe, name="WU19 Alfa",
                    idempotency_key=_key("t3dup"))
                record("duplicate stage name denied", False, "no error raised")
            except CrmCommandDenied as error:
                record("duplicate stage name denied",
                       error.reason == "stage_name_already_exists", error.reason)
            try:
                await create_stage.execute_create_stage(
                    _ctx(ACCOUNT_A, "t3ghost"), pipeline_id=GHOST_PIPELINE,
                    name="Ghost", idempotency_key=_key("t3ghost"))
                record("create stage on forged pipeline denied", False, "no error")
            except CrmCommandDenied as error:
                record("create stage on forged pipeline denied",
                       error.reason == "pipeline_not_found_in_scope", error.reason)

            # T4 swap oracle: middle pair exchanges, third intact, dense 0..2.
            mv = await move.execute_move(
                _ctx(ACCOUNT_A, "t4"), stage_id=s_ids[0], direction="down",
                idempotency_key=_key("t4"))
            rows = await runner.query(
                "SELECT id, position FROM pipeline_stages "
                "WHERE pipeline_id = %s ORDER BY position", (pipe,))
            pos = {str(r["id"]): int(r["position"]) for r in rows}
            record("swap middle pair exchanged, third intact",
                   mv.payload["moved"] is True and pos[s_ids[0]] == 1
                   and pos[s_ids[1]] == 0 and pos[s_ids[2]] == 2
                   and sorted(pos.values()) == [0, 1, 2], f"{pos}")
            back = await move.execute_move(
                _ctx(ACCOUNT_A, "t4b"), stage_id=s_ids[0], direction="up",
                idempotency_key=_key("t4b"))
            rows = await runner.query(
                "SELECT id, position FROM pipeline_stages "
                "WHERE pipeline_id = %s ORDER BY position", (pipe,))
            pos = {str(r["id"]): int(r["position"]) for r in rows}
            record("swap back restores order",
                   back.payload["moved"] is True and pos[s_ids[0]] == 0
                   and pos[s_ids[1]] == 1 and pos[s_ids[2]] == 2, f"{pos}")

            # T5 edges: already-first up / already-last down = no-op success.
            e1 = await move.execute_move(
                _ctx(ACCOUNT_A, "t5a"), stage_id=s_ids[0], direction="up",
                idempotency_key=_key("t5a"))
            e2 = await move.execute_move(
                _ctx(ACCOUNT_A, "t5b"), stage_id=s_ids[2], direction="down",
                idempotency_key=_key("t5b"))
            record("already-first up is no-op success",
                   e1.payload["moved"] is False and e1.payload["position"] == 0,
                   f"{e1.payload}")
            record("already-last down is no-op success",
                   e2.payload["moved"] is False and e2.payload["position"] == 2,
                   f"{e2.payload}")
            try:
                await move.execute_move(
                    _ctx(ACCOUNT_A, "t5bad"), stage_id=s_ids[0],
                    direction="sideways", idempotency_key=_key("t5bad"))
                record("bad direction rejected", False, "no error raised")
            except CrmCommandValidationError:
                record("bad direction rejected", True, "")
            for label, ctx, sid in (
                ("forged stage move denied",
                 _ctx(ACCOUNT_A, "t5g"), GHOST_STAGE),
                ("cross-account stage move denied",
                 _ctx(ACCOUNT_A, "t5x"), FOREIGN_STAGE),
            ):
                try:
                    await move.execute_move(
                        ctx, stage_id=sid, direction="up",
                        idempotency_key=_key(label))
                    record(label, False, "no error raised")
                except CrmCommandDenied as error:
                    record(label, error.reason == "stage_not_found_in_scope",
                           error.reason)

            # T6 rename warning matrix (warn when OLD or NEW name is agent-known).
            w1 = await rename_stage.execute_rename_stage(
                _ctx(ACCOUNT_A, "t6a"), stage_id=s_ids[0],
                name="WU19 Delta", idempotency_key=_key("t6a"))
            w2 = await rename_stage.execute_rename_stage(
                _ctx(ACCOUNT_A, "t6b"), stage_id=s_ids[0],
                name="Calificado", idempotency_key=_key("t6b"))
            w3 = await rename_stage.execute_rename_stage(
                _ctx(ACCOUNT_A, "t6c"), stage_id=s_ids[0],
                name="WU19 Epsilon", idempotency_key=_key("t6c"))
            record("unknown->unknown silent",
                   w1.payload["agent_name_warning"] is False, f"{w1.payload}")
            record("unknown->known warns",
                   w2.payload["agent_name_warning"] is True, f"{w2.payload}")
            record("known->unknown warns",
                   w3.payload["agent_name_warning"] is True, f"{w3.payload}")
            try:
                await rename_stage.execute_rename_stage(
                    _ctx(ACCOUNT_A, "t6blank"), stage_id=s_ids[0], name=" ",
                    idempotency_key=_key("t6blank"))
                record("blank stage rename rejected", False, "no error raised")
            except CrmCommandValidationError:
                record("blank stage rename rejected", True, "")
            try:
                await rename_stage.execute_rename_stage(
                    _ctx(ACCOUNT_A, "t6dup"), stage_id=s_ids[0],
                    name="WU19 Beta", idempotency_key=_key("t6dup"))
                record("rename onto sibling name denied", False, "no error")
            except CrmCommandDenied as error:
                record("rename onto sibling name denied",
                       error.reason == "stage_name_already_exists", error.reason)

            # T7 delete blocked with leads (seeded stage) — zero effects.
            before = await runner.query(
                "SELECT id, position FROM pipeline_stages WHERE id = %s",
                (SEEDED_STAGE_WITH_LEAD,))
            try:
                await delete.execute_delete(
                    _ctx(ACCOUNT_A, "t7"), stage_id=SEEDED_STAGE_WITH_LEAD,
                    idempotency_key=_key("t7"))
                record("delete stage with leads denied", False, "no error raised")
            except CrmCommandDenied as error:
                after = await runner.query(
                    "SELECT id, position FROM pipeline_stages WHERE id = %s",
                    (SEEDED_STAGE_WITH_LEAD,))
                record("delete stage with leads denied",
                       error.reason == "stage_has_leads"
                       and int(error.metadata.get("lead_count", 0)) >= 1
                       and len(after) == 1
                       and int(after[0]["position"]) == int(before[0]["position"]),
                       f"reason={error.reason} meta={error.metadata}")
            try:
                await delete.execute_delete(
                    _ctx(ACCOUNT_A, "t7x"), stage_id=FOREIGN_STAGE,
                    idempotency_key=_key("t7x"))
                record("cross-account stage delete denied", False, "no error")
            except CrmCommandDenied as error:
                record("cross-account stage delete denied",
                       error.reason == "stage_not_found_in_scope", error.reason)

            # T8 delete empty succeeds.
            tmp = await create_stage.execute_create_stage(
                _ctx(ACCOUNT_A, "t8"), pipeline_id=pipe, name="WU19 Tmp",
                idempotency_key=_key("t8"))
            gone = await delete.execute_delete(
                _ctx(ACCOUNT_A, "t8d"), stage_id=tmp.payload["resource_id"],
                idempotency_key=_key("t8d"))
            missing = await runner.query(
                "SELECT count(*) AS n FROM pipeline_stages WHERE id = %s",
                (tmp.payload["resource_id"],))
            record("delete empty stage succeeds",
                   gone.payload["deleted"] is True and int(missing[0]["n"]) == 0,
                   f"{gone.payload}")

            # T9 idempotent double-execution: same key twice, one effect.
            k9 = _key("t9")
            d1 = await create_stage.execute_create_stage(
                _ctx(ACCOUNT_A, "t9a"), pipeline_id=pipe, name="WU19 Replay",
                idempotency_key=k9)
            d2 = await create_stage.execute_create_stage(
                _ctx(ACCOUNT_A, "t9b"), pipeline_id=pipe, name="WU19 Replay",
                idempotency_key=k9)
            reps = await runner.query(
                "SELECT count(*) AS n FROM pipeline_stages "
                "WHERE pipeline_id = %s AND name = %s", (pipe, "WU19 Replay"))
            record("replayed create returns stored payload, one row",
                   d2.replayed is True and d2.payload == d1.payload
                   and int(reps[0]["n"]) == 1, f"replayed={d2.replayed}")
            k9r = _key("t9r")
            m1 = await move.execute_move(
                _ctx(ACCOUNT_A, "t9c"), stage_id=s_ids[1], direction="down",
                idempotency_key=k9r)
            m2 = await move.execute_move(
                _ctx(ACCOUNT_A, "t9d"), stage_id=s_ids[1], direction="down",
                idempotency_key=k9r)
            rows = await runner.query(
                "SELECT id, position FROM pipeline_stages "
                "WHERE pipeline_id = %s ORDER BY position", (pipe,))
            pos = [int(r["position"]) for r in rows]
            record("replayed reorder returns stored payload, zero extra effects",
                   m2.replayed is True and m2.payload == m1.payload
                   and sorted(pos) == list(range(len(pos))), f"{pos}")
        finally:
            await cleanup()

        post = await counts()
        record("post pipelines==2 (clean)", post["pipelines"] == 2, f"{post}")
        record("post stages==4 (clean)", post["stages"] == 4, f"{post}")
        record("post my-audit==0 (clean)", post["mine"] == 0, f"{post}")

    asyncio.run(_run())
    return results


def test_pipeline_commands_wu19():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"PIPELINE-COMMANDS-WU19 UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)
