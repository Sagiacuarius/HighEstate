"""Executable lifecycle/contract tests for the minimal FastAPI boundary.

Runs under pytest when available. Every scenario is also executable inline via
`python -m api.test_runtime` from the project root, which is how evidence is
produced in environments without a pytest runner.
"""

from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.testclient import TestClient

from api.app import ConfigurationError, app, load_configuration


def run_scenarios() -> list[tuple[str, bool, str]]:
    import os

    results: list[tuple[str, bool, str]] = []

    # 0a. Missing configuration fails fast before serving traffic.
    saved_host = os.environ.get("RUNTIME_API_HOST")
    saved_port = os.environ.get("RUNTIME_API_PORT")
    os.environ.pop("RUNTIME_API_HOST", None)
    os.environ.pop("RUNTIME_API_PORT", None)
    try:
        load_configuration()
        ok = False
        detail = "ConfigurationError not raised"
    except ConfigurationError as error:
        ok = "RUNTIME_API_HOST" in str(error) and "RUNTIME_API_PORT" in str(error)
        detail = str(error)
    results.append(("missing config fails fast naming variables", ok, detail))

    # 0b. Invalid port value fails fast.
    os.environ["RUNTIME_API_HOST"] = "127.0.0.1"
    os.environ["RUNTIME_API_PORT"] = "not-a-number"
    try:
        load_configuration()
        ok = False
        detail = "ConfigurationError not raised for bad port"
    except ConfigurationError as error:
        ok = "RUNTIME_API_PORT" in str(error)
        detail = str(error)
    results.append(("invalid port fails fast", ok, detail))

    # 0c. Host validation accepts loopback/wildcard/localhost/DNS and rejects impossible values.
    valid_hosts = ["127.0.0.1", "0.0.0.0", "localhost", "api.example.com"]
    valid_host_results = []
    for valid_host in valid_hosts:
        os.environ["RUNTIME_API_HOST"] = valid_host
        os.environ["RUNTIME_API_PORT"] = "8000"
        valid_host_results.append(load_configuration()["host"] == valid_host)
    invalid_hosts = ["", " 127.0.0.1", "bad host", "api/example", "999.999.999.999", "-invalid.example", "invalid-.example", "api_.example"]
    invalid_host_results = []
    for invalid_host in invalid_hosts:
        os.environ["RUNTIME_API_HOST"] = invalid_host
        os.environ["RUNTIME_API_PORT"] = "8000"
        try:
            load_configuration()
            invalid_host_results.append(False)
        except ConfigurationError:
            invalid_host_results.append(True)
    host_ok = all(valid_host_results) and all(invalid_host_results)
    results.append(("valid hosts accepted and invalid hosts rejected", host_ok, f"valid={valid_host_results} invalid={invalid_host_results}"))

    # Valid configuration for all remaining lifecycle scenarios.
    os.environ["RUNTIME_API_HOST"] = "127.0.0.1"
    os.environ["RUNTIME_API_PORT"] = "8000"

    async def real_blocked_request_during_shutdown() -> tuple[bool, str]:
        import asyncio

        blocked = asyncio.Event()
        release = asyncio.Event()

        async def blocked_endpoint(_request: Request):
            blocked.set()
            try:
                await release.wait()
                return {"status": "released"}
            except asyncio.CancelledError:
                raise

        app.add_api_route("/__test__/blocked", blocked_endpoint, methods=["GET"])
        receive_count = 0

        async def receive():
            nonlocal receive_count
            receive_count += 1
            if receive_count == 1:
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        lifecycle = app.router.lifespan_context(app)
        await lifecycle.__aenter__()
        request_task = asyncio.create_task(app({
            "type": "http", "method": "GET", "path": "/__test__/blocked", "raw_path": b"/__test__/blocked",
            "query_string": b"", "headers": [(b"x-correlation-id", b"blocked-request")],
            "scheme": "http", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 1),
            "root_path": "", "http_version": "1.1", "asgi": {"version": "3.0", "spec_version": "2.0"},
        }, receive, send))
        await asyncio.wait_for(blocked.wait(), timeout=1.0)
        registered = len(app.state.active_request_tasks) == 1 and app.state.active_requests == 1
        # Close the same lifecycle context while the real request remains blocked.
        # Its bounded drain must cancel the registered request task.
        await lifecycle.__aexit__(None, None, None)
        cancelled_or_finished = request_task.done()
        if not cancelled_or_finished:
            request_task.cancel()
        await asyncio.gather(request_task, return_exceptions=True)
        timed_out = bool(getattr(app.state, "drain_timed_out", False))
        return registered and cancelled_or_finished and timed_out, f"registered={registered} cancelled={cancelled_or_finished} drain_timed_out={timed_out}"

    # The direct ASGI task is a real blocked request, not a counter simulation.
    # Its test is run separately from the normal TestClient lifecycle.
    try:
        drain_ok, drain_detail = asyncio.run(real_blocked_request_during_shutdown())
    except Exception as error:
        drain_ok, drain_detail = False, f"{type(error).__name__}: {error}"
    results.append(("active request task drains/cancels during shutdown", drain_ok, drain_detail))

    with TestClient(app) as client:
        # 1. Liveness echoes correlation and returns the v1 contract.
        live = client.get("/health/live", headers={"X-Correlation-Id": "test-correlation"})
        payload = live.json()
        ok = (
            live.status_code == 200
            and payload["correlation_id"] == "test-correlation"
            and payload["contract_version"] == "v1"
            and live.headers.get("x-correlation-id") == "test-correlation"
        )
        results.append(("liveness echoes correlation + v1 contract", ok, str(payload)))

        # 2. Readiness is ok during an open lifecycle.
        ready = client.get("/health/ready", headers={"X-Correlation-Id": "ready-correlation"})
        ok = ready.status_code == 200 and ready.json()["status"] == "ok" and ready.headers.get("x-correlation-id") == "ready-correlation"
        results.append(("readiness ok inside lifecycle + header echo", ok, str(ready.json())))

        # 3. Correlation generated when missing.
        live = client.get("/health/live")
        generated = live.json()["correlation_id"]
        ok = live.status_code == 200 and generated.startswith("auto-")
        results.append(("correlation generated when missing", ok, generated))

        # 4. Oversized correlation rejected → regenerated server-side.
        oversized = "x" * 500
        live = client.get("/health/live", headers={"X-Correlation-Id": oversized})
        ok = live.status_code == 200 and len(live.json()["correlation_id"]) <= 128
        results.append(("oversized correlation replaced", ok, live.headers.get("x-correlation-id", "")))

        # 5. Idempotency metadata is stored on request.state, including explicit None.
        async def idempotency_state_probe(request: Request):
            return {"idempotency_key": getattr(request.state, "idempotency_key", "missing")}

        app.add_api_route("/__test__/idempotency-state", idempotency_state_probe, methods=["GET"])
        valid_idem = client.get("/__test__/idempotency-state", headers={"X-Idempotency-Key": "idem-123"})
        absent_idem = client.get("/__test__/idempotency-state")
        bad_idem = client.get("/__test__/idempotency-state", headers={"X-Idempotency-Key": "bad key"})
        bad_body = bad_idem.json()
        state_ok = (
            valid_idem.status_code == 200
            and valid_idem.json()["idempotency_key"] == "idem-123"
            and absent_idem.status_code == 200
            and absent_idem.json()["idempotency_key"] is None
            and bad_idem.status_code == 400
            and bad_body.get("error", {}).get("code") == "invalid_idempotency_key"
        )
        results.append(("idempotency state valid/absent/invalid", state_ok, f"valid={valid_idem.json()} absent={absent_idem.json()} invalid={bad_idem.status_code}"))

        # 6. Valid idempotency key is accepted on health.
        good_idem = client.get("/health/live", headers={"X-Idempotency-Key": "idem-123"})
        ok = good_idem.status_code == 200
        results.append(("valid idempotency key accepted", ok, ""))

        # 7. 404 uses the v1 envelope; error.correlation_id must equal echoed header.
        missing = client.get("/does-not-exist", headers={"X-Correlation-Id": "corr-404"})
        body = missing.json()
        ok = (
            missing.status_code == 404
            and body.get("error", {}).get("code") == "not_found"
            and body["error"]["correlation_id"] == "corr-404"
            and missing.headers.get("x-correlation-id") == body["error"]["correlation_id"]
        )
        results.append(("404 envelope: header matches error.correlation_id", ok, str(body)))

        # 8. 405 uses the v1 envelope with matching correlation.
        wrong_method = client.post("/health/live", headers={"X-Correlation-Id": "corr-405"})
        body = wrong_method.json()
        ok = (
            wrong_method.status_code == 405
            and body.get("error", {}).get("code") == "method_not_allowed"
            and body["error"]["correlation_id"] == "corr-405"
            and wrong_method.headers.get("x-correlation-id") == "corr-405"
        )
        results.append(("405 envelope: header matches error.correlation_id", ok, str(body)))

    # 10. After a full lifecycle close, the service must refuse traffic:
    #     either the middleware shutting_down envelope or readiness=unavailable.
    closed = TestClient(app)
    with closed:
        pass
    after = TestClient(app)
    response = after.get("/health/ready", headers={"X-Correlation-Id": "after-shutdown"})
    body = response.json()
    middleware_refusal = (
        response.status_code == 503
        and body.get("error", {}).get("code") == "shutting_down"
        and response.headers.get("x-correlation-id") == "after-shutdown"
    )
    readiness_unavailable = response.status_code == 503 and body.get("status") == "unavailable"
    results.append(("graceful shutdown refuses traffic", middleware_refusal or readiness_unavailable, f"{response.status_code} {str(body)[:120]}"))
    after.close()

    return results


if __name__ == "__main__":
    outcomes = run_scenarios()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    print(f"FASTAPI SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)


def test_all_scenarios_pass():
    """Pytest entry point: every scenario must hold."""
    failures = [name for name, ok, _ in run_scenarios() if not ok]
    assert failures == [], f"failing scenarios: {failures}"
