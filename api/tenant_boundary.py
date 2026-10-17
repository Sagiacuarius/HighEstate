"""Provider-neutral tenant/auth boundary for the FastAPI adapter.

This module deliberately contains no real authentication provider. The
CurrentIdentity adapter is server-owned; HTTP request claims are never used as
authority. The in-memory implementations are test/reference adapters only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.http_contracts import envelope


@dataclass(frozen=True)
class User:
    id: str
    display_name: str


@dataclass(frozen=True)
class Agency:
    id: str
    name: str


@dataclass(frozen=True)
class ClientAccount:
    id: str
    agency_id: str
    name: str


@dataclass(frozen=True)
class Membership:
    user_id: str
    agency_id: str
    client_account_id: str
    permissions: frozenset[str]

    def allows(self, permission: str) -> bool:
        return permission in self.permissions


@dataclass(frozen=True)
class TenantContext:
    user: User
    agency: Agency
    client_account: ClientAccount
    membership: Membership
    correlation_id: str


class CurrentIdentity(Protocol):
    """Server-owned authenticated identity; request claims are not accepted."""

    async def get_authenticated_user(self, request: Request) -> User | None:
        ...


IdentityAdapter = CurrentIdentity


class TenantRepository(Protocol):
    async def find_account(self, client_account_id: str) -> tuple[Agency, ClientAccount] | None:
        ...


class MembershipRepository(Protocol):
    async def find_membership(self, user_id: str, client_account_id: str) -> Membership | None:
        ...


class TenantAccessDenied(Exception):
    """Non-enumerating authorization failure for missing/foreign scope."""


class TenantAuthenticationRequired(Exception):
    """No server-trusted authenticated identity is available."""


class TenantServiceNotConfigured(Exception):
    """No application composition supplied a tenant resolver."""


SERVICE_PRINCIPAL_ID = "service:tenant-webhook"
SERVICE_PRINCIPAL_NAME = "Tenant Webhook Service"
SERVICE_WRITE_PERMISSIONS = frozenset({"client_account:write"})


def get_tenant_resolver(request: Request) -> TenantContextResolver | None:
    """FastAPI dependency; composition, not request data, supplies the resolver."""
    composition = getattr(request.app.state, "tenant_composition", None)
    return getattr(composition, "resolver", None)


class InMemoryIdentityAdapter:
    """Test-only identity adapter. It ignores all request identity claims."""

    def __init__(self, authenticated_user: User | None):
        self._authenticated_user = authenticated_user

    async def get_authenticated_user(self, request: Request) -> User | None:
        del request
        return self._authenticated_user


class InMemoryTenantRepository:
    """Test-only Agency/ClientAccount relationship store."""

    def __init__(self, agencies: list[Agency] | None = None, accounts: list[ClientAccount] | None = None):
        self._agencies = {agency.id: agency for agency in agencies or []}
        self._accounts = {account.id: account for account in accounts or []}
        for account in self._accounts.values():
            if account.agency_id not in self._agencies:
                raise ValueError("ClientAccount references an unknown Agency")

    async def find_account(self, client_account_id: str) -> tuple[Agency, ClientAccount] | None:
        account = self._accounts.get(client_account_id)
        if account is None:
            return None
        agency = self._agencies.get(account.agency_id)
        return (agency, account) if agency else None


class InMemoryMembershipRepository:
    """Test-only explicit membership store."""

    def __init__(self, memberships: list[Membership] | None = None):
        self._memberships = {
            (membership.user_id, membership.client_account_id): membership
            for membership in memberships or []
        }

    async def find_membership(self, user_id: str, client_account_id: str) -> Membership | None:
        return self._memberships.get((user_id, client_account_id))


class TenantContextResolver:
    """Application service that derives context from trusted identity + stores."""

    def __init__(self, identity: CurrentIdentity, tenants: TenantRepository, memberships: MembershipRepository):
        self._identity = identity
        self._tenants = tenants
        self._memberships = memberships

    async def resolve(self, request: Request, target_client_account_id: str, agency_hint: str | None = None) -> TenantContext:
        user = await self._identity.get_authenticated_user(request)
        if user is None:
            raise TenantAuthenticationRequired()

        resolved = await self._tenants.find_account(target_client_account_id)
        # A missing account, foreign account, falsified agency hint and absent
        # membership intentionally share one non-enumerating denial.
        if resolved is None:
            raise TenantAccessDenied()
        agency, account = resolved
        if account.agency_id != agency.id:
            raise TenantAccessDenied()
        if agency_hint is not None and agency_hint != agency.id:
            raise TenantAccessDenied()

        membership = await self._memberships.find_membership(user.id, account.id)
        if membership is None:
            raise TenantAccessDenied()
        if (
            membership.user_id != user.id
            or membership.agency_id != agency.id
            or membership.client_account_id != account.id
            or not membership.allows("client_account:read")
        ):
            raise TenantAccessDenied()

        return TenantContext(
            user=user,
            agency=agency,
            client_account=account,
            membership=membership,
            correlation_id=getattr(request.state, "correlation_id", ""),
        )

    async def resolve_service(self, target_client_account_id: str,
                              correlation_id: str = "") -> TenantContext:
        """Resolve a trusted provider/service scope without human identity.

        The account mapping is supplied by application configuration, while the
        account and agency relationship remain authoritative in the tenant
        repository. No request claims or human membership are consulted.
        """
        resolved = await self._tenants.find_account(target_client_account_id)
        if resolved is None:
            raise TenantAccessDenied()
        agency, account = resolved
        if account.agency_id != agency.id:
            raise TenantAccessDenied()
        principal = User(SERVICE_PRINCIPAL_ID, SERVICE_PRINCIPAL_NAME)
        membership = Membership(
            user_id=principal.id,
            agency_id=agency.id,
            client_account_id=account.id,
            permissions=SERVICE_WRITE_PERMISSIONS,
        )
        return TenantContext(
            user=principal,
            agency=agency,
            client_account=account,
            membership=membership,
            correlation_id=correlation_id,
        )

    async def switch(
        self,
        request: Request,
        current_context: TenantContext,
        target_client_account_id: str,
        agency_hint: str | None = None,
    ) -> TenantContext:
        """Resolve a new context without mutating the previous one."""
        user = await self._identity.get_authenticated_user(request)
        if user is None or user.id != current_context.user.id:
            raise TenantAuthenticationRequired()
        if not current_context.membership.allows("client_account:switch"):
            raise TenantAccessDenied()
        return await self.resolve(request, target_client_account_id, agency_hint)


router = APIRouter(prefix="/tenant", tags=["tenant"])


def _resolver_from_request(request: Request) -> TenantContextResolver | None:
    return getattr(request.app.state, "tenant_context_resolver", None)


@router.get("/context/{client_account_id}")
async def resolve_context(
    request: Request,
    client_account_id: str,
    agency_id: str | None = None,
    resolver: TenantContextResolver | None = Depends(get_tenant_resolver),
) -> JSONResponse:
    correlation = getattr(request.state, "correlation_id", "")
    if resolver is None:
        return envelope(503, "tenant_service_not_configured", "Tenant service is not configured", correlation)
    try:
        context = await resolver.resolve(request, client_account_id, agency_id)
    except TenantAuthenticationRequired:
        return envelope(401, "authentication_required", "Authentication required", correlation)
    except TenantAccessDenied:
        return envelope(403, "tenant_access_denied", "Tenant access denied", correlation)
    return JSONResponse(
        status_code=200,
        headers={"X-Correlation-Id": correlation},
        content={
            "contract_version": "v1",
            "correlation_id": correlation,
            "user_id": context.user.id,
            "agency_id": context.agency.id,
            "client_account_id": context.client_account.id,
            "permissions": sorted(context.membership.permissions),
        },
    )
