"""PostgreSQL tenancy repositories implementing the accepted boundary ports.

Implements the interfaces consumed by api.tenant_boundary.TenantContextResolver
(find_account / find_membership) plus minimal Agency/ClientAccount/User reads,
all backed by real PostgreSQL tables from migration 001.
"""

from __future__ import annotations

from typing import Any

from api.adapters.postgres_core import PostgresRunner
from api.tenant_boundary import Agency, ClientAccount, Membership, User


class PostgresTenantRepository:
    """Resolves Agency + ClientAccount pairs from the database."""

    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def find_account(self, client_account_id: str) -> tuple[Agency, ClientAccount] | None:
        rows = await self._runner.query(
            """
            SELECT a.id AS agency_id, a.name AS agency_name, a.lifecycle_state AS agency_state,
                   ca.id AS account_id, ca.agency_id AS account_agency_id,
                   ca.name AS account_name, ca.lifecycle_state AS account_state
            FROM client_accounts ca
            JOIN agency a ON a.id = ca.agency_id
            WHERE ca.id = %s
            """,
            (client_account_id,),
        )
        if not rows:
            return None
        row: dict[str, Any] = rows[0]
        return (
            Agency(str(row["agency_id"]), row["agency_name"]),
            ClientAccount(str(row["account_id"]), str(row["account_agency_id"]), row["account_name"]),
        )


class PostgresMembershipRepository:
    """Resolves explicit memberships; returns None when absent (fail closed)."""

    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def find_membership(self, user_id: str, client_account_id: str) -> Membership | None:
        rows = await self._runner.query(
            """
            SELECT m.agency_id, m.client_account_id, m.user_id, m.permissions, m.lifecycle_state
            FROM memberships m
            WHERE m.user_id = %s AND m.client_account_id = %s AND m.lifecycle_state = 'active'
            """,
            (user_id, client_account_id),
        )
        if not rows:
            return None
        row: dict[str, Any] = rows[0]
        return Membership(
            user_id=str(row["user_id"]),
            agency_id=str(row["agency_id"]),
            client_account_id=str(row["client_account_id"]),
            permissions=frozenset(row["permissions"]),
        )


class PostgresUserRepository:
    """Minimal server-side user lookup used by composition/tests only."""

    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def find_user(self, user_id: str) -> User | None:
        rows = await self._runner.query(
            "SELECT id FROM users WHERE id = %s AND lifecycle_state = 'active'",
            (user_id,),
        )
        if not rows:
            return None
        return User(id=str(rows[0]["id"]), display_name=str(rows[0]["id"]))

    async def find_user_by_identity_link(self, provider: str, external_subject: str) -> User | None:
        rows = await self._runner.query(
            """
            SELECT u.id FROM identity_links il
            JOIN users u ON u.id = il.user_id
            WHERE il.provider = %s AND il.external_subject = %s AND u.lifecycle_state = 'active'
            """,
            (provider, external_subject),
        )
        if not rows:
            return None
        return User(id=str(rows[0]["id"]), display_name=str(rows[0]["id"]))
