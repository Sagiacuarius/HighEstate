# Exploration: Production Runtime Foundation

## Current State

The repository is now a small TypeScript domain/application proof, not a deployable application. `package.json` contains only TypeScript and Vitest scripts; `tsconfig.json` compiles `src/**/*.ts`; there is no HTTP server, frontend runtime, database client, migration system, authentication adapter, worker, container definition, environment schema, CI workflow, health endpoint, logging policy, backup procedure, or deployment manifest.

The implemented seam is valuable but explicitly non-production. `Agency`, `ClientAccount`, `User`, `Membership`, immutable `TenantContext`, trusted-identity ports, authorization use cases, and tenant-scoped CRUD contracts exist under `src/`. The in-memory adapters prove authorized switching and denied cross-account operations, but they are process-local and provide no database transaction, concurrency guarantee, durable idempotency, real authentication, or runtime integration. A notable gap is that `SwitchClientAccount` reads idempotency results but does not persist a successful result through the port; the in-memory test inserts replay state manually. Durable replay recording must therefore be designed as part of the production command boundary, not assumed from the current seam.

The product documents confirm a shared multi-tenant runtime, `Agency → ClientAccount` isolation, provider boundaries, validated tools, deterministic automation, confirmation, execution, and audit. They describe Next.js/React/TypeScript, FastAPI/Python, LangGraph, Kapso, and OpenAI/OpenRouter as directions, not as a fully selected production stack. WACRM is an audited reference only: no fork, dependency, or runtime adoption is approved.

## Affected Areas

- `src/domain/tenant/` — preserve the product-owned tenancy invariants while adding persistence-safe mappings and transaction-independent domain contracts.
- `src/application/tenant/use-cases.ts` — expose HTTP-safe application commands and close the durable idempotency write/transaction gap.
- `src/application/ports/tenant-ports.ts` — extend ports for transactional repositories, identity/session, durable idempotency, audit, and committed events without leaking a vendor.
- `src/adapters/in-memory/` — retain as reference tests; do not treat it as production behavior.
- `package.json`, `tsconfig.json` — the current TypeScript-only toolchain must gain an explicit runtime/build boundary or be replaced/partitioned by the selected stack.
- `openspec/changes/wacrm-adaptation-foundation/` — accepted evidence is the baseline and must remain unchanged; its limitations define this change's acceptance boundary.
- New application entrypoint and inbound adapters — required for local HTTP execution, request correlation, authenticated identity, server-derived tenant context, and error mapping.
- New persistence/migration adapter — required for `Agency`, `ClientAccount`, memberships, users/identity links, tenant-scoped resources, audit, events, and idempotency.
- New deployment/operations files — required for local reproducibility, VPS deployment, secrets, health checks, logs, backups, rollback, and CI.

## Confirmed Constraints vs Pending Decisions

| Area | Confirmed from repository/evidence | Still pending; must not be invented as a decision |
|---|---|---|
| Deployment path | Local first, then GitHub, then VPS; runtime must be portable | VPS vendor/OS/topology, domain/TLS and operational owner |
| Tenancy | `Agency → ClientAccount`; explicit memberships; server-derived scope; fail-closed isolation | Final role matrix, agency-wide actions, invitation/recovery policy |
| Architecture | Product-owned domain/application ports; replaceable adapters; shared runtime | Exact service split and whether Python is introduced in the first slice |
| Web/API | A real API and local entrypoint are required | Next.js full-stack, Next.js plus FastAPI, or another HTTP composition |
| Persistence | Durable storage, migrations, transactions, and tenant predicates are required | PostgreSQL distribution/host, ORM/query library, RLS versus application predicates |
| Identity | Real authentication and server-trusted identity are required | Managed auth versus self-hosted auth/session implementation; email/OAuth/MFA scope |
| Integrations | Providers must be behind internal contracts | Kapso activation timing, credentials, webhook contract and first real provider |
| Operations | Docker or equivalent reproducible local/VPS packaging; CI; backups; observability | Container platform, registry, monitoring vendor, backup destination and retention |
| WACRM | Audited reference only; no dependency/fork automatic | Any future source reuse remains separately gated |

## Runtime and Stack Alternatives

