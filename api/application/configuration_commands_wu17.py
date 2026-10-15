"""Configuration write commands (change: configuration, WU17).

On the accepted `CrmCommandBase` template (canonical hash, reserve -> apply ->
audit + outbox -> one transaction; replay returns the stored result), mirroring
`agent_commands_wu16.py` exactly:

- CreateCustomFieldDefinitionCommand (operation
  `configuration.custom_field.define`): per-account custom lead field
  (name + type + select choices); name unique per account.
- UpdateCustomFieldDefinitionCommand (`configuration.custom_field.update`):
  partial field update (at least one of name/type/choices).
- DeleteCustomFieldDefinitionCommand (`configuration.custom_field.delete`):
  clears allowlist refs + values first, then the definition (composite FKs
  are ON DELETE RESTRICT).
- SetLeadCustomFieldValueCommand (`configuration.custom_field.value.set`):
  coerced upsert of one value per lead x field, validated against the
  definition's type (number -> numeric string, date -> ISO-8601, select ->
  must be in choices, text -> bounded string).
- AddContactTagCommand / RemoveContactTagCommand
  (`configuration.contact_tag.add` / `.remove`): mutate `contacts.tags text[]`
  in place with trim+lower canonicalization + dedupe (idempotent).

Idempotency keys are `console:{account}:{entity}:{key}` (built at the route).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import psycopg

from api.application.crm_commands_wu5 import (
    CrmCommandBase,
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.tenant_boundary import TenantContext

CUSTOM_FIELD_TYPES = frozenset({"text", "number", "select", "date"})

_FIELD_NAME_MAX = 100
_TAG_MAX = 64
_TEXT_VALUE_MAX = 4000


def _require_name(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise CrmCommandValidationError("name must be a non-empty string")
    name = value.strip()
    if len(name) > _FIELD_NAME_MAX:
        raise CrmCommandValidationError("name exceeds 100 characters")
    return name


def _require_type(value: Any) -> str:
    if value not in CUSTOM_FIELD_TYPES:
        raise CrmCommandValidationError("type is invalid")
    return value


def _require_choices(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CrmCommandValidationError("choices must be a non-empty list")
    cleaned: list[str] = []
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, str) or not entry.strip():
            raise CrmCommandValidationError("choices must be non-empty strings")
        cleaned.append(entry.strip())
    if len(set(cleaned)) != len(cleaned):
        raise CrmCommandValidationError("choices must be unique")
    return cleaned


def _coerce_value(field_type: str, value: Any, choices: Any) -> str:
    """Canonical text value for one definition type; rejects invalid input."""
    if field_type == "number":
        if isinstance(value, bool):
            raise CrmCommandValidationError("number value must not be a boolean")
        try:
            return str(Decimal(str(value)))
        except Exception:
            raise CrmCommandValidationError("value is not a valid number") from None
    if field_type == "date":
        if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
            raise CrmCommandValidationError("date value must be an ISO-8601 date")
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            raise CrmCommandValidationError(
                "value is not a valid ISO-8601 date") from None
    if field_type == "select":
        if not isinstance(value, str) or value.strip() not in (choices or ()):
            raise CrmCommandValidationError(
                "value must be one of the allowed choices")
        return value.strip()
    # text
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise CrmCommandValidationError("text value must be a non-empty string")
    text = value.strip()
    if len(text) > _TEXT_VALUE_MAX:
        raise CrmCommandValidationError("text value exceeds 4000 characters")
    return text


def _canonical_tag(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise CrmCommandValidationError("tag must be a non-empty string")
    tag = value.strip().lower()
    if len(tag) > _TAG_MAX:
        raise CrmCommandValidationError("tag exceeds 64 characters")
    return tag


class CreateCustomFieldDefinitionCommand(CrmCommandBase):
    """Per-account custom lead field (operation
    `configuration.custom_field.define`)."""

    operation = "configuration.custom_field.define"
    audit_resource_type = "custom_field_definition"
    outbox_event_type = "CustomFieldDefinitionCreated"

    async def execute_define(self, context: TenantContext | None, *,
                             name: Any, type: Any, choices: Any = None,
                             idempotency_key: str):
        clean_name = _require_name(name)
        clean_type = _require_type(type)
        clean_choices = (_require_choices(choices)
                         if clean_type == "select" else None)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"name": clean_name, "type": clean_type,
                             "choices": clean_choices},
            name=clean_name, type=clean_type, choices=clean_choices,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        field_id = str(uuid4())
        try:
            await cur.execute(
                """
                INSERT INTO custom_field_definitions
                  (id, client_account_id, name, type, choices, demo_seed_key)
                VALUES (%s, %s, %s, %s, %s::text[], NULL)
                """,
                (field_id, account_id, intent["name"], intent["type"],
                 intent["choices"]),
            )
        except psycopg.errors.UniqueViolation:
            raise CrmCommandDenied("custom_field_name_exists") from None
        return {"resource_id": field_id, "name": intent["name"],
                "type": intent["type"], "choices": intent["choices"],
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"name": payload["name"], "type": payload["type"]}


class UpdateCustomFieldDefinitionCommand(CrmCommandBase):
    """Partial field update (at least one of name/type/choices; operation
    `configuration.custom_field.update`)."""

    operation = "configuration.custom_field.update"
    audit_resource_type = "custom_field_definition"
    outbox_event_type = "CustomFieldDefinitionUpdated"

    async def execute_update(self, context: TenantContext | None, *,
                             field_id: str, name: Any = None,
                             type: Any = None, choices: Any = None,
                             idempotency_key: str):
        if name is None and type is None and choices is None:
            raise CrmCommandValidationError("nothing to update")
        clean: dict[str, Any] = {}
        if name is not None:
            clean["name"] = _require_name(name)
        if type is not None:
            clean["type"] = _require_type(type)
        if choices is not None:
            clean["choices"] = _require_choices(choices)
        if "type" in clean and clean["type"] != "select" and "choices" in clean:
            clean["choices"] = None
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"field_id": field_id, **clean},
            field_id=field_id, **clean,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id, name, type, choices FROM custom_field_definitions "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["field_id"], account_id),
        )
        current = await cur.fetchone()
        if current is None:
            raise CrmCommandDenied("custom_field_not_found_in_scope")
        name = intent.get("name", current["name"])
        ftype = intent.get("type", current["type"])
        choices = intent.get("choices", list(current["choices"] or ()))
        if ftype != "select":
            choices = None
        try:
            await cur.execute(
                "UPDATE custom_field_definitions SET name = %s, type = %s, "
                "choices = %s::text[], updated_at = now() "
                "WHERE id = %s AND client_account_id = %s",
                (name, ftype, choices, intent["field_id"], account_id),
            )
        except psycopg.errors.UniqueViolation:
            raise CrmCommandDenied("custom_field_name_exists") from None
        return {"resource_id": intent["field_id"], "name": name, "type": ftype,
                "choices": choices, "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"name": payload["name"], "type": payload["type"]}


class DeleteCustomFieldDefinitionCommand(CrmCommandBase):
    """Clear allowlist refs + values, then delete the definition (operation
    `configuration.custom_field.delete`)."""

    operation = "configuration.custom_field.delete"
    audit_resource_type = "custom_field_definition"
    outbox_event_type = "CustomFieldDefinitionDeleted"

    async def execute_delete(self, context: TenantContext | None, *,
                             field_id: str, idempotency_key: str):
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"field_id": field_id}, field_id=field_id,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id FROM custom_field_definitions "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["field_id"], account_id),
        )
        if await cur.fetchone() is None:
            raise CrmCommandDenied("custom_field_not_found_in_scope")
        await cur.execute(
            "DELETE FROM agent_field_allowlist "
            "WHERE client_account_id = %s AND field_definition_id = %s",
            (account_id, intent["field_id"]),
        )
        await cur.execute(
            "DELETE FROM lead_custom_field_values "
            "WHERE client_account_id = %s AND field_id = %s",
            (account_id, intent["field_id"]),
        )
        await cur.execute(
            "DELETE FROM custom_field_definitions "
            "WHERE id = %s AND client_account_id = %s",
            (intent["field_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("custom_field_delete_failed")
        return {"resource_id": intent["field_id"],
                "correlation_id": context.correlation_id}


class SetLeadCustomFieldValueCommand(CrmCommandBase):
    """Coerced upsert of one value per lead x field (operation
    `configuration.custom_field.value.set`)."""

    operation = "configuration.custom_field.value.set"
    audit_resource_type = "lead_custom_field_value"
    outbox_event_type = "LeadCustomFieldValueSet"

    async def execute_set(self, context: TenantContext | None, *,
                          lead_id: str, field_id: str, value: Any,
                          idempotency_key: str):
        if not isinstance(lead_id, str) or not lead_id.strip():
            raise CrmCommandValidationError("lead_id must be a non-empty string")
        if not isinstance(field_id, str) or not field_id.strip():
            raise CrmCommandValidationError("field_id must be a non-empty string")
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"lead_id": lead_id, "field_id": field_id,
                             "value": value},
            lead_id=lead_id, field_id=field_id, value=value,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        lead_id = intent["lead_id"]
        field_id = intent["field_id"]
        await cur.execute(
            "SELECT id FROM leads WHERE id = %s AND client_account_id = %s "
            "FOR UPDATE",
            (lead_id, account_id),
        )
        if await cur.fetchone() is None:
            raise CrmCommandDenied("lead_not_found_in_scope")
        await cur.execute(
            "SELECT name, type, choices FROM custom_field_definitions "
            "WHERE id = %s AND client_account_id = %s",
            (field_id, account_id),
        )
        field = await cur.fetchone()
        if field is None:
            raise CrmCommandDenied("custom_field_not_found_in_scope")
        coerced = _coerce_value(field["type"], intent["value"], field["choices"])
        await cur.execute(
            """
            INSERT INTO lead_custom_field_values
              (id, client_account_id, lead_id, field_id, value)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (lead_id, field_id) DO UPDATE SET
              value = EXCLUDED.value, updated_at = now()
            """,
            (str(uuid4()), account_id, lead_id, field_id, coerced),
        )
        return {"resource_id": lead_id, "field_id": field_id,
                "name": field["name"], "type": field["type"], "value": coerced,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"field_id": payload["field_id"], "type": payload["type"]}


class AddContactTagCommand(CrmCommandBase):
    """Idempotent tag append (trim+lower+dedupe; operation
    `configuration.contact_tag.add`)."""

    operation = "configuration.contact_tag.add"
    audit_resource_type = "contact"
    outbox_event_type = "ContactTagAdded"

    async def execute_add(self, context: TenantContext | None, *,
                          contact_id: str, tag: Any, idempotency_key: str):
        clean_tag = _canonical_tag(tag)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"contact_id": contact_id, "tag": clean_tag},
            contact_id=contact_id, tag=clean_tag,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "UPDATE contacts SET tags = ARRAY(SELECT DISTINCT x FROM "
            "unnest(array_append(COALESCE(tags, '{}'), %s)) AS x ORDER BY x), "
            "updated_at = now() WHERE id = %s AND client_account_id = %s",
            (intent["tag"], intent["contact_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("contact_not_found_in_scope")
        await cur.execute(
            "SELECT tags FROM contacts WHERE id = %s AND client_account_id = %s",
            (intent["contact_id"], account_id),
        )
        tags = list((await cur.fetchone())["tags"] or ())
        return {"resource_id": intent["contact_id"], "tags": tags,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"tags": payload["tags"]}


class RemoveContactTagCommand(CrmCommandBase):
    """Tag remove (canonicalized exact match; operation
    `configuration.contact_tag.remove`)."""

    operation = "configuration.contact_tag.remove"
    audit_resource_type = "contact"
    outbox_event_type = "ContactTagRemoved"

    async def execute_remove(self, context: TenantContext | None, *,
                             contact_id: str, tag: Any, idempotency_key: str):
        clean_tag = _canonical_tag(tag)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"contact_id": contact_id, "tag": clean_tag},
            contact_id=contact_id, tag=clean_tag,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "UPDATE contacts SET tags = ARRAY(SELECT x FROM "
            "unnest(COALESCE(tags, '{}')) AS x WHERE x <> %s ORDER BY x), "
            "updated_at = now() WHERE id = %s AND client_account_id = %s",
            (intent["tag"], intent["contact_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("contact_not_found_in_scope")
        await cur.execute(
            "SELECT tags FROM contacts WHERE id = %s AND client_account_id = %s",
            (intent["contact_id"], account_id),
        )
        tags = list((await cur.fetchone())["tags"] or ())
        return {"resource_id": intent["contact_id"], "tags": tags,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"tags": payload["tags"]}
