# Production Runtime Foundation — Work Unit 1 rev 4 Evidence

## Status

Revision 4 after rejection. The requested idempotency-state correction was implemented and verified. **Not declared accepted**; this document is ready for review only.

## Scope

This work unit provides the smallest local boundary for a Next.js web entrypoint and FastAPI API while keeping the existing TypeScript domain/application independent.

No authentication, PostgreSQL, persistence, Docker, CI/CD, WACRM, CRM, WhatsApp, agents, LangGraph, automations, meetings, forms or product UI was added.

## 1. Graceful shutdown evidence

`api/app.py` now tracks real request tasks:

- Every request middleware execution obtains `asyncio.current_task()`.
- `app.state.active_request_tasks` contains every active request asyncio task.
- `active_requests` remains as the numeric count.
- Both are updated in `finally`; no separate `active_work` collection exists.
- On shutdown, lifecycle changes to `draining`, `accepting_requests` becomes false and readiness becomes false before waiting.
- The shutdown waits on a snapshot of the registered task set with the bounded `DRAIN_TIMEOUT_SECONDS = 5.0`.
- If the timeout expires, remaining tasks are cancelled and gathered with `return_exceptions=True`.
- `app.state.drain_timed_out` records whether the bounded window expired.
- Requests arriving during draining receive HTTP 503 with the v1 `shutting_down` error envelope.

The test scenario creates a real blocked ASGI request task against a test-only route, verifies it is present in `active_request_tasks`, closes the same lifespan while it is blocked, and verifies the task finishes by cancellation and `drain_timed_out=True`. It also verifies a late request is refused. This is not a counter-only simulation.

## 2. Formal lockfile strategy

The project uses npm workspaces:

```json
"workspaces": ["web"]
```

Strategy:

- Root `package.json` is the package-manager boundary.
- Root `package-lock.json` is the sole active lockfile.
- `web/package.json` is the workspace manifest.
- `web/package-lock.json` is deleted and must not be recreated/committed.
- Root scripts invoke workspace commands, including `npm.cmd --workspace web run build`.

After clean removal of `node_modules` and the nested lockfile, the root lockfile was regenerated and `npm.cmd ci` completed successfully. Final filesystem check showed only:

```text
./package-lock.json
```

No duplicate active lockfile remains.

## 3. Fail-fast configuration

FastAPI requires these environment values before startup:

```text
RUNTIME_API_HOST
RUNTIME_API_PORT
```

`load_configuration()` now validates the host before Uvicorn/lifespan startup. It accepts `127.0.0.1`, `0.0.0.0`, `localhost` and valid DNS names, and rejects empty/whitespace, embedded spaces, slashes, invalid labels and impossible numeric IPs. Port validation remains integer/range validated.

The final FastAPI scenarios include:

```text
valid=[True, True, True, True]
invalid=[True, True, True, True, True, True, True, True]
```

The lifespan calls configuration validation before marking the app ready. `python -m api.run` calls it before invoking Uvicorn.

Web requires:

```text
RUNTIME_API_BASE_URL
```

`web/runtime-config.ts` validates at configuration load time using `URL`: http/https only, host required, embedded credentials rejected. `web/app/page.tsx` has no fallback. Missing, blank or `not-a-url` values throw `RUNTIME_API_BASE_URL` configuration errors.

## 4. Idempotency state evidence

The final FastAPI scenario verifies the downstream request state explicitly:

```text
valid={'idempotency_key': 'idem-123'}
absent={'idempotency_key': None}
invalid=400
```

The middleware initializes `request.state.idempotency_key = None` for every accepted request, replaces it with the validated `X-Idempotency-Key` when present, and returns a 400 v1 error envelope before handler execution for an invalid key. No replay, persistence or durable idempotency is implemented.

## 5. Verification commands and exact results

Executed from:

```text
C:\Users\Windows\Documents\AI Agency Platform
```

### Clean install

```text
npm.cmd ci
exit code: 0
added 74 packages, audited 76 packages
```

npm reported 3 high severity advisories in the installed dependency graph and blocked benign `esbuild`/`sharp` install scripts under the local allowScripts policy. These are recorded limitations; no `npm audit fix --force` was run because it could change dependency versions outside this correction scope.

### TypeScript typecheck

```text
npm.cmd run typecheck
exit code: 0
```

### TypeScript/Vitest tests

```text
npm.cmd test
exit code: 0
Test Files  5 passed (5)
Tests       27 passed (27)
Start       19:06:13
Duration    1.21s
```

Breakdown:

```text
contracts/runtime-v1.test.ts     5
web/runtime-client.test.ts      13
web/runtime-config.test.ts       2
src/domain/tenant                1
src/adapters/in-memory            6
```

### Next.js build through workspace

```text
RUNTIME_API_BASE_URL=http://127.0.0.1:8000 npm.cmd --workspace web run build
exit code: 0
Next.js 15.5.23
Route /            dynamic, 102 kB first load
Route /_not-found  static, 103 kB first load
```

