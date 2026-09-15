# Design: Production Runtime Foundation

## Technical Approach

Build two independently deployable, contract-first services: for Work Unit 1, Next.js owns the minimal web entrypoint and FastAPI owns the minimal local HTTP/lifecycle boundary. Authentication, PostgreSQL, persistence and production operations are later work-unit concerns. Product-owned Clean/Hexagonal ports preserve the TypeScript tenant seam while future adapters become the runtime boundary. WACRM remains audit-only. Non-goals for Work Unit 1 remain WACRM fork/adaptation, WhatsApp, CRM breadth, agents, LangGraph, automations, meetings, calendar, forms, LLMs, authentication, PostgreSQL and activating any external provider.

## Context Map and Boundaries

```text
Browser -> Next.js -> FastAPI -> domain/application ports -> PostgreSQL
             |           |              |                  |
        session UI   auth adapter   repositories       predicates/RLS
```

Next.js never decides identity, agency, account, or permissions; it forwards a server-managed session/token and correlation ID. FastAPI derives `User`, `Agency`, `ClientAccount`, `Membership`, and immutable `TenantContext`; PostgreSQL receives authorized transaction context only. No direct browser-to-Postgres path exists.

## Architecture Decisions

| Decision | Options/tradeoff | Choice and rationale |
|---|---|---|
| Service split | Modular monolith is smaller; two runtimes cost more | Confirmed Next.js + FastAPI; explicit boundary enables later workers/LangGraph without adding them now. |
| Auth | Managed provider, self-hosted sessions, bearer tokens; provider and recovery policy remain unknown | Define `IdentityAdapter`/session port now; select provider, method, MFA, CSRF and revocation in ADR before implementation. Recommendation: server-managed session/cookie or equivalent trusted adapter, never client tenant claims. |
| Persistence | ORM/query layer remains open | Define repository/transaction ports; choose implementation after spike against real PostgreSQL. |
| Isolation | App predicates alone; predicates plus RLS | Mandatory application authorization plus PostgreSQL scoped predicates; optional RLS is defense-in-depth and never replaces checks/service-role ownership validation. |

## Layers, Contracts, and Data Model

Both services use `inbound adapters -> application use cases -> domain -> outbound ports`; infrastructure points inward. Shared contracts are versioned OpenAPI/JSON schemas plus generated clients, with error envelope, correlation ID, idempotency key, pagination, and non-enumerating denials. Candidate paths: `src/domain/tenant`, `src/application/{tenant,audit}`, `api/app/{domain,application,adapters}`, `web/app`, `contracts/`.

Persist `agency`; `user`; `identity_link(provider-neutral subject, user)`; `client_account(agency_id)`; `membership(user, agency, account, lifecycle, roles/permissions)`; representative `tenant_resource(client_account_id)`; `audit(actor/context/action/outcome/correlation/time, redacted metadata)`; `outbox(event, scope, status, attempts)`; and `idempotency(actor, operation, key, payload_hash, status, canonical_result)`. Foreign keys, mandatory scope, and scope-leading indexes apply. Membership identity is the logical key `(user_id, client_account_id)` within its agency (implemented with an agency-consistent unique constraint, e.g. `(agency_id, user_id, client_account_id)`). The membership transaction atomically reserves that key; an existing duplicate returns the same canonical membership/result deterministically. This is distinct from command idempotency, whose key is `(actor, operation, idempotency_key)` and whose payload comparison governs command retries. Role matrix and invitation/recovery policy stay open.

## Critical Flows

Authentication resolves a trusted subject, identity link, user, and explicit membership; requested account is only an intention. A command transaction authorizes, atomically reserves `(actor, operation, key)`, compares payload hash, mutates state, stores result, audit, and outbox event, then commits. Concurrent duplicates wait/replay; conflicting reuse fails. External calls occur after commit through bounded future consumers with provider deduplication—not inside transactions. Authentication denials produce only a redacted operational log when operationally relevant; authorization denials for relevant requests produce a redacted audit record and, when needed, a matching redacted operational log. Both use safe outcome/reason classes, correlation ID, and no raw identity, tenant, resource, claims, or payload; the client receives one non-enumerating denial shape regardless of whether membership or the target exists.

