# Production Runtime Foundation — Work Unit 4 Evidence

## Status

Work Unit 4 implementation and real PostgreSQL verification are complete. **Accepted for repository/isolation slice (psycopg 3.2.9 against production_runtime_wu4, 19/19).** No production authentication, RLS or production readiness is claimed.

## Scope

Implemented real PostgreSQL adapters using direct `psycopg` SQL, plus a minimal internal test boundary for tenant-scoped resource wiring.

The adapter layer covers:

```text
Agency/ClientAccount resolution
User/identity-link lookup
Membership resolution
TenantContext resolution through the accepted WU2 service
TenantResource read/list/create/update/delete
```

No ORM, Supabase, RLS, command transaction boundary, durable idempotency, audit/outbox runtime, Docker, CI/CD, deploy, WACRM or product functionality was added.

## Files created

```text
api/adapters/__init__.py
api/adapters/postgres_core.py
api/adapters/postgres_tenancy.py
api/adapters/postgres_resources.py
api/composition_wu4.py
api/tenant_resources.py
api/test_postgres_repositories.py
infra/migrations/validation/wu4_seed.sql
```

## Files modified

```text
api/requirements.txt
```

The accepted Work Unit 1/2 application files, runtime contracts, migrations and web files were not modified for this unit.

## Driver and configuration

Driver:

```text
psycopg 3.2.9
psycopg-binary 3.2.9
```

Dependency registered in `api/requirements.txt`:

```text
psycopg[binary]==3.2.9
```

No ORM is used. SQL is parameterized through psycopg.

Required adapter configuration:

```text
RUNTIME_DB_HOST
RUNTIME_DB_PORT
RUNTIME_DB_NAME
RUNTIME_DB_USER
```

The password is intentionally not a configuration variable in code. Local libpq authentication uses the existing Windows pgpass file. No password was printed, stored in the repository, logged or included in this evidence.

Fail-fast configuration test output:

```text
host/port/db/user present, no password field: ['dbname', 'host', 'port', 'user']
```

## Test database

```text
Database: production_runtime_wu4
Host:     127.0.0.1
Port:     5432
Server:   PostgreSQL 18.6
```

`production_runtime_local` was not used or destroyed by Work Unit 4.

The database was seeded with independent test identities:

```text
U_A_ONLY: user with membership only in Account A
U_AB:     user with memberships in Account A and Account B
U_NONE:   user without memberships
```

The seed also provides `secret-b` in Account B and `own-a` in Account A.

## Architecture

### PostgreSQL tenancy adapter

`api/adapters/postgres_tenancy.py` implements:

```text
PostgresTenantRepository
PostgresMembershipRepository
PostgresUserRepository
```

Account resolution joins `client_accounts` to `agency`, so Agency → ClientAccount ownership is server/database-derived. Membership lookup requires both trusted user identity and target account.

### Tenant resource adapter

`api/adapters/postgres_resources.py` implements:

```text
PostgresTenantResourceRepository.list()
PostgresTenantResourceRepository.read()
PostgresTenantResourceRepository.create()
PostgresTenantResourceRepository.update()
PostgresTenantResourceRepository.delete()
```

Every operation requires `TenantContext`. The mandatory SQL predicate is derived from:

```text
context.client_account.id
```

The adapter rejects missing or inconsistent context before SQL. Read/update/delete queries include both resource ID and server-derived `client_account_id`; a foreign resource therefore returns no resource and the HTTP boundary maps it to a non-enumerating 403. Create rejects a client-provided `client_account_id` that differs from the server-derived context. The request cannot select the tenant authority.

### Composition and HTTP wiring

`api/composition_wu4.py` creates an explicit WU4 composition with:

```text
PostgresRunner
PostgresTenantRepository
PostgresMembershipRepository
PostgresUserRepository
PostgresTenantResourceRepository
TenantContextResolver
```

`api/tenant_resources.py` exposes only minimal internal test routes. The target account is composition/session state, not a request body authority. No real authentication is implemented; tests inject the in-memory identity adapter through composition.

