"""Scoped cache keys for the WU6 read surfaces (MVP Commercial Demo WU7).

Dev-only, process-local. Every key embeds the server-derived scope
(agency_id + client_account_id) so entries from one account can never be
served to another: a lookup whose key scope differs from the caller's context
scope fails closed before any fill/store. Seed/reset and context switches
invalidate by scope prefix.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from api.adapters.postgres_commercial_crm import _require
from api.tenant_boundary import TenantAccessDenied, TenantContext

CACHE_KEY_VERSION = "v1"
CACHE_KEY_NAMESPACE = "mvp-demo"


def build_scoped_key(context: TenantContext, surface: str, qualifier: str = "") -> str:
    """Pure key builder. Scope comes only from the server-resolved context."""
    account_id = _require(context)
    agency_id = context.agency.id
    if not agency_id:
        raise TenantAccessDenied("tenant context has no agency")
    if not surface or surface.strip() != surface:
        raise ValueError("cache surface must be a non-empty token")
    if "/" in surface or " " in surface:
        raise ValueError("cache surface must be a single path token")
    qualifier = qualifier.strip()
    if "/" in qualifier or " " in qualifier:
        raise ValueError("cache qualifier must be a single token")
    return f"{CACHE_KEY_NAMESPACE}:{CACHE_KEY_VERSION}:{agency_id}:{account_id}:{surface}:{qualifier}"


def key_scope(key: str) -> tuple[str, str]:
    """Extract (agency_id, client_account_id) from a scoped key or fail closed."""
    parts = key.split(":")
    if len(parts) != 6 or parts[0] != CACHE_KEY_NAMESPACE or parts[1] != CACHE_KEY_VERSION:
        raise TenantAccessDenied("cache key is not a scoped demo key")
    return parts[2], parts[3]


class ScopedQueryCache:
    """Minimal dev-only read cache with mandatory scope-partitioned keys."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0

    def _check(self, context: TenantContext, key: str) -> None:
        account_id = _require(context)
        agency_id, key_account = key_scope(key)
        if agency_id != context.agency.id or key_account != account_id:
            raise TenantAccessDenied("cache key scope does not match context")

    async def get_or_fill(self, context: TenantContext, key: str,
                          filler: Callable[[TenantContext], Awaitable[Any]]) -> tuple[Any, bool]:
        """Returns (value, from_cache). Cross-scope keys denied before fill."""
        self._check(context, key)
        if key in self._store:
            self.hits += 1
            return self._store[key], True
        self.misses += 1
        value = await filler(context)
        self._store[key] = value
        return value, False

    def invalidate_scope(self, agency_id: str, client_account_id: str) -> int:
        prefix = f"{CACHE_KEY_NAMESPACE}:{CACHE_KEY_VERSION}:{agency_id}:{client_account_id}:"
        doomed = [key for key in self._store if key.startswith(prefix)]
        for key in doomed:
            del self._store[key]
        return len(doomed)

    def clear(self) -> int:
        """Dev-only full flush (used after seed/reset, which touch both accounts)."""
        count = len(self._store)
        self._store.clear()
        return count

    def __len__(self) -> int:
        return len(self._store)
