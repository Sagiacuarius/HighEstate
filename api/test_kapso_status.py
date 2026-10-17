"""Direct-invokable focused checks for the Kapso status projection."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from api.routes.kapso_status import _storage_status


class _Runner:
    async def query(self, sql, _params):
        if "GROUP BY status" in sql:
            return [{"status": "pending", "count": 2}, {"status": "failed", "count": 1}]
        if "processed_at" in sql:
            return [{"count": 3}]
        if "direction = 'inbound'" in sql:
            return [{"created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
        return [{"created_at": datetime(2026, 1, 2, tzinfo=timezone.utc), "status": "published"}]


class _UnavailableRunner:
    async def query(self, _sql, _params):
        raise RuntimeError("table unavailable")


def test_status_projection_has_counts_and_no_message_body():
    result = asyncio.run(_storage_status(_Runner(), "account-1"))
    assert result["outbox"] == {"pending": 2, "processing": 0, "failed": 1, "published_recent": 3}
    assert result["last_inbound"]["status"] == "persisted"
    assert "body" not in result["last_inbound"]
    assert result["last_outbound"]["status"] == "published"


def test_status_projection_returns_typed_unavailable_storage():
    result = asyncio.run(_storage_status(_UnavailableRunner(), "account-1"))
    assert result["outbox"] == {"status": "unavailable", "code": "storage_unavailable"}
    assert result["last_inbound"] == result["outbox"]


if __name__ == "__main__":
    test_status_projection_has_counts_and_no_message_body()
    test_status_projection_returns_typed_unavailable_storage()
    print("kapso status direct checks: 2 passed")
