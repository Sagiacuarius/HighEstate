"""Tenant-scoped, read-only Kapso configuration and observability status."""
from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.http_contracts import envelope
from api.tenant_boundary import TenantAccessDenied, TenantAuthenticationRequired

KAPSO_STATUS_ROUTE = "/kapso/status"
_UNAVAILABLE = {"status": "unavailable", "code": "storage_unavailable"}


def _phone_mapping() -> dict[str, str]:
    try:
        value = json.loads(os.getenv("KAPSO_PHONE_ACCOUNT_MAP", "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        key: account for key, account in value.items()
        if isinstance(key, str) and key.strip() and isinstance(account, str) and account.strip()
    }


async def _storage_status(runner: Any, account_id: str) -> dict[str, Any]:
    try:
        counts = await runner.query(
            "SELECT status, count(*) AS count FROM outbox_events "
            "WHERE client_account_id = %s GROUP BY status",
            (account_id,),
        )
        recent = await runner.query(
            "SELECT count(*) AS count FROM outbox_events "
            "WHERE client_account_id = %s AND status = 'published' "
            "AND processed_at >= now() - interval '24 hours'",
            (account_id,),
        )
        inbound = await runner.query(
            "SELECT created_at FROM messages WHERE client_account_id = %s "
            "AND direction = 'inbound' ORDER BY created_at DESC, id DESC LIMIT 1",
            (account_id,),
        )
        outbound = await runner.query(
            "SELECT m.created_at, COALESCE(o.status, 'persisted') AS status "
            "FROM messages m LEFT JOIN outbox_events o ON o.client_account_id = m.client_account_id "
            "AND o.payload->>'resource_id' = m.id::text "
            "WHERE m.client_account_id = %s AND m.direction = 'outbound' "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT 1",
            (account_id,),
        )
    except Exception:
        return {
            "outbox": _UNAVAILABLE,
            "last_inbound": _UNAVAILABLE,
            "last_outbound": _UNAVAILABLE,
        }

    by_status = {str(row.get("status")): int(row.get("count", 0)) for row in counts}
    return {
        "outbox": {
            "pending": by_status.get("pending", 0),
            "processing": by_status.get("processing", 0),
            "failed": by_status.get("failed", 0),
            "published_recent": int(recent[0].get("count", 0)) if recent else 0,
        },
        "last_inbound": ({"timestamp": inbound[0]["created_at"].isoformat(), "status": "persisted"}
                         if inbound and inbound[0].get("created_at") else None),
        "last_outbound": ({"timestamp": outbound[0]["created_at"].isoformat(),
                           "status": str(outbound[0]["status"])}
                          if outbound and outbound[0].get("created_at") else None),
    }


def build_kapso_status_router() -> APIRouter:
    router = APIRouter()

    @router.get(KAPSO_STATUS_ROUTE)
    async def kapso_status(request: Request):
        correlation = request.state.correlation_id
        composition = request.app.state.demo_composition
        try:
            context = await composition.resolver.resolve(
                request, composition.target_client_account_id)
        except TenantAuthenticationRequired:
            return envelope(401, "authentication_required", "authentication is required", correlation)
        except TenantAccessDenied:
            return envelope(403, "tenant_access_denied", "access denied", correlation)

        mapping = _phone_mapping()
        account_id = context.client_account.id
        phone_numbers = [
            {"phone_number_id": phone_id, "account_configured": mapped == account_id}
            for phone_id, mapped in mapping.items() if mapped == account_id
        ]
        configured = bool(os.getenv("KAPSO_API_KEY", "").strip())
        webhook_configured = bool(os.getenv("KAPSO_WEBHOOK_SECRET", "").strip()) and bool(phone_numbers)
        storage = await _storage_status(composition.crm.runner, account_id)
        payload = {
            "contract_version": "v1",
            "environment": "dev-only-kapso-status",
            "account_id": account_id,
            "correlation_id": correlation,
            "configured": configured,
            "webhook_configured": webhook_configured,
            "phone_numbers": phone_numbers,
            **storage,
            "worker": {
                "expected": configured and bool(phone_numbers),
                "status": "not_observed",
            },
        }
        return JSONResponse(status_code=200, content=payload,
                            headers={"X-Correlation-Id": correlation})

    return router
