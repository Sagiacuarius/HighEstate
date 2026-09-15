"""Minimal FastAPI boundary for the production-runtime foundation.

HTTP-only translation layer: no domain, persistence or auth logic lives here.
Contract limits mirror contracts/runtime-v1.ts.

Run from the project root with:  python -m api.run
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.http_contracts import envelope

SERVICE_NAME = "production-runtime-api"
CONTRACT_VERSION = "v1"
CORRELATION_ID_MAX_LENGTH = 128
IDEMPOTENCY_KEY_MAX_LENGTH = 256

# Bounded drain window for graceful shutdown (seconds).
DRAIN_TIMEOUT_SECONDS = 5.0


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid at startup."""


class InvalidIdempotencyKey(Exception):
    """Raised when a supplied X-Idempotency-Key is malformed."""


def _validate_header_value(value: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("empty")
    if len(normalized) > max_length:
        raise ValueError("too long")
    # Mirrors contracts/runtime-v1.ts HEADER_SAFE_PATTERN: printable ASCII 0x21-0x7E only.
    allowed = set(chr(code) for code in range(0x21, 0x7F))
    if any(char not in allowed for char in normalized):
        raise ValueError("invalid characters")
    return normalized


def validate_api_host(value: str) -> str:
    host = value.strip()
    if not host or host != value or any(char.isspace() for char in host):
        raise ConfigurationError("RUNTIME_API_HOST must be a valid host")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if len(host) > 253:
        raise ConfigurationError("RUNTIME_API_HOST must be a valid host")
    if host.lower() == "localhost":
        return host
    labels = host.rstrip(".").split(".")
    if not labels or any(
        not label or len(label) > 63 or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    ):
        raise ConfigurationError("RUNTIME_API_HOST must be a valid host")
    if all(label.isdigit() for label in labels):
        raise ConfigurationError("RUNTIME_API_HOST must be a valid IP or DNS host")
    return host


def load_configuration() -> dict[str, str]:
    """Fail-fast startup validation. No silent defaults for critical values.

    Required environment:
      RUNTIME_API_HOST  — bind address (e.g. 127.0.0.1)
      RUNTIME_API_PORT  — bind port (integer)
    """
    host_raw = os.environ.get("RUNTIME_API_HOST", "")
    host = host_raw.strip()
    port_raw = os.environ.get("RUNTIME_API_PORT", "").strip()
    missing = [name for name, value in (("RUNTIME_API_HOST", host), ("RUNTIME_API_PORT", port_raw)) if not value]
    if missing:
        raise ConfigurationError(f"missing required configuration: {', '.join(missing)}")
    host = validate_api_host(host_raw)
    try:
        port = int(port_raw)
    except ValueError as error:
        raise ConfigurationError("RUNTIME_API_PORT must be an integer") from error
    if not (1 <= port <= 65535):
        raise ConfigurationError("RUNTIME_API_PORT must be between 1 and 65535")
    return {"host": host, "port": str(port)}


def correlation_id(request: Request) -> str:
    """Validated X-Correlation-Id; generates one when missing/invalid."""
    raw = request.headers.get("x-correlation-id", "")
    try:
        return _validate_header_value(raw, CORRELATION_ID_MAX_LENGTH)
    except ValueError:
        generated = f"auto-{uuid4().hex[:12]}"
        request.state.correlation_id_generated = True
        return generated


def optional_idempotency_key(request: Request) -> str | None:
    """Validated X-Idempotency-Key or None. Never generated implicitly."""
    raw = request.headers.get("x-idempotency-key")
    if raw is None or not raw.strip():
        return None
    try:
        return _validate_header_value(raw, IDEMPOTENCY_KEY_MAX_LENGTH)
    except ValueError as error:
        raise InvalidIdempotencyKey(str(error)) from error


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = load_configuration()  # fail-fast before serving any traffic
    app.state.config = config
    app.state.lifecycle = "starting"
    app.state.ready = False
    app.state.accepting_requests = False
    app.state.active_requests = 0
    app.state.active_request_tasks: set[asyncio.Task[object]] = set()
    app.state.lifecycle = "ready"
    app.state.ready = True
    app.state.accepting_requests = True
    try:
        yield
    finally:
        # Graceful shutdown: stop accepting new work, then wait a bounded time
        # for active requests to finish; cancel whatever remains.
        app.state.lifecycle = "draining"
        app.state.accepting_requests = False
        app.state.ready = False
        deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
        drain_timed_out = False
        while app.state.active_request_tasks and asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            tasks = tuple(app.state.active_request_tasks)
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=remaining)
            except asyncio.TimeoutError:
                drain_timed_out = True
                break
        remaining_tasks = tuple(app.state.active_request_tasks)
        if remaining_tasks:
            drain_timed_out = True
        for task in remaining_tasks:
            if not task.done():
                task.cancel()
        if remaining_tasks:
            await asyncio.gather(*remaining_tasks, return_exceptions=True)
        app.state.drain_timed_out = drain_timed_out
        app.state.active_request_tasks.clear()
        app.state.lifecycle = "stopped"


app = FastAPI(title=SERVICE_NAME, version=CONTRACT_VERSION, lifespan=lifespan)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    if not getattr(app.state, "accepting_requests", False):
        return envelope(
            503,
            "shutting_down",
            "Service is shutting down",
            correlation_id(request),
        )
    correlation = correlation_id(request)
    request.state.correlation_id = correlation
    request.state.idempotency_key = None
    try:
        request.state.idempotency_key = optional_idempotency_key(request)
    except InvalidIdempotencyKey:
        return envelope(400, "invalid_idempotency_key", "X-Idempotency-Key is malformed", correlation)
    task = asyncio.current_task()
    if task is None:
        return envelope(500, "internal_error", "Request task unavailable", correlation)
    app.state.active_requests += 1
    app.state.active_request_tasks.add(task)
    try:
        response = await call_next(request)
    finally:
        app.state.active_request_tasks.discard(task)
        app.state.active_requests -= 1
    response.headers["X-Correlation-Id"] = correlation
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, _error: Exception) -> JSONResponse:
    return envelope(500, "internal_error", "Internal server error", getattr(request.state, "correlation_id", ""))


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc) -> JSONResponse:
    return envelope(404, "not_found", "Resource not found", getattr(request.state, "correlation_id", ""))


@app.exception_handler(405)
async def method_not_allowed_handler(request: Request, _exc) -> JSONResponse:
    return envelope(405, "method_not_allowed", "Method not allowed", getattr(request.state, "correlation_id", ""))


@app.get("/health/live")
async def live(request: Request) -> dict[str, str]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "contract_version": CONTRACT_VERSION,
        "correlation_id": getattr(request.state, "correlation_id", ""),
    }


@app.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    is_ready = bool(getattr(app.state, "ready", False))
    payload = {
        "status": "ok" if is_ready else "unavailable",
        "service": SERVICE_NAME,
        "contract_version": CONTRACT_VERSION,
        "correlation_id": getattr(request.state, "correlation_id", ""),
        "dependencies": {},
    }
    return JSONResponse(
        status_code=200 if is_ready else 503,
        headers={"X-Correlation-Id": getattr(request.state, "correlation_id", "")},
        content=payload,
    )


from api.tenant_boundary import router as tenant_router

app.include_router(tenant_router)

