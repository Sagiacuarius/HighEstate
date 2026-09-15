"""PostgreSQL tenant-scoped resource adapter (Work Unit 4).

Mandatory design rules (from postgres-tenant-isolation spec):
- every operation requires a server-derived TenantContext;
- the SQL predicate always includes context.client_account.id;
- resources are never resolved from client-supplied account ids;
- a context/resource mismatch fails closed BEFORE any SQL executes.
"""

from __future__ import annotations

from typing import Any

from api.adapters.postgres_core import CrossTenantAccessDenied, PostgresRunner
from api.tenant_boundary import TenantContext


def _require(context: TenantContext | None) -> str:
    if context is None:
        raise CrossTenantAccessDenied("tenant context is required")
    account_id = context.client_account.id
    if not account_id:
        raise CrossTenantAccessDenied("tenant context has no client account")
    # Defense in depth: the membership must match the context account/agency.
    if context.membership.client_account_id != account_id or context.membership.agency_id != context.agency.id:
        raise CrossTenantAccessDenied("tenant context membership is inconsistent")
    return account_id


class PostgresTenantResourceRepository:
    """CRUD for the representative tenant_resource table, always scoped."""

    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def list(self, context: TenantContext) -> list[dict[str, Any]]:
        account_id = _require(context)
        rows = await self._runner.query(
            """
            SELECT id, client_account_id, resource_key, data
            FROM tenant_resource
            WHERE client_account_id = %s
            ORDER BY created_at, id
            """,
            (account_id,),
        )
        return [
            {
                "id": str(r["id"]),
                "client_account_id": str(r["client_account_id"]),
                "resource_key": r["resource_key"],
                "data": r["data"],
            }
            for r in rows
        ]

    async def read(self, context: TenantContext, resource_id: str) -> dict[str, Any] | None:
        """Read only within the context account. A foreign id is indistinguishable
        from a missing one (single None) to avoid cross-tenant enumeration."""
        account_id = _require(context)
        rows = await self._runner.query(
            """
            SELECT id, client_account_id, resource_key, data
            FROM tenant_resource
            WHERE id = %s AND client_account_id = %s
            """,
            (resource_id, account_id),
        )
        if not rows:
            return None
        r = rows[0]
        return {
            "id": str(r["id"]),
            "client_account_id": str(r["client_account_id"]),
            "resource_key": r["resource_key"],
            "data": r["data"],
        }

    async def create(
        self, context: TenantContext, resource_id: str, resource_key: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        account_id = _require(context)
        if not context.membership.allows("client_account:write"):
            # Permission comes from the server-derived membership, never the request.
            raise CrossTenantAccessDenied("membership lacks write permission")
        affected = await self._runner.execute(
            """
            INSERT INTO tenant_resource (id, client_account_id, resource_key, data)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (client_account_id, resource_key) DO NOTHING
            """,
            (resource_id, account_id, resource_key, _json(data)),
        )
        if affected != 1:
            raise CrossTenantAccessDenied("resource_key already exists in this account")
        created = await self.read(context, resource_id)
        assert created is not None
        return created

    async def update(
        self, context: TenantContext, resource_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        account_id = _require(context)
        if not context.membership.allows("client_account:write"):
            raise CrossTenantAccessDenied("membership lacks write permission")
        # The predicate client_account_id = context account makes a foreign id
        # structurally un-updatable; target_account is only validated equality.
        target_account = patch.get("client_account_id")
        if target_account is not None and str(target_account) != account_id:
            raise CrossTenantAccessDenied("cannot move resource across accounts")
        affected = await self._runner.execute(
            """
            UPDATE tenant_resource
            SET data = %s::jsonb, updated_at = now()
            WHERE id = %s AND client_account_id = %s
            """,
            (_json(patch.get("data", {})), resource_id, account_id),
        )
        if affected == 0:
            return None
        return await self.read(context, resource_id)

    async def delete(self, context: TenantContext, resource_id: str) -> bool:
        account_id = _require(context)
        if not context.membership.allows("client_account:write"):
            raise CrossTenantAccessDenied("membership lacks write permission")
        affected = await self._runner.execute(
            "DELETE FROM tenant_resource WHERE id = %s AND client_account_id = %s",
            (resource_id, account_id),
        )
        return affected == 1


def _json(value: Any) -> str:
    import json

    return json.dumps(value if value is not None else {})