Local execution for Work Unit 1 runs only Next.js and FastAPI with validated local variables, liveness/readiness lifecycle behavior, correlation and error contracts. PostgreSQL and Docker Compose are specified for later work units (`persistent-tenancy`, `postgres-tenant-isolation`, `compose-environment`) and are not introduced here. VPS runs and production approval remain future concerns.

## Operations and Delivery

Configuration is typed, environment-specific, fail-fast, and secret-free in Git; production secrets use an unresolved VPS mechanism. Logs are structured/redacted (timestamp, level, service, environment, correlation ID, safe actor/scope class, outcome, duration). Readiness checks PostgreSQL/migration compatibility; liveness checks process only.

Migrations are expand/contract, versioned and CI-validated before rollout. Application rollback targets the prior image; destructive schema/data recovery is operator-led. The Operations/Maintainer role is the operational owner for encrypted backups, retention, restore drills, and RPO/RTO evidence; the concrete person, provider, retention values, and RPO/RTO targets remain open. GitHub Actions installs lockfiles, typechecks, tests, builds and signs/records immutable artifacts, validates migrations, runs real-PostgreSQL isolation and health checks, then auto-deploys only on green gates; readiness timeout restores the prior compatible artifact.

## Work Units and Forecast

Delivery strategy: **auto-chain** with a **feature-branch chain** and an **800-line authored-change budget**. Work units are (1) contracts/runtime entrypoints, (2) auth/context, (3) schema/migrations, (3.5) local database validation, (4) resource isolation, (5) transaction/idempotency, (6) audit/outbox, (7) Compose/environment, (8) health/logging, (9) backup/recovery, and (10) Actions/deploy/rollback. Each includes focused evidence and rollback. Forecast: **1,200–1,800 authored lines; 800-line budget risk: High; Chained PRs recommended: Yes; Decision needed before apply: No.** Any work unit or resulting slice above 800 authored lines is automatically chained into additional independently reviewable feature-branch slices; the happy path does not ask for another approval. The strategy does not alter the 400-line default review guard unless the governing configuration says otherwise.

## File Changes

Create `web/`, `api/`, `contracts/`, `infra/compose/`, `infra/migrations/`, `.github/workflows/`, and runbooks; adapt `src/domain/tenant` and `src/application/ports`; do not modify WACRM artifacts.

## Testing Strategy

Unit: domain, policy, redaction, hashing. Integration: real PostgreSQL constraints/predicates/RLS, concurrent idempotency, migration compatibility, outbox atomicity, backup restore. Contract/E2E: Next.js↔FastAPI auth/context, A/B denial, readiness, same-image Compose smoke. No runner is configured; CI must establish evidence.

## Threat Matrix

All rows are **N/A**: this design introduces no user-controlled routing, shell-command composition, subprocess API, executable-file classification, Git ref/commit/PR command boundary, or implementation of VCS automation. CI/Compose are deployment artifacts; their security tests belong to implementation gates, not this matrix.

## Open Questions / ADRs

- [ ] ADR auth provider/method, session schema, MFA/CSRF/revocation, role matrix, invitation/recovery.
- [ ] ADR ORM/query layer, RLS enablement, PostgreSQL topology, migration tool.
- [ ] ADR VPS/OS/topology, registry, secret mechanism, TLS/domain, backup provider/retention/encryption, RPO/RTO.
- [ ] ADR observability provider and deployment controller; define minimum provider-neutral signals first.

## Migration / Rollout

Bootstrap with compatibility-first migrations; deploy image and migrations as separate gated steps; seed only explicit development data. Roll back application to the prior digest; destructive data changes require operator recovery.
