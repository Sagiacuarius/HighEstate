# Proposal: Production Runtime Foundation

## Intent and motivation
Turn the TypeScript domain proof into the smallest deployable product slice: a real authenticated Next.js web application and FastAPI API that persist and enforce `Agency → ClientAccount` tenancy. This removes process-local false confidence and establishes the operational baseline needed for safe iteration. WACRM remains audited reference material only; it is neither a dependency nor an assumed Supabase adoption.

## Scope

### In scope
- Next.js web + FastAPI API boundaries, local execution, typed HTTP contracts, and server-derived membership/context.
- Real authentication (provider/method intentionally deferred to design), persistent Agency, ClientAccount, users/memberships, and one tenant-scoped representative resource.
- PostgreSQL isolation proven against the real database; transactional commands, durable idempotency, audit records, and outbox events.
- Health/readiness, redacted structured logs, migrations, encrypted backup/restore procedure, Docker Compose for local/VPS, and GitHub Actions gates (typecheck, tests, builds, migrations, isolation) before automatic deploy.
- Immutable artifacts, health-gated rollout, explicit application/schema rollback boundaries, and independently reviewable work units.

### Explicit non-goals
WACRM fork/adaptation, WhatsApp, CRM breadth, agents, LangGraph, automations, meetings, calendar, forms, and LLMs. No external provider is activated.

## Architecture and approach
Next.js owns browser/UI and session-facing concerns; FastAPI owns authenticated application commands/queries and domain orchestration; neither trusts client-selected identity or tenant IDs. Clean Architecture, SOLID, and Hexagonal boundaries keep domain/application ports independent from HTTP, auth, PostgreSQL, queues, and providers. A transaction coordinates authorization, mutation, idempotency result, audit, and outbox commit; workers consume outbox records outside unbounded transactions. PostgreSQL predicates (optionally defense-in-depth RLS) are tested through actual adapters.

## Capabilities
### New
- `production-runtime`: executable services, configuration, packaging, CI/CD, health, logs, backups, and rollback.
- `persistent-tenant-isolation`: durable tenancy, memberships, context, resource isolation, transactions, and idempotency.
- `authentication-and-audit`: trusted identity mapping, audit, and committed events.
### Modified
- None; WACRM audit evidence remains unchanged.

## Work units and delivery
Independent slices: runtime/contracts; auth/context; PostgreSQL schema/migrations; tenant resource/isolation; transactional idempotency/audit/outbox; operations/Compose/backups; CI/deploy/rollback. Each includes tests, evidence, and rollback. `Decision needed before apply: No`; `Chained PRs recommended: Yes`; `400-line budget risk: High` (forecast 1,200–1,800 authored lines against 800). Use auto-chain with a feature-branch chain: each PR targets the preceding slice and is independently reviewable.

## Risks and open decisions
Two runtimes, destructive migrations, secret leakage, worker duplication, and isolation regressions are high risks; mitigate with compatibility-first migrations, secret validation/redaction, atomic outbox/idempotency, real-DB tests, least privilege, and restore drills. Open: auth provider/method; session schema; ORM/query layer; PostgreSQL topology; VPS; registry; observability; exact backup owner, retention, encryption, and RPO/RTO.

## Acceptance criteria
- [ ] Compose starts Next.js, FastAPI, and PostgreSQL; authenticated A→B switching works.
- [ ] Forged IDs, unauthorized membership, and cross-account reads/writes fail closed in real PostgreSQL.
- [ ] Concurrent duplicate commands replay one durable result; conflicting key reuse fails; audit/outbox commit atomically.
- [ ] CI blocks deploy unless typecheck, tests, builds, migration validation, and isolation gates pass; deploy is health-gated and rollback is documented.

## Proposal question round
Assumptions for review: one admin journey, one resource, one deploy target, and provider-neutral auth are sufficient. Please confirm or correct the required admin lifecycle, recovery/MFA expectations, and backup RPO/RTO before design.

## Rollback
Stop deployment, route traffic to the prior immutable image, and restore the prior compatible schema only through tested forward/backward migration procedures. Destructive data changes require a separate recovery decision; remove only this slice’s adapters/configuration, preserving audit evidence.
