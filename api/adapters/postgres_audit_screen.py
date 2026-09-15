"""WU12 (audit-screen) SQL adapter: read-only queries over the accepted
audit_records table (migration 003). No writes, no new capture, no migration.

Same discipline as the accepted WU6 read adapter: psycopg confined here, the
mandatory client_account_id predicate FIRST on every statement (from the
server context via _require), filters appended only when already validated in
the query service, and every value parameterized — never interpolated. The
filter-to-column mapping is pinned to what the accepted writers actually store
(crm_commands_wu5 / property_commands_wu11 / simulator_wu9):

- resource_type 'lead' + resource_id lead-uuid  -> join leads for contact /
  selected_property mappings (EXISTS carries its own account equality);
- resource_type 'property' | 'property_interest' -> resource_id is the
  property id (verified in the WU11 writers);
- property contact-link / interest extras carry metadata->>'contact_id'.

Keyset pagination over (created_at, id) DESC, the exact ordering of the
accepted audit_records_account_created_idx from 003; LIMIT is one above the
page size so the caller can detect has_more without a second count query.
"""

from __future__ import annotations

from typing import Any

from api.adapters.postgres_commercial_crm import _require
from api.adapters.postgres_core import PostgresRunner
from api.tenant_boundary import TenantContext

_AUDIT_COLUMNS = (
    "id, action, outcome, resource_type, resource_id, actor_id, "
    "correlation_id, idempotency_key, metadata->>'reason' AS reason, created_at"
)


class PostgresAuditScreenRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def query_audit(self, context: TenantContext, *,
                          contact_id: str | None,
                          property_id: str | None,
                          user_id: str | None,
                          action: str | None,
                          date_from: str | None,
                          date_to: str | None,
                          cursor: tuple[str, str] | None,
                          limit: int) -> list[dict[str, Any]]:
        """One page (limit+1 rows fetched) of the account-scoped audit trail.

        All optional values arrive pre-validated from the query service
        (uuid/date/action shapes); the SQL here stays purely structural.
        """
        account_id = _require(context)
        sql = [f"SELECT {_AUDIT_COLUMNS} FROM audit_records a WHERE a.client_account_id = %s"]
        params: list[Any] = [account_id]
        if contact_id is not None:
            sql.append(
                "AND ((a.resource_type = 'lead' AND EXISTS ("
                "      SELECT 1 FROM leads l WHERE l.id::text = a.resource_id"
                "      AND l.client_account_id = a.client_account_id"
                "      AND l.contact_id::text = %s))"
                "     OR a.metadata->>'contact_id' = %s)")
            params.extend([contact_id, contact_id])
        if property_id is not None:
            sql.append(
                "AND ((a.resource_type IN ('property', 'property_interest')"
                "      AND a.resource_id = %s)"
                "     OR (a.resource_type = 'lead' AND EXISTS ("
                "      SELECT 1 FROM leads l WHERE l.id::text = a.resource_id"
                "      AND l.client_account_id = a.client_account_id"
                "      AND l.selected_property_id::text = %s)))")
            params.extend([property_id, property_id])
        if user_id is not None:
            sql.append("AND a.actor_id::text = %s")
            params.append(user_id)
        if action is not None:
            sql.append("AND a.action = %s")
            params.append(action)
        if date_from is not None:
            sql.append("AND a.created_at >= %s::timestamptz")
            params.append(date_from)
        if date_to is not None:
            sql.append("AND a.created_at <= %s::timestamptz")
            params.append(date_to)
        if cursor is not None:
            sql.append("AND (a.created_at, a.id) < (%s::timestamptz, %s::uuid)")
            params.extend([cursor[0], cursor[1]])
        sql.append("ORDER BY a.created_at DESC, a.id DESC LIMIT %s")
        params.append(limit)
        return await self._runner.query(" ".join(sql), tuple(params))

    async def total_matching(self, context: TenantContext, *,
                             contact_id: str | None, property_id: str | None,
                             user_id: str | None, action: str | None,
                             date_from: str | None, date_to: str | None) -> int:
        """Unpaginated count for the same predicate (page 1 display only).

        Intentionally excludes the cursor: 'total' describes the filtered
        set, not the remainder, so paging never changes the headline number.
        """
        account_id = _require(context)
        sql = ["SELECT count(*) AS n FROM audit_records a WHERE a.client_account_id = %s"]
        params: list[Any] = [account_id]
        if contact_id is not None:
            sql.append(
                "AND ((a.resource_type = 'lead' AND EXISTS ("
                "      SELECT 1 FROM leads l WHERE l.id::text = a.resource_id"
                "      AND l.client_account_id = a.client_account_id"
                "      AND l.contact_id::text = %s))"
                "     OR a.metadata->>'contact_id' = %s)")
            params.extend([contact_id, contact_id])
        if property_id is not None:
            sql.append(
                "AND ((a.resource_type IN ('property', 'property_interest')"
                "      AND a.resource_id = %s)"
                "     OR (a.resource_type = 'lead' AND EXISTS ("
                "      SELECT 1 FROM leads l WHERE l.id::text = a.resource_id"
                "      AND l.client_account_id = a.client_account_id"
                "      AND l.selected_property_id::text = %s)))")
            params.extend([property_id, property_id])
        if user_id is not None:
            sql.append("AND a.actor_id::text = %s")
            params.append(user_id)
        if action is not None:
            sql.append("AND a.action = %s")
            params.append(action)
        if date_from is not None:
            sql.append("AND a.created_at >= %s::timestamptz")
            params.append(date_from)
        if date_to is not None:
            sql.append("AND a.created_at <= %s::timestamptz")
            params.append(date_to)
        rows = await self._runner.query(" ".join(sql), tuple(params))
        return int(rows[0]["n"])
