# Design: MVP Commercial Demo

## Technical Approach

Deliver a local FastAPI/Next.js vertical slice over `Agency → ClientAccount`, with explicit CRM tables and PostgreSQL as system of record. The simulator is provider-neutral; the HTML remains visual reference and `demo/agencia-inmobiliaria.css` remains the styling reference. No Kapso, WhatsApp, LLM, productive auth, Docker, VPS, CI/CD, or automation builder is introduced.

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Demo selector | UI sends only `requested_account_id` (intention). A server-owned demo identity is loaded by the identity adapter; the resolver checks membership and creates immutable `TenantContext`. | Client account claims cannot become authority. Missing identity, account, membership, malformed request, or failed permission rejects fail-closed; the previous valid context is not mutated and the response is non-enumerating. |
| CRM commands | New `CrmCommandPort`/operation abstraction adapts WU5/WU6 principles: canonical hash, idempotency reserve/replay/conflict, context-derived predicate, transaction, audit/outbox. | No direct reuse claim for `tenant_resource` commands. Reservation, redaction, transaction and audit adapters are adapted; CRM entities, commands, predicates and results are new. |
| Persistence | Versioned SQL is authoritative; a minimal reproducible local runner records applied versions. Explicit seed/reset commands operate only development data. | Avoids inventing an ORM and preserves `001–003`; seed/reset are deterministic, account-safe, transactional and idempotent. |
| Database baseline | PostgreSQL 18.6 is only the verified local baseline. Product version and topology remain open. | WU5/WU6 evidence proves this local baseline, not a global architecture decision. |

## Contracts, Invariants, and Flow

Selector: `requested_account_id` is never copied into authority fields. On success, response returns the resolved context; on denial, prior context remains active. Resolver, selector, and CRM command denials each execute the WU6 pattern: rollback the rejected operation, then append one separate redacted `outcome=denied` audit record in a new transaction, with correlation and safe reason class only; no success audit/outbox, raw payload, protected ID, or existence leak.

`inbound message → resolve context → CRM command port → reserve(actor,operation,key,canonical_hash) → scoped writes → audit + outbox → commit → response`. Replay returns the canonical result; hash conflict fails without mutation. This preserves message → lead → pipeline/stage → follow-up → activity.

The CRM tenant-scoped tables—contacts/leads, properties, pipeline/stages, conversations/messages, and follow-ups—carry `client_account_id`. `audit_records.client_account_id` remains nullable when an event is not tenant-scoped, as required by the WU6 baseline. Composite foreign keys (parent ID, account ID) and unique constraints enforce same-account relations: `lead(contact_id, client_account_id)`, `lead(property_id, client_account_id)`, `lead(stage_id, pipeline_id, client_account_id)`, `conversation(contact_id, client_account_id)`, `message(conversation_id, client_account_id)`, and `follow_up(lead_id, client_account_id)`. Repositories repeat `client_account_id = context.client_account.id`. Pipeline stages are ordered and lead transitions allow only declared stage/status transitions; completed/cancelled follow-ups are not rescheduled by replay.

### Seed and reset safety contract

Seed data uses stable keys in a reserved namespace, including `demo-account-a`, `demo-account-b`, and per-account entity keys such as `demo-a-contact-001` and `demo-b-contact-001`; generated IDs are never used as ownership markers. Each designated `ClientAccount` is marked as demo-owned by its stable seed key, and every seeded CRM row carries the corresponding stable demo ownership key. Seed and reset may delete/recreate only rows carrying those markers and belonging to the two designated demo accounts; they MUST NOT delete unmarked rows, rows in other accounts, foundation records, or migration history. The operation runs in one database transaction with row/advisory locking for the designated accounts, validates ownership before deletion, and commits all changes or rolls back all changes. Re-applying seed converges to the same records without duplicates; concurrent seed/reset requests serialize, then the later transaction revalidates ownership and produces the same deterministic state. A reset is therefore idempotent and account-safe without changing migrations `001–003`.

## Files and Composition

Add `infra/migrations/004_commercial_crm.up.sql`, a reviewed-only destructive down file, `infra/migrations/runner.py`, and explicit `validation/mvp_seed.sql`/`mvp_reset.sql`; never edit or remove `001–003`. Add CRM domain/ports/repository/command/query/HTTP/composition modules under `api/`, versioned CRM parsers in `contracts/runtime-v1.ts`, and dashboard files under `web/`. Existing tenancy, idempotency, audit/outbox, HTTP envelope, and runtime-client modules are seams, not tenant-resource commands to call directly.

## Testing Strategy

Existing runners remain the WU1–WU6 Python modules and existing TypeScript tests. This change adds a local CRM harness (runner and fixtures); `openspec/config.yaml: runner: none` means no configured project runner, not that tests are forbidden. Minimum tests: API contract tests for selector intent, denial/context preservation, payload validation, replay/conflict and correlation; PostgreSQL tests for migration compatibility, composite FKs/predicates, relations, seed/reset, rollback and concurrent idempotency; UI/E2E tests using an explicitly selected local browser/API harness for select A/B, simulate, reload, isolation and reset. Tools remain uncommitted/open until selected.

## Threat Matrix

| Boundary | Applicability and required behavior | RED boundary |
|---|---|---|
| Documentation-like paths | N/A: no executable documentation is added. | None |
| Git repository selection | N/A: no Git command boundary. | None |
| Commit state | N/A: no commit automation. | None |
| Push state | N/A: no push automation. | None |
| PR commands | N/A: no PR command composition. | None |
| HTTP routing/auth/payload/scope/enumeration | Applicable: new routes must use fixed route registration, server-owned identity, strict schemas, context predicates, non-enumerating errors, and correlation. | Unknown route/method, forged account, malformed payload, foreign scope, and denial-without-mutation tests. |

## Migration, Rollout, and Rollback

Apply `004` only after recorded `001–003`; expand/compatible migration precedes code that depends on new columns/constraints. Seed/reset is an explicit, deterministic, idempotent operation that upserts exactly two labeled demo accounts and removes/recreates only data identified as demo-owned within those designated `ClientAccounts`; it never drops foundation tables or migrations. Application rollback means reverting the feature slice while retaining compatible schema. Any `004` down/destructive data recovery is operator-reviewed, separately approved, and never an automatic application rollback.

## Delivery

Seven feature-branch-chain slices use auto-chain and preserve the 800-line review budget: contracts/domain; migration/runner/seed; repositories; command; read API; dashboard; E2E. Forecast remains 1,200–1,800 authored lines. `400-line budget risk: High`; the review budget is 800 changed lines. Any slice exceeding 800 lines is automatically subdivided into independent work units/PRs, each with its own verification and rollback boundary. The forecast records `Decision needed before apply: No`, `Chained PRs recommended: Yes`, and preserves `auto-chain` plus `feature-branch-chain`. WACRM is reference only.

## Open Questions

- [ ] Confirm demo labels and canonical simulator payload during task planning; provider, LLM, productive auth, and deployment remain out of scope.
