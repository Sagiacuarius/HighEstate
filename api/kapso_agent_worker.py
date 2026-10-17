"""Production entrypoint for the Kapso inbound agent outbox consumer."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

from api.adapters.postgres_audit_outbox import PostgresAuditOutboxRepository
from api.adapters.postgres_core import ConnectionPool, PostgresRunner, load_database_configuration
from api.application.kapso_agent_consumer import build_kapso_agent_consumer
from api.application.outbox_dispatcher import OutboxDispatcher

logger = logging.getLogger(__name__)


async def dispatch_once(conn_kwargs: dict[str, Any], runner: Any, limit: int = 20,
                       lease_seconds: int = 60) -> int:
    consumer = build_kapso_agent_consumer(conn_kwargs, runner)
    dispatcher = OutboxDispatcher(conn_kwargs, PostgresAuditOutboxRepository(), consumer)
    return await dispatcher.dispatch_once(
        limit=limit, lease_seconds=lease_seconds,
        event_types=("InboundMessageProcessed",),
    )


def _interval_seconds() -> float:
    raw = os.getenv("KAPSO_WORKER_INTERVAL_SECONDS", "5").strip()
    try:
        interval = float(raw)
    except ValueError as error:
        raise ValueError("KAPSO_WORKER_INTERVAL_SECONDS must be a number") from error
    if interval <= 0:
        raise ValueError("KAPSO_WORKER_INTERVAL_SECONDS must be positive")
    return interval


async def _run() -> None:
    conn_kwargs = load_database_configuration()
    interval = _interval_seconds()
    runner = PostgresRunner(ConnectionPool(dict(conn_kwargs)))
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()

    def stop() -> None:
        stopped.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop)
        except (NotImplementedError, OSError):
            signal.signal(signum, lambda _signum, _frame: stopped.set())
    try:
        logger.info("Kapso agent worker started")
        while not stopped.is_set():
            await dispatch_once(conn_kwargs, runner)
            try:
                await asyncio.wait_for(stopped.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        await runner.close()
        logger.info("Kapso agent worker stopped")


def main() -> None:
    try:
        asyncio.run(_run())
    except Exception as error:
        logger.error("Kapso agent worker startup/runtime failure: %s", error)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
