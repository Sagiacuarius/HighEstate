"""Server-owned demo identity and membership selector (MVP Commercial Demo WU7).

Dev-only stand-in for a future auth layer: the demo user is server
configuration, never derived from request claims. The requested account id is
an intent/hint only — membership is resolved server-side through the accepted
TenantContextResolver, and the caller's prior context is never mutated here
(the composition mutates session state only after a successful select).
"""

from __future__ import annotations

import re

from fastapi import Request

from api.tenant_boundary import (
    CurrentIdentity,
    TenantAccessDenied,
    TenantAuthenticationRequired,
    TenantContext,
    TenantContextResolver,
    User,
)

DEMO_USER_ID = "10000000-0000-0000-0000-000000000001"
DEMO_USER_DISPLAY_NAME = "demo-user"

# Same shape guard as the accepted WU1 contract ids (non-empty, no
# surrounding whitespace, bounded length, restricted charset).
_REQUESTED_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]*$")


class DemoIdentityAdapter:
    """Server-owned dev identity: always the configured demo user, claims ignored."""

    def __init__(self, user_id: str = DEMO_USER_ID,
                 display_name: str = DEMO_USER_DISPLAY_NAME):
        self._user = User(user_id, display_name)

    async def get_authenticated_user(self, request: Request) -> User | None:
        del request
        return self._user


class AbsentDemoIdentityAdapter:
    """Dev-only unauthenticated stand-in: always resolves to no identity."""

    async def get_authenticated_user(self, request: Request) -> User | None:
        del request
        return None


class MalformedAccountIntent(ValueError):
    """Requested account hint fails the pure shape guard (400, before any SQL)."""


def validate_requested_account_id(raw: object) -> str:
    if not isinstance(raw, str):
        raise MalformedAccountIntent("requested account id must be a string")
    if not raw or raw.strip() != raw or len(raw) > 256:
        raise MalformedAccountIntent("requested account id is malformed")
    if _REQUESTED_ACCOUNT_PATTERN.match(raw) is None:
        raise MalformedAccountIntent("requested account id is malformed")
    return raw


class DemoContextSelector:
    """Membership selector: hint in, server-resolved TenantContext out.

    Mirrors the TS DemoContextResolver contract (src/application/commercial-demo.ts):
    resolve() from trusted identity + membership; the intent never authorizes
    itself. Raises TenantAuthenticationRequired (no server identity),
    MalformedAccountIntent (shape failure, before SQL), or TenantAccessDenied
    (missing/foreign/no-membership — one non-enumerating outcome).
    """

    def __init__(self, identity: CurrentIdentity, resolver: TenantContextResolver):
        self._identity = identity
        self._resolver = resolver

    async def select(self, request: Request, requested_account_id: str) -> TenantContext:
        hint = validate_requested_account_id(requested_account_id)
        user = await self._identity.get_authenticated_user(request)
        if user is None:
            raise TenantAuthenticationRequired()
        context = await self._resolver.resolve(request, hint)
        if context.user.id != user.id:
            # Resolver/user mismatch must never surface as authority.
            raise TenantAccessDenied()
        return context
