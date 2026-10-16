"""Work Unit 19 (Commercial Pipelines): advisor pipeline/stage write surface.

Thin WU19-owned glue over accepted seams — nothing accepted is modified:
the six pipeline commands (pipeline_commands_wu19), TenantContextResolver
(accepted tenant boundary), the WU8 dashboard composition's app.state
(resolver, scoped cache), envelope (http_contracts).

Mounted as an APIRouter onto the WU8 app (composition_wu8_mvp) so selector
and the demo account session are shared with the dashboard; the Embudo
view calls this surface. Mirrors the calendar_wu13 router structure:
build_pipeline_router(conn_kwargs) factory, _context/_denial helpers,
strict unknown-key rejection, console idempotency keys, and the standard
accepted/replayed/result envelope with X-Correlation-Id.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.adapters.postgres_idempotency import IdempotencyConflict
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
from api.http_contracts import envelope
from api.tenant_boundary import (
    TenantAccessDenied,
    TenantAuthenticationRequired,
)

PIPELINE_ENVIRONMENT = "dev-only-commercial-pipelines"

PIPELINES_ROUTE = "/pipelines"
PIPELINE_ROUTE = PIPELINES_ROUTE + "/{pipeline_id}"
STAGES_ROUTE = PIPELINE_ROUTE + "/stages"
STAGE_ROUTE = "/stages/{stage_id}"
MOVE_ROUTE = STAGE_ROUTE + "/move"

_PIPELINE_KEYS = frozenset({"name", "stages", "idempotencyKey"})
_STAGE_KEYS = frozenset({"name", "idempotencyKey"})
_MOVE_KEYS = frozenset({"direction", "idempotencyKey"})
_DELETE_KEYS = frozenset({"idempotencyKey"})

_NOT_FOUND_REASONS = frozenset({
    "pipeline_not_found_in_scope", "stage_not_found_in_scope",
})
_CONFLICT_REASONS = frozenset({
    "stage_name_already_exists", "stage_position_conflict",
})


def _console_key(account_id: str, entity: str, raw: dict[str, Any],
                 canonical: dict[str, Any], header_key: str | None = None) -> str:
    """console:{scope}:{entity}:{key}: client key when supplied (body first,
    X-Idempotency-Key header as fallback), else a deterministic content hash
    (same payload replays, new payload is new). Mirrors agent_wu14."""
    client = raw.get("idempotencyKey")
    if (not isinstance(client, str) or not client.strip()) and header_key:
        client = header_key
    if isinstance(client, str) and client.strip():
        return f"console:{account_id}:{entity}:{client.strip()}"
    digest = hashlib.sha256(json.dumps(
        canonical, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()[:24]
    return f"console:{account_id}:{entity}:{digest}"


def _ok(correlation: str, payload: dict[str, Any],
        *, replayed: bool = False) -> JSONResponse:
    return JSONResponse(
        status_code=200, headers={"X-Correlation-Id": correlation}, content={
            "contract_version": "v1", "environment": PIPELINE_ENVIRONMENT,
            "accepted": True, "replayed": replayed,
            "correlation_id": correlation, "result": payload,
        })


def build_pipeline_router(conn_kwargs: dict[str, Any]) -> APIRouter:
    """Advisor pipeline/stage writes: create/rename pipelines, create/rename/
    move/delete stages. Server-derived tenant scope; forged/foreign/ghost ids
    share one non-enumerating 404; stage delete with leads denies 409."""
    router = APIRouter()
    create_pipeline_cmd = CreatePipelineCommand(dict(conn_kwargs))
    rename_pipeline_cmd = RenamePipelineCommand(dict(conn_kwargs))
    create_stage_cmd = CreateStageCommand(dict(conn_kwargs))
    rename_stage_cmd = RenameStageCommand(dict(conn_kwargs))
    move_stage_cmd = ReorderStageCommand(dict(conn_kwargs))
    delete_stage_cmd = DeleteStageCommand(dict(conn_kwargs))

    async def _context(request: Request, correlation: str):
        composition = request.app.state.demo_composition
        try:
            context = await composition.resolver.resolve(
                request, request.app.state.target_client_account_id)
        except TenantAuthenticationRequired:
            return None, envelope(401, "authentication_required",
                                  "authentication is required", correlation)
        except TenantAccessDenied:
            return None, envelope(403, "tenant_access_denied", "access denied", correlation)
        return context, None

    def _denial(problem: CrmCommandDenied, correlation: str):
        if problem.reason in _NOT_FOUND_REASONS:
            return envelope(404, "resource_not_found",
                            "resource is not available in this account scope", correlation)
        if problem.reason == "stage_has_leads":
            lead_count = problem.metadata.get("lead_count")
            content: dict[str, Any] = {
                "error": {"code": "stage_has_leads",
                          "message": "stage cannot be deleted while leads reference it",
                          "correlation_id": correlation},
            }
            if isinstance(lead_count, int):
                content["lead_count"] = lead_count
            response = JSONResponse(status_code=409, content=content)
            response.headers["X-Correlation-Id"] = correlation
            return response
        if problem.reason in _CONFLICT_REASONS:
            return envelope(409, problem.reason,
                            "pipeline write conflict", correlation)
        return envelope(403, "tenant_access_denied", "access denied", correlation)

    async def _body(request: Request, correlation: str,
                    allowed: frozenset) -> tuple[dict[str, Any] | None, Any]:
        try:
            raw = await request.json()
        except Exception:
            raw = None
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            return None, envelope(400, "invalid_command",
                                  "body must be an object", correlation)
        unknown = set(raw.keys()) - allowed
        if unknown:
            return None, envelope(400, "invalid_command",
                                  f"unsupported keys: {sorted(unknown)}",
                                  correlation)
        return raw, None

    def _header_key(request: Request) -> str | None:
        value = request.headers.get("x-idempotency-key", "").strip()
        return value or None

    async def _owning_pipeline(request: Request, account_id: str,
                               stage_id: str) -> str | None:
        composition = request.app.state.demo_composition
        rows = await composition.crm.runner.query(
            "SELECT pipeline_id FROM pipeline_stages "
            "WHERE id = %s AND client_account_id = %s",
            (stage_id, account_id),
        )
        if not rows:
            return None
        return str(rows[0]["pipeline_id"])

    @router.post(PIPELINES_ROUTE)
    async def create_pipeline(request: Request):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _body(request, correlation, _PIPELINE_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await create_pipeline_cmd.execute_create(
                context, name=raw.get("name"), stages=raw.get("stages"),
                idempotency_key=_console_key(
                    account_id, "pipeline", raw,
                    {"name": raw.get("name"), "stages": raw.get("stages")},
                    _header_key(request)),
            )
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        return _ok(correlation, outcome.payload, replayed=outcome.replayed)

    @router.put(PIPELINE_ROUTE)
    async def rename_pipeline(request: Request, pipeline_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _body(request, correlation, _PIPELINE_KEYS - {"stages"})
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await rename_pipeline_cmd.execute_rename(
                context, pipeline_id=pipeline_id, name=raw.get("name"),
                idempotency_key=_console_key(
                    account_id, "pipeline-rename", raw,
                    {"pipeline_id": pipeline_id, "name": raw.get("name")},
                    _header_key(request)),
            )
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        payload = dict(outcome.payload)
        # Pipelines carry no agent-known stage names, so a pipeline rename
        # never disturbs the agent; the flag is always False (warn-only,
        # never denies) to keep the rename envelope uniform.
        payload.setdefault("agent_name_warning", False)
        return _ok(correlation, payload, replayed=outcome.replayed)

    @router.post(STAGES_ROUTE)
    async def create_stage(request: Request, pipeline_id: str):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _body(request, correlation, _STAGE_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        try:
            outcome = await create_stage_cmd.execute_create_stage(
                context, pipeline_id=pipeline_id, name=raw.get("name"),
                idempotency_key=_console_key(
                    account_id, "pipeline-stage", raw,
                    {"pipeline_id": pipeline_id, "name": raw.get("name")},
                    _header_key(request)),
            )
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        return _ok(correlation, outcome.payload, replayed=outcome.replayed)

    @router.put(STAGE_ROUTE)
    async def rename_stage(request: Request, stage_id: str,
                           pipeline_id: str | None = None):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _body(request, correlation, _STAGE_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        if pipeline_id is not None:
            owning = await _owning_pipeline(request, account_id, stage_id)
            if owning is None or owning != pipeline_id:
                return envelope(404, "resource_not_found",
                                "resource is not available in this account scope",
                                correlation)
        try:
            outcome = await rename_stage_cmd.execute_rename_stage(
                context, stage_id=stage_id, name=raw.get("name"),
                idempotency_key=_console_key(
                    account_id, "pipeline-stage-rename", raw,
                    {"stage_id": stage_id, "name": raw.get("name")},
                    _header_key(request)),
            )
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        return _ok(correlation, outcome.payload, replayed=outcome.replayed)

    @router.post(MOVE_ROUTE)
    async def move_stage(request: Request, stage_id: str,
                         pipeline_id: str | None = None):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _body(request, correlation, _MOVE_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        if pipeline_id is not None:
            owning = await _owning_pipeline(request, account_id, stage_id)
            if owning is None or owning != pipeline_id:
                return envelope(404, "resource_not_found",
                                "resource is not available in this account scope",
                                correlation)
        try:
            outcome = await move_stage_cmd.execute_move(
                context, stage_id=stage_id, direction=raw.get("direction"),
                idempotency_key=_console_key(
                    account_id, "pipeline-stage-move", raw,
                    {"stage_id": stage_id, "direction": raw.get("direction")},
                    _header_key(request)),
            )
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        return _ok(correlation, outcome.payload, replayed=outcome.replayed)

    @router.delete(STAGE_ROUTE)
    async def delete_stage(request: Request, stage_id: str,
                           pipeline_id: str | None = None):
        correlation = request.state.correlation_id
        context, error = await _context(request, correlation)
        if error is not None:
            return error
        raw, error = await _body(request, correlation, _DELETE_KEYS)
        if error is not None:
            return error
        account_id = context.client_account.id
        if pipeline_id is not None:
            owning = await _owning_pipeline(request, account_id, stage_id)
            if owning is None or owning != pipeline_id:
                return envelope(404, "resource_not_found",
                                "resource is not available in this account scope",
                                correlation)
        try:
            outcome = await delete_stage_cmd.execute_delete(
                context, stage_id=stage_id,
                idempotency_key=_console_key(
                    account_id, "pipeline-stage-delete", raw,
                    {"stage_id": stage_id}, _header_key(request)),
            )
        except CrmCommandDenied as problem:
            return _denial(problem, correlation)
        except CrmCommandValidationError as problem:
            return envelope(400, "invalid_command", str(problem), correlation)
        except IdempotencyConflict:
            return envelope(409, "tenant_idempotency_conflict",
                            "command idempotency conflict", correlation)
        composition = request.app.state.demo_composition
        composition.cache.invalidate_scope(context.agency.id, context.client_account.id)
        return _ok(correlation, outcome.payload, replayed=outcome.replayed)

    return router