1. **TypeScript modular monolith first** — HTTP API, domain, persistence, and a minimal UI/entrypoint in one deployable Node process; keep provider and worker ports internal.
   - Pros: smallest migration from the existing TypeScript seam; one language, one local command, simple Docker/VPS shape; preserves the current contracts without a premature service boundary.
   - Cons: does not immediately realize the documented FastAPI/LangGraph direction; agent execution may later require a separate process.
   - Effort: Medium.

2. **Next.js web/API plus FastAPI application services** — browser/UI in Next.js and tenant/application API in Python, with LangGraph in the same Python boundary or a worker.
   - Pros: aligns most closely with the documented target; good separation for future agent/runtime work.
   - Cons: two languages, two build/test toolchains, duplicated configuration and local/VPS operations; larger first production slice and more cross-service auth/transaction complexity.
   - Effort: High.

3. **Adopt a WACRM/Supabase-shaped runtime immediately** — use the audited reference's Next.js/Supabase patterns as the implementation base.
   - Pros: potentially faster UI and database scaffolding if independently approved.
   - Cons: conflicts with the current adoption decision, does not prove the Agency layer, service-role paths require additional ownership checks, and imports provider/deployment assumptions before product contracts are stable.
   - Effort: High and currently blocked.

### Recommendation

Use a **portable modular monolith for the first production slice**, with the current TypeScript domain/application seam retained as the core and an HTTP/persistence/identity adapter selected in the proposal/design phase. Keep the boundary capable of a later FastAPI/LangGraph worker, but do not pay the two-runtime cost before a real agent or asynchronous workload requires it. This recommendation is an implementation shape, not a vendor decision: database, auth, VPS, CI provider, and cloud services remain open until explicitly selected.

## Runtime Foundation Findings

### Application/API and local entrypoint

The first executable boundary needs a server entrypoint, request ID/correlation middleware, authenticated request context, tenant resolution before handlers, typed command/query handlers, non-enumerating authorization errors, graceful shutdown, and a documented local start command. The API must never accept `userId`, `agencyId`, permissions, or tenant scope as authoritative request data. A local UI can be deferred; an API plus a minimal authenticated operator journey is sufficient evidence for this change.

### Authentication

Implement a real identity adapter that returns a trusted subject and maps it to product-owned `User` and memberships. Session/cookie versus bearer-token behavior, password reset, invitation, MFA, OAuth, session revocation, rate limiting, and CSRF policy are pending decisions. The adapter must not let a client-selected account or claim replace server-side membership lookup. Secrets and refresh/session material must not be logged or stored in source control.

### Database, migrations, and tenant persistence

The minimal durable model should cover `Agency`, `User`/identity link, `ClientAccount`, `Membership`, and a representative tenant-scoped resource, plus audit entries, domain/outbox events, and idempotency records. Every tenant-scoped table needs a mandatory `client_account_id`, foreign-key relationship to the account, account-aware uniqueness, indexes that begin with scope where appropriate, and tests for forged IDs and cross-account access. Migrations must be versioned, repeatable in local and VPS environments, reviewed in CI, and applied as an explicit deploy step.

RLS can be a defense-in-depth layer if the selected database supports it, but it cannot replace application authorization, especially for worker/service-role paths. The production adapter must prove isolation against the actual database, not only through the in-memory repository.

### Transactions and durable idempotency

A switch or write command must make authorization decision, domain mutation, idempotency reservation/result, audit record, and committed event/outbox record coherent. The exact transaction boundary depends on the selected persistence technology, but duplicate `(actor, operation, idempotency_key)` requests must return the same recorded result, while reuse for a different target must fail. Concurrent requests must be covered by unique constraints/locking or an equivalent atomic reservation. External provider calls must not occur inside an unbounded database transaction; use an outbox/job handoff and provider-level deduplication.

### Real adapters and environment configuration

Implement adapters in layers: HTTP/identity, durable repositories, audit/outbox, then a single provider contract with a fake/test adapter. Configuration must be parsed and validated at startup, separated by local/test/production environments, and contain no provider-specific values in domain code. Kapso and LLM credentials are not required for this foundation slice; provider interfaces and disabled configuration states are enough. WACRM remains absent.

### Packaging and VPS portability

Prefer a multi-stage Docker build and Compose-equivalent local orchestration for the app and selected database, with a non-root runtime, pinned lockfile/base image policy, health/readiness endpoints, graceful shutdown, migration command, and persistent database volume. The same image should run locally and on the VPS; only environment configuration and external endpoints should vary. If Docker is rejected, the equivalent must still define reproducible runtime versions, process supervision, migrations, persistence, health, and rollback explicitly.

