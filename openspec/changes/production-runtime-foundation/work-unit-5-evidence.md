# Production Runtime Foundation — Work Unit 5 Evidence

## Status

Work Unit 5 was implemented and verified against real PostgreSQL 18.6. **Accepted for transactional command + durable idempotency slice (psycopg 3.2.9 against production_runtime_wu4, 7/7 including concurrent replay/conflict).**

## Scope

Implemented the atomic command boundary:

```text
authorized TenantContext
→ idempotency reservation
→ tenant-scoped mutation
→ result persistence
→ commit
```

Replay returns the stored result without a second mutation. Reuse of the same actor/operation/key with a different request hash returns a non-enumerating 409 conflict and does not mutate the resource. No provider or external call exists inside the transaction.

No audit/outbox runtime, RLS, OAuth/MFA, production authentication, Docker, CI/CD, deployment, WACRM or product functionality was added.

## Files created

```text
infra/migrations/002_command_idempotency.up.sql
infra/migrations/002_command_idempotency.down.sql
infra/migrations/validation/wu5_reset.sql
api/adapters/postgres_idempotency.py
api/command_http.py
api/composition_wu5.py
api/test_command_idempotency.py
openspec/changes/production-runtime-foundation/work-unit-5-evidence.md
```

## Files modified

```text
api/requirements.txt                 # psycopg[binary]==3.2.9 already used by WU4, now explicitly retained
api/adapters/postgres_core.py       # Windows async event-loop compatibility
api/tenant_resources.py              # WU4 endpoint rejects spoofed client_account_id before write
infra/migrations/validation/wu4_seed.sql  # valid UUID-only WU4 fixture identifiers/reset cleanup
```

No Work Unit 1/2/3/3.5 application contracts or migration schema files were modified. The WU4 fixture correction was required only because the earlier validation fixture contained non-UUID placeholder IDs; the database schema migration was untouched.

## Migration 002

### `command_idempotency`

Columns:

```text
id
actor_id
operation
idempotency_key
request_hash
result_payload
status
created_at
updated_at
```

Constraints:

```text
actor_id REFERENCES users(id) ON DELETE RESTRICT
CHECK btrim(operation) <> ''
CHECK btrim(idempotency_key) <> ''
CHECK btrim(request_hash) <> ''
CHECK status IN ('processing', 'completed')
UNIQUE (actor_id, operation, idempotency_key)
```

Indexes:

```text
(actor_id, created_at, id)
(operation, created_at, id)
```

`002_command_idempotency.up.sql` is transactional. `002_command_idempotency.down.sql` is forward-history-compatible only as an operator-reviewed destructive rollback. It is not an automatic application rollback. Audit/outbox tables were intentionally not added; those remain Work Unit 6.

## Adapter and command handler

### `PostgresCommandConnection`

Opens a transaction connection with psycopg 3.2.9:

```text
autocommit=False
row_factory=dict_row
```

### `TransactionalUpdateTenantResource`

The command performs, in one PostgreSQL transaction:

1. Validate server-derived `TenantContext` and write permission.
2. `INSERT ... ON CONFLICT (actor_id, operation, idempotency_key) DO NOTHING RETURNING id`.
3. If reservation already exists, `SELECT ... FOR UPDATE`, compare `request_hash`, and replay or conflict.
4. For a new key, update `tenant_resource` with:

```sql
WHERE id = %s AND client_account_id = %s
```

where the account predicate is derived only from `TenantContext`.

5. Store `result_payload` and set status `completed`.
6. Commit mutation and idempotency result together.

On any exception, the transaction rolls back, including a newly inserted reservation. No external provider call exists in this code path.

### HTTP wiring

Created a minimal internal route:

```text
PUT /commands/tenant-resources/{resource_id}
```

Behavior:

```text
missing X-Idempotency-Key → 400 invalid_idempotency_key
same key + same body       → 200, replayed false then true
same key + different body  → 409 tenant_idempotency_conflict
```

The request hash is computed server-side from canonical JSON. The endpoint uses the validated idempotency key from request metadata and never accepts user, agency, account or permission authority from the body.

## Database

