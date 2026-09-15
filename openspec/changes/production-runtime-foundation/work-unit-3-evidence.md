# Production Runtime Foundation — Work Unit 3 Evidence (rev 2)

## Status

Migration implementation completed. **Real PostgreSQL validation is now
complete via Work Unit 3.5** against PostgreSQL 18.6 (native local service):
up apply, integrity constraints (FK, composite FK, NOT NULL, canonical
membership uniqueness, agency-immutability trigger, per-account resource-key
uniqueness), restart persistence, down rollback and clean reapply all passed
with real server messages. See `work-unit-3.5-evidence.md` for exact commands
and outputs. **Work Unit 3 is accepted for PostgreSQL schema integrity only.**

## Rev 1 (superseded)

## Scope

Implemented only versioned PostgreSQL schema migrations for the durable base multi-tenant model:

```text
Agency → ClientAccount
User + identity_links
Membership
tenant_resource
```

No repositories, PostgreSQL adapters, RLS policy, application transactions, durable idempotency runtime, outbox runtime, Docker Compose, CI/CD, WACRM, WhatsApp, CRM, agents, automations, meetings or forms were added.

## Migration files created

```text
infra/migrations/001_initial_tenancy.up.sql
infra/migrations/001_initial_tenancy.down.sql
```

The migration is tool-neutral and uses explicit numbered SQL files. The `up` migration is wrapped in `BEGIN`/`COMMIT` and is intended to run once in version order by a future migration runner. Forward evolution should use a new numbered migration; existing migrations must not be edited after application. The `down` migration is an operator-reviewed destructive rollback only, not an automatic application rollback.

## Schema implemented

### `agency`

- Stable UUID primary key supplied by the application.
- Non-empty name.
- Lifecycle state: active, suspended or archived.
- Created/updated timestamps.

### `users`

- Stable UUID primary key.
- Lifecycle state: active, suspended or deactivated.
- Created/updated timestamps.

### `identity_links`

- Stable UUID primary key.
- Mandatory foreign key to `users` with `ON DELETE RESTRICT`.
- Provider and external subject are non-empty.
- Unique `(provider, external_subject)` prevents duplicate external identities.

### `client_accounts`

- Stable UUID primary key.
- Mandatory `agency_id` foreign key to `agency` with `ON DELETE RESTRICT`.
- Non-empty name.
- Lifecycle state: active, suspended or archived.
- Created/updated timestamps.
- Composite unique `(id, agency_id)` supports agency-consistent membership foreign keys.
- Trigger `client_accounts_agency_immutable` rejects changing `agency_id`, preventing account movement between agencies.
- Agency-leading index `(agency_id, id)`.

### `memberships`

- Mandatory agency, client account and user references.
- Composite foreign key `(client_account_id, agency_id)` to `client_accounts(id, agency_id)` explicitly prevents an inconsistent agency/account pair.
- User and Agency foreign keys use `ON DELETE RESTRICT`.
- Roles and permissions stored as PostgreSQL `text[]` values.
- Lifecycle state: active, suspended or revoked.
- Created/updated timestamps.
- Canonical uniqueness:

```text
UNIQUE (agency_id, user_id, client_account_id)
```

- Scope-leading indexes for agency/user/account lookups.

### `tenant_resource`

- Stable UUID primary key.
- Mandatory `client_account_id` foreign key to `client_accounts` with `ON DELETE RESTRICT`.
- Non-empty resource key.
- Minimal JSONB data payload.
- Created/updated timestamps.
- Unique `(client_account_id, resource_key)`.
- Tenant-leading indexes:

```text
(client_account_id, id)
(client_account_id, created_at, id)
```

## Audit/outbox/idempotency tables

The specifications require these records for later atomic command/audit/outbox work, but the task plan assigns them to later Work Units 5 and 6. They were intentionally not created here. No command logic or runtime was implemented.

## Constraints and guarantees at schema level

The SQL defines:

- No orphaned Agency, User, ClientAccount, Membership or tenant resource references.
- Mandatory tenant scope for `tenant_resource`.
- Mandatory Agency ownership for each ClientAccount.
- Composite membership ownership consistency.
- Canonical membership uniqueness.
- Database trigger preventing ClientAccount Agency reassignment.
- Tenant-leading indexes for representative scoped data.

These are schema constraints only. They do not replace application authorization or future RLS defense-in-depth.

## PostgreSQL availability check

Checked before verification:

```text
psql       not available
pg_isready not available
docker     not available
```

No PostgreSQL, Docker or other infrastructure was installed.

## Verification performed

### Static migration validation

A repository-local static check verified:

```text
begin_commit=True
tables=True
foreign_keys=True
scope_nonnull=True
canonical_unique=True
composite_fk=True
immutable_trigger=True
scope_indexes=True
down_order=True
STATIC-SQL-CHECK: PASS
exit code: 0
```

This is text/structure validation only. It is not a PostgreSQL parser or database execution.

### Existing TypeScript checks

```text
npm.cmd run typecheck
exit code: 0
```

```text
npm.cmd test
exit code: 0
Test Files  5 passed (5)
Tests       27 passed (27)
Start at  23:04:19
Duration  1.30s
```

These checks cover the existing TypeScript domain/runtime suite and do not execute SQL migrations.

## PostgreSQL verification blocked

The following real-database checks could not be executed because no PostgreSQL instance/tooling exists:

- Apply migrations to PostgreSQL.
- Restart/reapply migration behavior.
- Invalid foreign-key insertion.
- Duplicate membership insertion.
- Inconsistent Agency/ClientAccount relationship insertion/update.
- ClientAccount Agency reassignment trigger against a live database.
- Compatible rollback execution.
- Actual tenant isolation/read/write behavior.

No claim is made that these scenarios passed against PostgreSQL.

## Rollback boundary

`001_initial_tenancy.down.sql` drops objects in reverse dependency order, inside a transaction. It is destructive and operator-reviewed only. Application rollback must not automatically run it. Future migrations should use expand/contract compatibility and separate recovery procedures for destructive data changes.

No RPO/RTO, backup provider, migration runner or PostgreSQL hosting provider was selected.

## Decisions still open

- PostgreSQL topology and hosting.
- Migration runner/tool.
- ORM/query layer.
- RLS enablement as defense-in-depth.
- Application repository/transaction adapters.
- Durable audit/outbox/idempotency schema and atomic command boundary.
- Backup, restore and recovery procedures.
- Authentication identity-link adapter.
- Observability and deployment model.

## Limitations

- SQL has not been executed against a real PostgreSQL instance.
- Static validation cannot prove SQL execution, trigger behavior or constraint enforcement.
- No persistence adapter exists.
- No application authorization or RLS exists.
- No transactions beyond migration file boundaries exist.
- No durable idempotency exists.
- No audit/outbox runtime exists.
- No restart/recovery evidence exists.

## WACRM status

WACRM remains an audited reference only:

- No source copied.
- No source executed.
- No dependency added.
- No adapter created.
- No fork created.

## Files not modified

The following were intentionally not modified:

```text
PDR
proposal.md
design.md
specs
Work Units 1 and 2 artifacts
src/domain
src/application
```

## Acceptance status

Work Unit 3 migrations are **accepted for PostgreSQL schema integrity** after real
validation through Work Unit 3.5. This acceptance does not cover application-level
authorization, repositories, RLS, transactions, durable idempotency, or tenant
isolation in the application runtime.
