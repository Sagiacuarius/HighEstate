"""Kapso inbound webhook route over the existing CRM command boundary."""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.adapters.kapso_inbound import (
    KAPSO_EVENT,
    EnvKapsoTenantMapping,
    KapsoPayloadInvalid,
    KapsoTenantMapping,
    parse_kapso_payload,
    verify_kapso_signature,
)
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.crm_commands_wu5 import CrmCommandDenied, CrmCommandValidationError, ProcessInboundMessageCommand
from api.tenant_boundary import TenantAccessDenied

KAPSO_ROUTE = "/v1/webhooks/kapso"


def build_kapso_router(conn_kwargs: dict[str, Any], mapping: KapsoTenantMapping | None = None,
                       webhook_secret: str | None = None, command: Any | None = None) -> APIRouter:
    router = APIRouter()
    command = command or ProcessInboundMessageCommand(dict(conn_kwargs))
    tenant_mapping = mapping or EnvKapsoTenantMapping.from_environment()
    secret = webhook_secret if webhook_secret is not None else os.getenv("KAPSO_WEBHOOK_SECRET", "")

    @router.post(KAPSO_ROUTE)
    async def kapso_webhook(request: Request):
        raw = await request.body()
        if not verify_kapso_signature(raw, request.headers.get("X-Webhook-Signature"), secret):
            return JSONResponse(status_code=401, content={"accepted": False, "error": "invalid_signature"})
        event_name = request.headers.get("X-Webhook-Event", "").strip()
        if event_name != KAPSO_EVENT:
            return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
        candidates = payload.get("data") if isinstance(payload, dict) and payload.get("batch") is True else [payload]
        if not isinstance(candidates, list) or not candidates:
            return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
        phone_ids = set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            metadata_phone_id = metadata.get("phone_number_id") if isinstance(metadata, dict) else None
            phone_id = item.get("phone_number_id") or metadata_phone_id
            if isinstance(phone_id, str) and phone_id:
                phone_ids.add(phone_id)
        if len(phone_ids) != 1 or not next(iter(phone_ids), None):
            return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
        account_id = tenant_mapping.account_for(next(iter(phone_ids)))
        if not account_id:
            return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
        composition = request.app.state.demo_composition
        try:
            context = await composition.resolver.resolve_service(
                account_id, getattr(request.state, "correlation_id", "")
            )
        except TenantAccessDenied:
            return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
        try:
            messages = parse_kapso_payload(payload, account_id=context.client_account.id)
        except KapsoPayloadInvalid:
            return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
        delivery_key = request.headers.get("X-Idempotency-Key", "").strip()
        if delivery_key and len(delivery_key) > 256:
            return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
        results = []
        for message in messages:
            try:
                idempotency_key = delivery_key or f"kapso:{message.external_message_id}"
                if len(messages) > 1 and delivery_key:
                    idempotency_key = f"{delivery_key}:{message.external_message_id}"
                outcome = await command.execute_event(
                    context, event=message.event, idempotency_key=idempotency_key
                )
            except (CrmCommandDenied, CrmCommandValidationError, IdempotencyConflict):
                return JSONResponse(status_code=202, content={"accepted": False, "ignored": True})
            results.append({"external_message_id": message.external_message_id, "replayed": outcome.replayed})
        return JSONResponse(status_code=200, content={"accepted": True, "results": results})

    return router