```text
Host:     127.0.0.1
Port:     5432
Database: production_runtime_wu4
User:     postgres
Server:   PostgreSQL 18.6
```

The existing local pgpass configuration supplied authentication. No password was printed, logged, copied into code or included here. `production_runtime_local` was not used or destroyed.

## Tests

### New command/idempotency test

Command:

```text
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_wu4 RUNTIME_DB_USER=postgres python -m api.test_command_idempotency
```

Result:

```text
exit code: 0
COMMAND IDEMPOTENCY SCENARIOS: 7/7 passed
```

Exact scenario results:

```text
PASS new command mutates and commits
PASS same key and hash replays stored result without mutation
PASS same key different hash conflicts without mutation
PASS five concurrent same key yields one mutation and four replays :: fresh=1 replays=4 idem={'count': 1, 'completed': 1}
PASS same key different hashes yields one winner and four conflicts :: winner=1 conflicts=4 idem={'count': 1}
PASS HTTP new/replay/conflict behavior and 409 envelope :: 200/200/409
PASS missing idempotency header rejected :: 400 invalid_idempotency_key
```

The five-worker concurrency test used independent concurrent PostgreSQL transactions and proved:

```text
one mutation
four replays
one idempotency row
one completed result
```

The conflicting-hash concurrency test proved:

```text
one winner
four conflicts
one idempotency row
no conflicting mutation
```

The HTTP conflict test verified:

```text
HTTP 409
error.code = tenant_idempotency_conflict
X-Correlation-Id exists
X-Correlation-Id == error.correlation_id
```

## Required existing verification

### TypeScript typecheck

```text
npm.cmd run typecheck
exit code: 0
```

### TypeScript tests

```text
npm.cmd test
exit code: 0
Test Files  5 passed (5)
Tests       27 passed (27)
Start at  16:02:19
Duration  1.89s
```

### Existing FastAPI scenarios

```text
RUNTIME_API_HOST=127.0.0.1 RUNTIME_API_PORT=8000 python -m api.test_runtime
exit code: 0
FASTAPI SCENARIOS: 13/13 passed
```

### Existing WU4 PostgreSQL repository scenarios

```text
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_wu4 RUNTIME_DB_USER=postgres python -m api.test_postgres_repositories
exit code: 0
POSTGRES WU4 SCENARIOS: 19/19 passed
```

The FastAPI test processes emit a Starlette deprecation warning concerning `httpx`/`httpx2`; no test was affected.

## Real database schema verification

The final database inspection showed:

```text
command_idempotency: present
actor_id: uuid NOT NULL, FK to users
operation: text NOT NULL
idempotency_key: text NOT NULL
request_hash: text NOT NULL
result_payload: jsonb
status: text NOT NULL
created_at/updated_at: timestamptz NOT NULL
unique actor/operation/key constraint present
request_hash non-empty CHECK present
```

The final test database retained only the expected completed command records from the final HTTP scenario. Test reset runs before each command-test phase clear command idempotency rows and reset the demo resource.

## Limitations and exclusions

- No audit/outbox durable logic was added; that belongs to Work Unit 6.
- No RLS is implemented.
- No application transaction boundary beyond this command exists.
- No production authentication or provider integration exists.
- In-memory identity injection remains test-only.
- No durable idempotency exists outside the implemented command path.
- Rows left in `processing` after a crash-before-commit have no TTL/recovery job; next retry currently receives immediate `409 tenant_idempotency_conflict` until manual intervention (future recovery belongs to WU6/runner).
- No migration runner/ledger exists; duplicate application of migration 002 must be prevented by the future migration runner.
- The local command connection opens/closes one psycopg async connection per command; no production pool is claimed.
- No external calls are allowed/implemented within the transaction.
- No production readiness or deployment claim is made.
- No application-wide isolation claim beyond the real scenarios listed here is made.

## WACRM

WACRM was not used. No code was copied, executed or added as a dependency.

## Acceptance status

Work Unit 5 is **accepted for transactional command + durable idempotency slice** against real PostgreSQL 18.6 with 7/7 command/idempotency scenarios, including concurrent replay and conflict behavior. Work Unit 6 audit/outbox, production authentication, RLS, production pooling and deployment remain outside this unit.
