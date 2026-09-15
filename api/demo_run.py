"""Dev-only demo server for the WU8 real-data dashboard.

Run from the project root: python -m api.demo_run

Serves the WU8 dashboard composition (selector + full session-scoped reads +
seed/reset controls) over real PostgreSQL. Fail-fast configuration, no silent
defaults: RUNTIME_API_HOST / RUNTIME_API_PORT plus the standard
RUNTIME_DB_HOST / RUNTIME_DB_PORT / RUNTIME_DB_NAME / RUNTIME_DB_USER
(password via libpq pgpass, never read here).
"""

from __future__ import annotations

import asyncio
import os
import sys

import uvicorn


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"startup failed: missing required configuration: {name}", file=sys.stderr)
        raise SystemExit(2)
    return value


def _selector_loop() -> asyncio.AbstractEventLoop:
    """Explicit SelectorEventLoop: uvicorn 0.41's default win32 loop factory is
    Proactor, which psycopg async cannot use, and passing a factory here beats
    the policy (uvicorn overrides it when loop != 'none')."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def main() -> int:
    from api.adapters.postgres_core import load_database_configuration
    from api.composition_wu8_mvp import build_mvp_wu8_application
    from api.identity.demo_identity import DemoIdentityAdapter

    host = _require("RUNTIME_API_HOST")
    port_raw = _require("RUNTIME_API_PORT")
    try:
        port = int(port_raw)
    except ValueError:
        print("startup failed: RUNTIME_API_PORT must be an integer", file=sys.stderr)
        return 2
    if not 1 <= port <= 65535:
        print("startup failed: RUNTIME_API_PORT must be between 1 and 65535", file=sys.stderr)
        return 2
    try:
        conn_kwargs = load_database_configuration()
    except Exception as error:
        print(f"startup failed: {error}", file=sys.stderr)
        return 2

    target = os.environ.get("RUNTIME_DEMO_ACCOUNT", "").strip() or "20000000-0000-0000-0000-000000000001"
    app = build_mvp_wu8_application(conn_kwargs, target, DemoIdentityAdapter())
    # Custom loop import string: uvicorn resolves "api.demo_run:_selector_loop"
    # via its loop-factory hook, keeping psycopg async usable on win32.
    uvicorn.run(app, host=host, port=port, reload=False, loop="api.demo_run:_selector_loop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
