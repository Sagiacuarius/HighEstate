# Production Runtime Foundation — Work Unit 3.5 Evidence (rev 2, real PostgreSQL)

## Status

Previous revision was blocked by environment. This revision records the **completed real validation** against a locally installed PostgreSQL 18.6 instance. Implementation and evidence are finished. **Work Unit 3.5 is accepted for local PostgreSQL validation; Work Unit 3 is accepted only for schema integrity, not application isolation or production readiness.**

## Environment

```text
Host:      127.0.0.1
Port:      5432
Database:  production_runtime_local (disposable local validation database)
User:      postgres
Server:    PostgreSQL 18.6 on x86_64-windows, compiled by msvc-19.44.35228, 64-bit
Service:   postgresql-x64-18 (Windows service, native install)
Tools:     C:\Program Files\PostgreSQL\18\bin\psql.exe / pg_isready.exe
```

Authentication: native password (scram-sha-256) supplied locally through
`%APPDATA%\postgresql\pgpass.conf`. No password is recorded here or was ever
transmitted through chat. `pg_isready` before connect:

```text
127.0.0.1:5432 - aceptando conexiones  (exit 0)
```

## Difference vs. the runbook

`infra/postgres-local-runbook.md` anticipated Docker with `postgres:16.4`.
Docker remained unavailable; validation instead used the user's **native
PostgreSQL 18.6** service, as the runbook's native-instance path allows. No
global PostgreSQL configuration was modified by this unit.

## Files created for validation (no schema change)

```text
infra/migrations/validation/wu35_apply_and_seed.sql
infra/migrations/validation/wu35_seed_only.sql
infra/migrations/validation/wu35_integrity_scenarios.sql
```

The Work Unit 3 migration SQL files were not modified: real execution exposed no
SQL defect.

## 1. Initial state / migration already applied

The six expected tables were present from a prior real apply (captured live):

```text
public | agency | client_accounts | identity_links | memberships | tenant_resource | users
```

Confirmed demo data:

```text
Agencies:      00000000-…-01 Agency A, 00000000-…-02 Agency B
Accounts:      20000000-…-01 Account A (agency …-01)
               20000000-…-02 Account B (agency …-01)
Memberships:   30000000-…-01 → Agency A/Account A/User 10000000-…-01
               30000000-…-02 → Agency A/Account B/User 10000000-…-01
Resources:     40000000-…-01 demo-a (Account A), 40000000-…-02 demo-b (Account B)
```

## 2–7. Integrity scenarios (`wu35_integrity_scenarios.sql`)

Every attempt was wrapped in its own explicit `BEGIN … ROLLBACK` transaction.
Real PostgreSQL messages (server localized to Spanish):

| Scenario | Real PostgreSQL result |
|---|---|
| S2 `tenant_resource` with non-existent `client_account_id` | `ERROR: … viola la llave foránea «tenant_resource_client_account_id_fkey»` — DETALLE: key `deadbeef-…` not present in `client_accounts` ✅ rejected |
| S3 duplicate canonical membership `(agency_id, user_id, client_account_id)` | `ERROR: llave duplicada viola restricción de unicidad «memberships_canonical_unique»` ✅ rejected |
| S4 membership with Agency B id on Account A | `ERROR: … viola la llave foránea «memberships_agency_account_fk»` (composite `(client_account_id, agency_id)`) ✅ rejected |
| S5 `UPDATE client_accounts SET agency_id=…` | `ERROR: client account agency ownership is immutable` (PL/pgSQL `prevent_client_account_agency_change()`, line 4, RAISE) ✅ rejected by trigger |
| S6 `tenant_resource` with NULL `client_account_id` | `ERROR: … viola la restricción "not-null"` ✅ rejected |
| S7a duplicate `resource_key` `demo-a` within Account A | `ERROR: llave duplicada viola restricción de unicidad «tenant_resource_account_key_unique»` ✅ rejected |
| S7b same `resource_key` in Account B | `INSERT 0 1`; count `demo-a` = 2 inside transaction ✅ allowed (per-account uniqueness) |