### Secrets, logs, health, backups, and observability

Use environment variables or a VPS secret mechanism for database URLs, auth keys, signing keys, encryption keys, provider tokens, and cookie configuration. Fail fast on missing production secrets and distinguish public configuration from secrets. Structured logs need timestamp, level, service, environment, request/correlation ID, actor class, agency/account scope where safe, outcome, and duration; never credentials, tokens, raw message content, or sensitive payloads.

Health must distinguish process liveness from dependency readiness and must not expose secrets or tenant data. Backups require an explicit owner, encryption, retention, restore test, and point-in-time expectations before pilot use. Baseline observability should include request errors/latency, auth denials, database/migration failures, queue/outbox failures, provider failures, and backup/restore status; traces and agent evaluations are later unless needed by the selected runtime.

### GitHub CI, deploy, and rollback

The first CI should install from the lockfile, typecheck, run unit/contract tests, build the selected runtime image, validate migrations, and run a database-backed isolation test. Later stages may add lint, dependency/security scanning, image scanning, and deployment checks. GitHub is the repository destination, not yet a selected CI/deployment provider. Production deploys need immutable versioned images/artifacts, migration compatibility rules, health-gated rollout, logs/receipt capture, and a documented rollback that distinguishes application rollback from irreversible schema/data migrations. No automatic production deploy should be assumed.

## Recommended Smallest Productive Slice

Build one deployable, authenticated API journey:

1. Start locally from a reproducible package.
2. Authenticate one administrator through the selected identity adapter.
3. Persist one Agency, two ClientAccounts, the administrator's explicit memberships, and one generic tenant-scoped resource.
4. Resolve a server-owned `TenantContext` and switch A → B.
5. Prove forged scope, unauthorized switch, and cross-account read/write denial against the real database.
6. Persist audit outcomes and durable idempotency atomically; replay succeeds and conflicting reuse fails.
7. Expose liveness/readiness, structured redacted logs, migration execution, backup/restore instructions, and CI checks.

Dependencies: select the runtime composition, database/migration approach, identity/session approach, transaction/idempotency model, packaging strategy, and minimum operational policy. Real WhatsApp, Kapso calls, CRM breadth, agent execution, LangGraph, automation builder/engine, meetings, calendar, forms, and LLM providers are not dependencies of this slice. A provider port and disabled/fake adapter may be specified, but no external integration needs to be activated.

## Explicit Limits and Non-Goals

- **WhatsApp/CRM:** no real provider connection, inbox, leads, properties, templates, or full CRM behavior.
- **Agents/automation:** no conversational or operator agent, LangGraph runtime, tool execution, visual builder, deterministic engine, scheduling, or retries beyond the durable command/idempotency foundation.
- **Meetings/forms:** no meeting provider, recordings, transcription, calendar, custom fields, or form ingestion.
- **WACRM:** no source copy, fork, dependency, runtime, or migration adoption.
- **Commercial production:** no billing, white-label, self-hosted product mode, multi-region scaling, or enterprise compliance claim.

## Risks

- Choosing two runtimes too early can multiply operational and security surfaces before product behavior exists.
- A database predicate implemented only in application code can regress through workers, service accounts, joins, caches, exports, or object paths.
- Idempotency without atomic reservation and result persistence will duplicate writes under retries or concurrency.
- Real authentication may expose gaps in identity-to-membership mapping, session revocation, CSRF, or account enumeration.
- Migration rollback may be impossible for destructive changes; deploy compatibility must precede automated rollback.
- Backup existence without restore evidence creates false recovery confidence.
- The 800-line review budget makes a full runtime, UI, auth, CRM, and provider delivery unsuitable for one slice; auto-chain should split implementation into independently deployable work units.
- WACRM's audited Supabase/Next.js evidence is useful reference material but must not silently become a selected provider or architecture decision.

## Ready for Proposal

**Yes, with explicit decision gates.** The next proposal should scope the smallest authenticated, database-backed, locally runnable tenant runtime and its production-operations baseline. It should present the modular-monolith recommendation alongside the documented two-runtime alternative, record database/auth/VPS/CI as pending choices, require real-database isolation and durable idempotency evidence, and keep all product breadth and WACRM intake outside the change.