Because Windows' default Proactor event loop is incompatible with psycopg async mode, the adapter sets `WindowsSelectorEventLoopPolicy` on Windows. The local runner uses one connection per operation and closes it after use; this avoids sharing async connections/locks across independent test event loops. It is not presented as a production connection pool.

## Real PostgreSQL test results

Command:

```bash
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_wu4 RUNTIME_DB_USER=postgres python -m api.test_postgres_repositories
```

Result:

```text
exit code: 0
POSTGRES WU4 SCENARIOS: 19/19 passed
```

Detailed results:

```text
PASS db config loads fail-fast
PASS postgres tenancy repositories resolve real rows
PASS absent context rejected
PASS inconsistent context rejected
PASS A-context list returns only Account A rows :: status=200 keys=['demo-a', 'own-a']
PASS membership only in A cannot resolve B :: tenant_access_denied
PASS read B resource with A context rejected :: status=403, no leak in body=True
PASS update B resource with A context rejected :: status=403
PASS delete B resource with A context rejected :: status=403
PASS client_account_id spoof rejected before write :: status=403
PASS no forged row persisted in B :: b_rows=['demo-b', 'secret-b']
PASS authorized B context can read B resource :: status=200
PASS A+B member reads A and B with matching contexts :: A=True B=True
PASS account in other agency rejected :: status=403
PASS user without membership rejected :: status=403
PASS anonymous request returns 401 envelope :: status=401
PASS Account B unchanged after all attempts
PASS Account A final rows recorded :: keys=['demo-a', 'own-a']
PASS repo.update on owned row mutates exactly one row
```

The test verifies:

- User with only A reads A.
- Same user cannot resolve/read B.
- B read/update/delete with A context return 403.
- Missing context raises `CrossTenantAccessDenied` before SQL.
- Inconsistent membership/context is rejected.
- Forged `client_account_id` in create is rejected before write.
- User with A+B memberships can read each account with its matching composition context.
- User without membership and anonymous identity are rejected.
- B's `secret-b` value is never returned in A context.
- B's rows remain unchanged after all attempts.

Final B snapshot:

```text
[('demo-b', '{"value": "B"}'), ('secret-b', '{"value": "must-not-leak"}')]
```

Final A keys:

```text
['demo-a', 'own-a']
```

The test creates an attempted update on an A-owned row and restores its original value; no test mutation remains in the final fixture state.

## Existing verification commands

### TypeScript typecheck

```text
npm.cmd run typecheck
exit code: 0
```

### Existing TypeScript test suite

```text
npm.cmd test
exit code: 0
Test Files  5 passed (5)
Tests       27 passed (27)
Start at  22:54:04
Duration  2.30s
```

### Existing FastAPI scenarios

```text
RUNTIME_API_HOST=127.0.0.1 RUNTIME_API_PORT=8000 python -m api.test_runtime
exit code: 0
FASTAPI SCENARIOS: 13/13 passed
```

### Python adapter compile/import verification

```text
python -m compileall -q api/adapters api/composition_wu4.py api/tenant_resources.py api/test_postgres_repositories.py
COMPILE_EXIT=0
psycopg_version=3.2.9
```

The FastAPI test process emits a Starlette deprecation warning about `httpx`/`httpx2`; it does not affect results.

## Limitations

- No RLS is configured. Application predicates remain mandatory; RLS is future defense-in-depth.
- No production authentication is implemented. In-memory identities are test injection only.
- No production connection-pool implementation; current runner uses connection-per-operation for safe local async-loop behavior.
- No repositories for audit/outbox/idempotency records.
- No command transaction boundary from Work Unit 5.
- No durable idempotency/replay.
- No distributed or application-level isolation claim beyond the repository tests performed here.
- No Docker, Compose, CI/CD or deployment.
- No PostgreSQL service ownership/topology decision.
- No backup/restore, RPO/RTO or production readiness evidence.

## WACRM

WACRM was not used. No code was copied, executed or added as a dependency.

## Acceptance status

Work Unit 4 is **accepted for PostgreSQL repository/isolation slice** against real PostgreSQL 18.6 with 19/19 repository/isolation scenarios passing. This evidence does not claim production authentication, RLS, production pool behavior, command transactions, durable idempotency or production readiness.
