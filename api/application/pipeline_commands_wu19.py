"""MVP Commercial Demo WU19: advisor pipeline/stage write commands.

Six `CrmCommandBase` subclasses (reserve idempotency -> scoped write under
the pipeline `FOR UPDATE` lock -> audit + outbox -> commit), mirroring the
accepted WU5/WU17 pattern:

- CreatePipelineCommand (`pipeline.create`): name-only (+ optional initial
  stages); duplicate pipeline names per account are ALLOWED (no such UNIQUE
  exists in 004).
- RenamePipelineCommand (`pipeline.rename`): name-only; no agent interaction.
- CreateStageCommand (`pipeline.stage.create`): append-at-end
  (`COALESCE(max(position), -1) + 1`); same-pipeline name UNIQUE backstopped.
- RenameStageCommand (`pipeline.stage.rename`): warn-only agent-name guard.
- ReorderStageCommand (`pipeline.stage.reorder`): up/down temp-slot swap;
  edges are no-op success (`moved: False`).
- DeleteStageCommand (`pipeline.stage.delete`): fail-closed BLOCK when leads
  reference the stage (pre-count denial + `ON DELETE RESTRICT` backstop).

Advisor rows are created with `demo_seed_key = NULL` so seed/reset
marker-pair deletes never touch them. Tenant scope always comes from the
server-derived `TenantContext`; idempotency keys are caller-supplied
(`console:{account}:{entity}:{key}` at the route layer).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg

from api.application.crm_commands_wu5 import (
    CrmCommandBase,
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.tenant_boundary import TenantContext

AGENT_KNOWN_STAGE_NAMES = frozenset(
    {"Calificado", "Simulador: visita calificada", "Visita agendada"}
)

_NAME_UNIQUE_SUFFIX = "pipeline_stages_pipeline_name_unique"


def _require_name(value: Any, label: str = "name") -> str:
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise CrmCommandValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _require_direction(value: Any) -> str:
    if value not in ("up", "down"):
        raise CrmCommandValidationError('direction must be "up" or "down"')
    return value


async def _lock_pipeline(cur: Any, pipeline_id: str, account_id: str) -> dict[str, Any]:
    """Serialize stage ops per pipeline; foreign/missing share one denial."""
    await cur.execute(
        "SELECT id, name FROM pipelines "
        "WHERE id = %s AND client_account_id = %s FOR UPDATE",
        (pipeline_id, account_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise CrmCommandDenied("pipeline_not_found_in_scope")
    return row


async def _get_stage(cur: Any, stage_id: str, account_id: str,
                     for_update: bool = False) -> dict[str, Any]:
    """Fetch one tenant-scoped stage; foreign/missing share one denial."""
    lock = " FOR UPDATE" if for_update else ""
    await cur.execute(
        "SELECT id, pipeline_id, name, position FROM pipeline_stages "
        f"WHERE id = %s AND client_account_id = %s{lock}",
        (stage_id, account_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise CrmCommandDenied("stage_not_found_in_scope")
    return row


async def _ordered_stages(cur: Any, account_id: str,
                          pipeline_id: str) -> list[dict[str, Any]]:
    await cur.execute(
        "SELECT id, name, position FROM pipeline_stages "
        "WHERE client_account_id = %s AND pipeline_id = %s "
        "ORDER BY position, id",
        (account_id, pipeline_id),
    )
    return list(await cur.fetchall())


async def _max_position(cur: Any, account_id: str, pipeline_id: str) -> int:
    await cur.execute(
        "SELECT COALESCE(max(position), -1) AS m FROM pipeline_stages "
        "WHERE client_account_id = %s AND pipeline_id = %s",
        (account_id, pipeline_id),
    )
    row = await cur.fetchone()
    return int(row["m"])


def _unique_denial(error: psycopg.errors.UniqueViolation) -> CrmCommandDenied:
    """Map the two stage UNIQUEs to fail-closed denials (race backstop)."""
    constraint = ""
    try:
        constraint = str((error.diag and error.diag.constraint_name) or "")
    except Exception:
        constraint = ""
    if _NAME_UNIQUE_SUFFIX in constraint or "name" in constraint:
        return CrmCommandDenied("stage_name_already_exists")
    return CrmCommandDenied("stage_position_conflict")


class CreatePipelineCommand(CrmCommandBase):
    """Create an advisor pipeline (+ optional initial stages, `0..n`)."""

    operation = "pipeline.create"
    audit_resource_type = "pipeline"
    outbox_event_type = "PipelineCreated"

    async def execute_create(self, context: TenantContext | None, *,
                             name: Any, stages: Any = None,
                             idempotency_key: str) -> Any:
        clean_name = _require_name(name)
        clean_stages: list[str] | None = None
        if stages is not None:
            if not isinstance(stages, list):
                raise CrmCommandValidationError("stages must be a list of names")
            clean_stages = [_require_name(entry, "stage name") for entry in stages]
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"name": clean_name, "stages": clean_stages},
            name=clean_name, stages=clean_stages,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        pipeline_id = str(uuid4())
        await cur.execute(
            "INSERT INTO pipelines (id, client_account_id, demo_seed_key, name) "
            "VALUES (%s, %s, NULL, %s)",
            (pipeline_id, account_id, intent["name"]),
        )
        created: list[dict[str, Any]] = []
        for position, stage_name in enumerate(intent["stages"] or ()):
            stage_id = str(uuid4())
            try:
                await cur.execute(
                    "INSERT INTO pipeline_stages "
                    "(id, client_account_id, pipeline_id, demo_seed_key, name, position) "
                    "VALUES (%s, %s, %s, NULL, %s, %s)",
                    (stage_id, account_id, pipeline_id, stage_name, position),
                )
            except psycopg.errors.UniqueViolation as error:
                raise _unique_denial(error) from None
            created.append({"stage_id": stage_id, "name": stage_name,
                            "position": position})
        return {"resource_id": pipeline_id, "name": intent["name"],
                "stages": created, "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"name": payload["name"], "stage_count": len(payload["stages"])}


class RenamePipelineCommand(CrmCommandBase):
    """Rename a pipeline; moves no leads/stages by construction."""

    operation = "pipeline.rename"
    audit_resource_type = "pipeline"
    outbox_event_type = "PipelineRenamed"

    async def execute_rename(self, context: TenantContext | None, *,
                             pipeline_id: str, name: Any,
                             idempotency_key: str) -> Any:
        clean_name = _require_name(name)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"pipeline_id": pipeline_id, "name": clean_name},
            pipeline_id=pipeline_id, name=clean_name,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await _lock_pipeline(cur, intent["pipeline_id"], account_id)
        await cur.execute(
            "UPDATE pipelines SET name = %s, updated_at = now() "
            "WHERE id = %s AND client_account_id = %s",
            (intent["name"], intent["pipeline_id"], account_id),
        )
        return {"resource_id": intent["pipeline_id"], "name": intent["name"],
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"name": payload["name"]}


class CreateStageCommand(CrmCommandBase):
    """Append a stage at `max(position) + 1` on a scoped pipeline."""

    operation = "pipeline.stage.create"
    audit_resource_type = "pipeline_stage"
    outbox_event_type = "PipelineStageCreated"

    async def execute_create_stage(self, context: TenantContext | None, *,
                                   pipeline_id: str, name: Any,
                                   idempotency_key: str) -> Any:
        clean_name = _require_name(name)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"pipeline_id": pipeline_id, "name": clean_name},
            pipeline_id=pipeline_id, name=clean_name,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await _lock_pipeline(cur, intent["pipeline_id"], account_id)
        position = await _max_position(cur, account_id, intent["pipeline_id"]) + 1
        stage_id = str(uuid4())
        try:
            await cur.execute(
                "INSERT INTO pipeline_stages "
                "(id, client_account_id, pipeline_id, demo_seed_key, name, position) "
                "VALUES (%s, %s, %s, NULL, %s, %s)",
                (stage_id, account_id, intent["pipeline_id"],
                 intent["name"], position),
            )
        except psycopg.errors.UniqueViolation as error:
            raise _unique_denial(error) from None
        return {"resource_id": stage_id, "pipeline_id": intent["pipeline_id"],
                "name": intent["name"], "position": position,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"pipeline_id": payload["pipeline_id"], "name": payload["name"],
                "position": payload["position"]}


class RenameStageCommand(CrmCommandBase):
    """Rename a stage; the agent-name guard warns only, never denies."""

    operation = "pipeline.stage.rename"
    audit_resource_type = "pipeline_stage"
    outbox_event_type = "PipelineStageRenamed"

    async def execute_rename_stage(self, context: TenantContext | None, *,
                                   stage_id: str, name: Any,
                                   idempotency_key: str) -> Any:
        clean_name = _require_name(name)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"stage_id": stage_id, "name": clean_name},
            stage_id=stage_id, name=clean_name,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        current = await _get_stage(cur, intent["stage_id"], account_id)
        await _lock_pipeline(cur, str(current["pipeline_id"]), account_id)
        old_name = str(current["name"])
        new_name = intent["name"]
        try:
            await cur.execute(
                "UPDATE pipeline_stages SET name = %s, updated_at = now() "
                "WHERE id = %s AND client_account_id = %s",
                (new_name, intent["stage_id"], account_id),
            )
        except psycopg.errors.UniqueViolation as error:
            raise _unique_denial(error) from None
        warning = old_name in AGENT_KNOWN_STAGE_NAMES or new_name in AGENT_KNOWN_STAGE_NAMES
        return {"resource_id": intent["stage_id"], "name": new_name,
                "previous_name": old_name, "agent_name_warning": warning,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"name": payload["name"], "previous_name": payload["previous_name"],
                "agent_name_warning": payload["agent_name_warning"]}


class ReorderStageCommand(CrmCommandBase):
    """Move a stage one step up/down via a temp-slot swap under the pipeline lock."""

    operation = "pipeline.stage.reorder"
    audit_resource_type = "pipeline_stage"
    outbox_event_type = "PipelineStageReordered"

    async def execute_move(self, context: TenantContext | None, *,
                           stage_id: str, direction: Any,
                           idempotency_key: str) -> Any:
        clean_direction = _require_direction(direction)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"stage_id": stage_id, "direction": clean_direction},
            stage_id=stage_id, direction=clean_direction,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        current = await _get_stage(cur, intent["stage_id"], account_id)
        pipeline_id = str(current["pipeline_id"])
        position = int(current["position"])
        await _lock_pipeline(cur, pipeline_id, account_id)
        ordered = await _ordered_stages(cur, account_id, pipeline_id)
        index = next((i for i, row in enumerate(ordered)
                      if str(row["id"]) == intent["stage_id"]), None)
        if index is None:
            raise CrmCommandDenied("stage_not_found_in_scope")
        neighbor = index - 1 if intent["direction"] == "up" else index + 1
        if neighbor < 0 or neighbor >= len(ordered):
            return {"resource_id": intent["stage_id"], "position": position,
                    "moved": False, "correlation_id": context.correlation_id}
        other = ordered[neighbor]
        other_pos = int(other["position"])
        temp = max(int(row["position"]) for row in ordered) + 1
        await cur.execute("UPDATE pipeline_stages SET position = %s, updated_at = now() "
                          "WHERE id = %s AND client_account_id = %s",
                          (temp, intent["stage_id"], account_id))
        await cur.execute("UPDATE pipeline_stages SET position = %s, updated_at = now() "
                          "WHERE id = %s AND client_account_id = %s",
                          (position, str(other["id"]), account_id))
        await cur.execute("UPDATE pipeline_stages SET position = %s, updated_at = now() "
                          "WHERE id = %s AND client_account_id = %s",
                          (other_pos, intent["stage_id"], account_id))
        return {"resource_id": intent["stage_id"], "position": other_pos,
                "moved": True, "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"position": payload["position"], "moved": payload["moved"]}


class DeleteStageCommand(CrmCommandBase):
    """Delete an empty stage; BLOCKs fail-closed when leads reference it."""

    operation = "pipeline.stage.delete"
    audit_resource_type = "pipeline_stage"
    outbox_event_type = "PipelineStageDeleted"

    async def execute_delete(self, context: TenantContext | None, *,
                             stage_id: str, idempotency_key: str) -> Any:
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"stage_id": stage_id}, stage_id=stage_id,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        current = await _get_stage(cur, intent["stage_id"], account_id)
        pipeline_id = str(current["pipeline_id"])
        old_name = str(current["name"])
        await _lock_pipeline(cur, pipeline_id, account_id)
        await cur.execute(
            "SELECT count(*) AS n FROM leads "
            "WHERE client_account_id = %s AND stage_id = %s",
            (account_id, intent["stage_id"]),
        )
        lead_count = int((await cur.fetchone())["n"])
        if lead_count > 0:
            raise CrmCommandDenied("stage_has_leads", {"lead_count": lead_count})
        try:
            await cur.execute(
                "DELETE FROM pipeline_stages "
                "WHERE id = %s AND client_account_id = %s AND pipeline_id = %s",
                (intent["stage_id"], account_id, pipeline_id),
            )
        except psycopg.errors.ForeignKeyViolation:
            # Lost the race against a concurrent lead insert; the RESTRICT
            # FK backstops the pre-count, so deny fail-closed.
            raise CrmCommandDenied("stage_has_leads") from None
        if cur.rowcount != 1:
            raise CrmCommandDenied("stage_not_found_in_scope")
        return {"resource_id": intent["stage_id"], "deleted": True,
                "agent_name_warning": old_name in AGENT_KNOWN_STAGE_NAMES,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"deleted": payload["deleted"],
                "agent_name_warning": payload["agent_name_warning"]}
