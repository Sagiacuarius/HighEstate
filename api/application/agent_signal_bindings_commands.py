"""Agent signal-binding write commands (change: agent-custom-field-writes, WU18).

On the accepted `CrmCommandBase` template (canonical hash, reserve -> apply ->
audit + outbox -> one transaction; replay returns the stored result),
mirroring `agent_commands_wu16.py` exactly:

- CreateSignalBindingCommand upserts one row per (account, signal_key)
  (operation `agent.signal_binding.create`).
- DeleteSignalBindingCommand removes one row by signal_key (operation
  `agent.signal_binding.delete`; editing a binding is delete + create).

`_apply` validates `signal_key` against the closed `SIGNAL_KEYS` catalog and
that `field_definition_id` names a defined field in scope
(`custom_field_not_found_in_scope` otherwise); deleting a bound definition
cascades its bindings via the 011 composite FK (`ON DELETE CASCADE`).

Idempotency keys are `console:{account}:signal-binding:{signal}` (built at
the route).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from api.application.agent_signals import SIGNAL_KEYS
from api.application.crm_commands_wu5 import (
    CrmCommandBase,
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.tenant_boundary import TenantContext


def _require_signal(signal_key: Any) -> str:
    if signal_key not in SIGNAL_KEYS:
        raise CrmCommandValidationError("signal_key is invalid")
    return signal_key


def _require_field_id(field_definition_id: Any) -> str:
    if (isinstance(field_definition_id, bool)
            or not isinstance(field_definition_id, str)
            or not field_definition_id.strip()):
        raise CrmCommandValidationError(
            "field_definition_id must be a non-empty string")
    return field_definition_id


class CreateSignalBindingCommand(CrmCommandBase):
    """Upsert one signal -> field binding (operation
    `agent.signal_binding.create`)."""

    operation = "agent.signal_binding.create"
    audit_resource_type = "agent_signal_binding"
    outbox_event_type = "AgentSignalBindingCreated"

    async def execute_create(self, context: TenantContext | None, *,
                             signal_key: Any, field_definition_id: Any,
                             idempotency_key: str):
        signal = _require_signal(signal_key)
        field_id = _require_field_id(field_definition_id)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"signal_key": signal,
                             "field_definition_id": field_id},
            signal_key=signal, field_definition_id=field_id,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id FROM custom_field_definitions "
            "WHERE id = %s AND client_account_id = %s",
            (intent["field_definition_id"], account_id),
        )
        if await cur.fetchone() is None:
            raise CrmCommandDenied("custom_field_not_found_in_scope")
        await cur.execute(
            """
            INSERT INTO agent_signal_bindings
              (id, client_account_id, signal_key, field_definition_id,
               demo_seed_key)
            VALUES (%s, %s, %s, %s, NULL)
            ON CONFLICT (client_account_id, signal_key) DO UPDATE SET
              field_definition_id = EXCLUDED.field_definition_id,
              updated_at = now()
            RETURNING id
            """,
            (str(uuid4()), account_id, intent["signal_key"],
             intent["field_definition_id"]),
        )
        row = await cur.fetchone()
        return {"resource_id": str(row["id"]),
                "signal_key": intent["signal_key"],
                "field_definition_id": intent["field_definition_id"],
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"signal_key": payload["signal_key"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "signal_key": payload["signal_key"]}


class DeleteSignalBindingCommand(CrmCommandBase):
    """Delete one signal binding by signal_key (operation
    `agent.signal_binding.delete`)."""

    operation = "agent.signal_binding.delete"
    audit_resource_type = "agent_signal_binding"
    outbox_event_type = "AgentSignalBindingDeleted"

    async def execute_delete(self, context: TenantContext | None, *,
                             signal_key: Any, idempotency_key: str):
        signal = _require_signal(signal_key)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"signal_key": signal}, signal_key=signal,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "DELETE FROM agent_signal_bindings "
            "WHERE client_account_id = %s AND signal_key = %s",
            (account_id, intent["signal_key"]),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("signal_binding_not_found_in_scope")
        return {"resource_id": account_id,
                "signal_key": intent["signal_key"], "deleted": True,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"signal_key": payload["signal_key"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "signal_key": payload["signal_key"]}