The build was executed through the npm workspace command. The build without the required variable was previously observed to fail explicitly with `Missing required configuration: RUNTIME_API_BASE_URL`, as intended.

### FastAPI executable scenarios

`python -m api.test_runtime` was executed from the project root with valid API configuration:

```text
FASTAPI SCENARIOS: 13/13 passed
exit code: 0
```

Relevant final output:

```text
PASS active request task drains/cancels during shutdown :: registered=True cancelled=True drain_timed_out=True
PASS idempotency state valid/absent/invalid :: valid={'idempotency_key': 'idem-123'} absent={'idempotency_key': None} invalid=400
PASS graceful shutdown refuses traffic :: 503 shutting_down
```

The standard pytest runner remains unavailable in the environment (`No module named pytest`; pip unavailable), so `api/test_runtime.py` was executed through its module entrypoint and the real FastAPI `TestClient` scenarios. This is documented rather than presented as pytest execution.

### FastAPI module entrypoint

```text
python -m api.run
```

From the project root:

- With `RUNTIME_API_HOST=127.0.0.1` and `RUNTIME_API_PORT=8000`, Uvicorn started on `http://127.0.0.1:8000` and a live health request returned HTTP 200 with correlation echo.
- Without those variables, it printed only `startup failed: missing required configuration: RUNTIME_API_HOST, RUNTIME_API_PORT` and returned exit code 2.

No `python api/run.py` command is documented or claimed as valid.

## 5. Contract and client corrections

`contracts/runtime-v1.ts` defines the shared v1 envelope, correlation limit 128, idempotency limit 256, printable ASCII header validation, liveness/readiness structural parsers and optional idempotency metadata.

`RuntimeHttpClient`:

- Uses the `URL` parser for `baseUrl`.
- Allows only HTTP/HTTPS.
- Requires a host.
- Rejects embedded username/password credentials.
- Rejects malformed and empty values.
- Propagates `X-Correlation-Id` and optional `X-Idempotency-Key`.
- Validates liveness/readiness payloads structurally.
- Parses every non-2xx error as the v1 envelope.
- Requires the error response header `X-Correlation-Id` and compares it to `error.correlation_id`.
- Rejects missing or inconsistent error correlation headers.
- Rejects malformed success payloads and mismatched success correlation headers.

## 6. Files created/modified

Created:

```text
web/app/layout.tsx
web/app/page.tsx
web/package.json
web/runtime-config.ts
web/runtime-config.test.ts
web/runtime-client.test.ts
```

Modified:

```text
package.json
package-lock.json
contracts/runtime-v1.ts
contracts/runtime-v1.test.ts
web/runtime-client.ts
api/app.py
api/run.py
api/test_runtime.py
openspec/changes/production-runtime-foundation/work-unit-1-evidence.md
```

Removed:

```text
web/package-lock.json
```

No domain or application files were modified.

## 7. Local verification commands

```bash
npm.cmd ci
npm.cmd run typecheck
npm.cmd test
RUNTIME_API_BASE_URL=http://127.0.0.1:8000 npm.cmd --workspace web run build
RUNTIME_API_HOST=127.0.0.1 RUNTIME_API_PORT=8000 python -m api.test_runtime
```

## 8. Dependencies

The root package has the existing TypeScript/Vitest dependencies plus the minimal Next.js workspace dependency set:

```text
next ^15.5.23
react ^19.2.8
react-dom ^19.2.8
```

Workspace type packages are declared in `web/package.json`:

```text
@types/node 20.17.6
@types/react 19.2.18
```

No backend runtime dependency was added to npm. Python requirements remain declarative in `api/requirements.txt`; no Python package installation was performed by this correction.

## 8. Limitations and deferred decisions

- No real authentication or trusted identity provider.
- No PostgreSQL, persistence, durable idempotency, transactions or outbox.
- No production readiness dependency checks; readiness is local lifecycle state only.
- PostgreSQL, Docker Compose, authentication, persistence and CI/CD are explicitly deferred to later work units (`persistent-tenancy`, `postgres-tenant-isolation`, `compose-environment`, `trusted-auth-context`, and deployment work).
- No Docker, CI/CD, deployment packaging or production observability.
- FastAPI test scenarios pass 13/13 via `python -m api.test_runtime`.
- Dependency audit reports 3 high severity advisories and install-script policy warnings; no `npm audit fix --force` was run.
- The target directory has no `.git`; no historical Git diff or diff verification is claimed.
- The request task set is process-local and applies to this FastAPI process only; distributed draining is future infrastructure work.

## 9. WACRM status

WACRM remains an audited architectural reference only:

- No source copied.
- No code executed.
- No dependency added.
- No adapter created.
- No fork created.

## Acceptance status

The requested adjustments are implemented and verified. **Work Unit 1 rev 4 is not declared accepted.** This revision is ready for review and does not declare production readiness.