Contamination check after all scenarios (committed state):

```text
contaminated_rows=0
memberships_account_a=1
account_a_agency=00000000-0000-0000-0000-000000000001
demo_resources=2
```

No scenario left persisted data; Account A kept its original Agency.

## 8. Restart persistence

First `Restart-Service` attempt from a non-elevated shell failed to stop the
service (expected permission error). The user restarted `postgresql-x64-18`
elevated. After restart:

```text
pg_isready:        127.0.0.1:5432 - aceptando conexiones (exit 0)
agencies=2
accounts=2
memberships=2
resources=demo-a,demo-b
```

Data A/B survived the service restart on the local data directory. ✅

## 9. Down migration

Executed with `ON_ERROR_STOP=1`:

```text
psql -v ON_ERROR_STOP=1 -f infra/migrations/001_initial_tenancy.down.sql
```

Output order:

```text
BEGIN
DROP TABLE (tenant_resource)
DROP TABLE (memberships)
DROP TRIGGER client_accounts_agency_immutable
DROP FUNCTION prevent_client_account_agency_change()
DROP TABLE (client_accounts)
DROP TABLE (identity_links)
DROP TABLE (users)
DROP TABLE (agency)
COMMIT
DOWN_EXIT=0
```

Post-rollback object audit:

```text
tables=0
functions_with_name=0
triggers=0
```

Drop order followed reverse dependency order and completed inside one
transaction. **The down migration is destructive**; it is acceptable here
because `production_runtime_local` is a disposable validation database, and it
must never be treated as an automatic application rollback.

## 10. Reapply (clean bootstrap) and seed repeatability

On the empty database:

```text
psql -f wu35_apply_and_seed.sql → all CREATE TABLE/INDEX/FUNCTION/TRIGGER
statements succeeded, COMMIT reached (schema reapply after down works). ✅
```

One transient error came from the **validation seed file**, not the schema:
placeholder UUIDs ending in letters (`…0000u1`) were invalid for `uuid` type.
That was a data fixture I authored during this unit — corrected by replacing
with valid canonical UUIDs; migration SQL itself untouched. ✅

Duplicate-apply behaviour (idempotency check):

```text
second apply of 001_initial_tenancy.up.sql:
ERROR:  la relación «agency» ya existe
```

This is the expected fail-fast for a versioned migration applied twice. No
migration ledger/runner exists yet; enforcing "apply once" is the future
migration-runner responsibility (open decision). After restoring demo data:

```text
seed exit 0; tables=6; agencies=2; accounts=3; memberships=2; resources demo-a, demo-b
```

## Commands used (representative)

```bash
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_local -tAc "select version()"
psql -v ON_ERROR_STOP=1 -f infra/migrations/validation/wu35_apply_and_seed.sql
psql -f infra/migrations/validation/wu35_integrity_scenarios.sql
psql -v ON_ERROR_STOP=1 -f infra/migrations/001_initial_tenancy.down.sql
psql -f infra/migrations/validation/wu35_seed_only.sql
```

All exits 0 except the intentional constraint errors inside rolled-back
transactions and the expected duplicate-apply error.

## What was proven / not proven

Proven against real PostgreSQL 18.6:

- Migration up applies cleanly; down rolls back cleanly; up reapplies after down.
- FK, composite FK, NOT NULL, canonical membership uniqueness, per-account
  resource-key uniqueness and the Agency-immutability trigger all enforce.
- Failed attempts leave no persisted data (transaction isolation of tests).
- Data persists across service restart.

**Not proven (explicitly):**

- Application-level tenant isolation — repositories/adapters are Work Unit 4.
- RLS — remains a future defense-in-depth decision.
- Backup/restore drill, RPO/RTO, migration runner, production topology — later units.
- Production readiness — not claimed.

## WACRM

Reference only; nothing copied, executed or installed from it.
