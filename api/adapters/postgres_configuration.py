"""Configuration read path (change: configuration, WU17).

Pure per-request loaders over the 010 configuration tables; no cache (the
"correctness > latency" precedent), account predicate first, missing rows ->
empty list. No commands, no writes live here.

- custom_field_definitions: the per-account catalogue of custom lead fields.
- lead_custom_field_values: the EAV value store (one canonical text value per
  lead x field).
- list_contacts_by_tag: contacts filtered by an exact tag (`= ANY(tags)`).
"""

from __future__ import annotations

from typing import Any

from api.adapters.postgres_commercial_crm import _require
from api.adapters.postgres_core import PostgresRunner


def _definition(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "client_account_id": str(row["client_account_id"]),
        "name": row["name"],
        "type": row["type"],
        "choices": list(row["choices"] or ()) if row.get("choices") else [],
    }


async def load_custom_field_definitions(
        context, runner: PostgresRunner) -> list[dict[str, Any]]:
    account_id = _require(context)
    rows = await runner.query(
        "SELECT id, client_account_id, name, type, choices "
        "FROM custom_field_definitions WHERE client_account_id = %s "
        "ORDER BY created_at, id",
        (account_id,),
    )
    return [_definition(r) for r in rows]


async def load_custom_field_values(
        context, runner: PostgresRunner, lead_id: str) -> list[dict[str, Any]]:
    account_id = _require(context)
    rows = await runner.query(
        "SELECT v.field_id, v.value, d.name, d.type "
        "FROM lead_custom_field_values v "
        "JOIN custom_field_definitions d ON d.id = v.field_id "
        "AND d.client_account_id = v.client_account_id "
        "WHERE v.client_account_id = %s AND v.lead_id = %s "
        "ORDER BY d.name, v.id",
        (account_id, lead_id),
    )
    return [{"field_id": str(r["field_id"]), "name": r["name"],
             "type": r["type"], "value": r["value"]} for r in rows]


async def list_contacts_by_tag(
        context, runner: PostgresRunner, tag: str) -> list[dict[str, Any]]:
    account_id = _require(context)
    rows = await runner.query(
        "SELECT id, name, tags FROM contacts "
        "WHERE client_account_id = %s AND %s = ANY(tags) "
        "ORDER BY name, id",
        (account_id, tag),
    )
    return [{"id": str(r["id"]), "name": r["name"],
             "tags": list(r["tags"] or ())} for r in rows]
