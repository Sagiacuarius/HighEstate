# Tasks: Production Runtime Foundation

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated authored lines | 1,500–2,200; 800-line budget risk: High |
| Delivery | 10 small PRs; auto-chain; feature-branch-chain |
| Open decisions | Auth/session, ORM/query layer, migration tool, RLS, VPS/topology, registry, secrets, observability, backup owner/retention/encryption, RPO/RTO |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

Each PR targets the previous boundary: PR1 → `feature/production-runtime-foundation`; PR2 → PR1; …; PR10 → PR9. No WACRM file is changed. Design threat matrix is entirely N/A, so no separate threat RED tasks apply.

### Suggested Work Units

| Unit | Objective; concrete tasks; dependencies; areas | Acceptance; evidence; rollback; estimate |
|---|---|---|
| 1 | Define versioned HTTP contracts, error/correlation/idempotency envelopes, minimal Next.js entrypoint and FastAPI app/lifecycle; preserve `src/domain` ports. Depends on baseline. Areas: `contracts/`, `web/`, `api/`. | Local start/build, invalid config, graceful shutdown, correlation/idempotency/error-envelope tests. PostgreSQL and Compose are explicitly deferred to Units 3/7. Remove entrypoints/contracts only. **120–180 lines.** |
| 2 | Add provider-neutral `IdentityAdapter`, server-derived session/context middleware, User/identity-link/membership resolution, non-enumerating denials; record ADR without selecting provider. Depends on 1. Areas: `api/app/auth/`, `api/app/application/tenant/`, `contracts/`. | Authorized A resolves; forged B/claims and missing membership fail closed; unit/contract plus redacted denial evidence. Revert auth adapters/middleware. **130–190.** |
| 3 | Create PostgreSQL versioned migrations for Agency, ClientAccount, User, identity link, Membership, and representative `tenant_resource`; FKs, lifecycle, scope indexes, canonical membership uniqueness. ORM/query/migration tool remains TBD. Depends on 2. Areas: `infra/migrations/`. | Restart persistence, invalid FK rollback, duplicate membership deterministic; migration up/down/compatibility evidence. Revert only additive migrations before dependent rollout. **160–240.** |
| 4 | Implement repository/transaction ports and PostgreSQL adapters with mandatory account predicates; wire resource CRUD and optional RLS only after ADR, never bypassing app checks. Depends on 3. Areas: `api/app/adapters/postgres/`, `api/app/domain/`, `src/application/ports/`. | Real-Postgres A-only list; forged B read/update/delete denied; missing scope cannot insert; isolation test report. Remove adapters, preserving schema/contracts. **180–280.** |
| 5 | Implement atomic command boundary: authorization, idempotency reservation/hash/result, mutation, and commit; handle concurrent duplicates/conflicts without external calls in transaction. Depends on 4. Areas: `api/app/application/commands/`, `api/app/adapters/postgres/`. | Retry replays one result; concurrent K yields one mutation; conflicting reuse mutates nothing; concurrency test evidence. Revert command handler/idempotency tables only with compatibility review. **150–230.** |
| 6 | Add durable audit and outbox records, redacted denial outcomes, scoped event payloads, post-commit idempotent consumer seam. Depends on 5. Areas: `api/app/audit/`, `api/app/outbox/`, migrations. | Success has one audit + pending event; commit failure has neither; denial is redacted; consumer replay safe. Remove consumer/audit wiring while retaining required history. **120–190.** |
| 7 | Add typed environment validation, non-root multi-stage images, Compose for Next.js/FastAPI/PostgreSQL, persistent volume, migration command, secret-free templates. Same images/config-only VPS shape; mechanism TBD. Depends on 1,3. Areas: `infra/compose/`, `web/Dockerfile`, `api/Dockerfile`, `.env.example`. | Clean bootstrap/restart works; missing production secret fails naming only variable; image digest equivalence check. Revert packaging/config, preserve application code. **150–220.** |
| 8 | Add separate liveness/readiness, dependency/migration checks, graceful drain, structured redacted logs with correlation and safe actor/scope classes. Observability provider remains TBD. Depends on 1,4,7. Areas: `api/app/health/`, `api/app/logging/`, `web/app/health/`. | Healthy ready; DB outage live-not-ready; token/raw payload absent from logs; health smoke evidence. Revert probes/log adapter only. **100–160.** |
| 9 | Document encrypted backup/restore drill, owner/retention/encryption placeholders, compatibility gates and application-versus-destructive schema rollback boundaries; do not invent RPO/RTO. Depends on 3,7,8. Areas: `runbooks/backup-restore.md`, `runbooks/rollback.md`, `infra/migrations/`. | Restore verifies A/B data without altering source; invalid migration blocks; prior image rollback is explicit; operator review receipt. Remove runbooks/gates, no data rollback assumed. **100–160.** |
| 10 | Build GitHub Actions lockfile/typecheck/tests/build/migration/real-Postgres isolation/health gates, immutable artifact and receipt, automatic health-gated deploy to TBD target/registry, timeout rollback. Depends on 1–9. Areas: `.github/workflows/`, `scripts/ci/`, runbooks. | Any failed gate prevents deploy; green revision deploys only after readiness; failed rollout restores prior artifact; CI receipts retained. Revert workflow/deploy scripts without runtime rollback. **180–260.** |

## Phase Checklist

- [ ] 1–2: runtime contracts and trusted context.
- [ ] 3–6: durable PostgreSQL tenancy, isolation, transactions, idempotency, audit/outbox.
- [ ] 7–9: Compose, secrets, health/logging, recovery and rollback evidence.
- [ ] 10: CI/CD gates and health-gated automatic deployment.

## Explicitly Out of Scope

WhatsApp, CRM breadth, agents, LangGraph, automations, meetings, calendar, forms, LLMs, WACRM fork/dependency/copy, Supabase assumption, and unapproved infrastructure providers.
