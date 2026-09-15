# Proposal: Audit Screen

## Intent

Expose the audit trail that WU5 commands and WU6 audit/outbox already persist in `audit_records` (migration `003`) as a first-class operator view: a tenant-scoped, filtered read API plus an "Auditoría" view in the demo dashboard. Nothing is newly captured — this change is read surface and UI only. It supports weekly internal review during the pilot and a sales-quality signal (show a prospect the full history of a conversation).

## Scope

### In Scope
- Versioned read API `GET /v1/demo/audit` on the accepted WU8 composition app (WU6 read discipline): mandatory server-derived `client_account_id` predicate over `audit_records` with **zero schema change** (no new migration, no indexes added in this unit — the existing `(client_account_id, created_at, id)` index from `003` covers the primary access pattern).
- Filters: `contact_id` (mapped via existing durable relations, defined in design), `property_id` (resource match + interest join + lead selected-property, per design), `user_id` (`actor_id`), `from`/`to` (on `created_at`), `action` (exact `action` string), `limit` + opaque keyset cursor (`(created_at, id)` — the log grows; offset pagination is not offered).
- Response shape reuses the accepted activity projection rules: `action`, `outcome`, `resource_type`, `resource_id`, `actor_id`, `correlation_id`, `idempotency_key`, non-success `reason` class only, `created_at`. `metadata` is never serialized beyond the already-exposed `reason` key; the `audit_records_metadata_not_sensitive` CHECK plus the existing projection discipline carry the redaction guarantee.
- Dashboard "Auditoría" view (new nav item beside "Actividad", which stays as-is): table of action, outcome, resource, actor, date, correlation; filter controls for the six filter dimensions; statuses translated with the existing `statusLabel`; empty state and error states using the versioned envelope with consistent correlation — WU8 UI pattern.
- TS contract mirror in `contracts/runtime-v1.ts` for the audit response (typed parser, same rules as the WU6 mirrors), and typed client method in `web/app/demo-api.ts`.

### Out of Scope / No-goals
- No new capture paths, no writes to `audit_records` beyond what accepted commands already do.
- No roles/permissions (operator view for now; Block 5 layers a permission on top without redoing the view).
- No CSV export, no aggregate reports, no charts — filtered listing only.
- No Kapso/WhatsApp, LLM/LangGraph, RLS, productive auth, Docker, CI/CD.
- No new migration. `audit_records`, `leads`, `property_interests`, `conversations`, `messages` schemas are consumed as-is.

## Capabilities

### New Capabilities
- `audit-screen`: filtered, tenant-scoped, read-only exposure of the durable audit trail with an operator dashboard view.

### Modified Capabilities
- None at the requirement level: the accepted WU6 activity feed keeps its shape and route; the audit screen is additive alongside it. (Baseline reference: `mvp-commercial-demo` + `property-catalog`.)

## Approach

Add a WU12 read service + SQL adapter query module (never editing accepted WU6 read internals), an `audit_screen` APIRouter mounted onto the accepted WU8 app (same seam WU9 used for the simulator), contract parser, and the "Auditoría" nav view. Filter-to-table mapping is pinned in design so no invented columns leak into SQL.

## Acceptance

- [ ] `GET /v1/demo/audit` returns only rows with `client_account_id` = server-resolved account; NULL-account cross-context rows invisible; no route parameter or query carries an account id, and a forged `clientAccountId` provably cannot change scope.
- [ ] Each filter (`contact_id`, `property_id`, `user_id`, `from`/`to`, `action`, `limit`+cursor) matches a direct-SQL oracle in the harness; invalid values fail closed with the versioned 400 envelope.
- [ ] Keyset cursor: page 2 continues exactly after page 1 with no duplicates/gaps; cursor is opaque and tampering fails closed.
- [ ] Cross-account and ghost ids share one indistinguishable 404/empty treatment consistent with the accepted reads (no existence leak in filters).
- [ ] UI shows table + filters + Spanish status labels; empty state renders; a denied/failed read shows the envelope error with matching correlation header/body; no `metadata` or message bodies appear anywhere in the response JSON dump.
- [ ] Regression: `compileall`, WU3–WU11 harnesses, `typecheck`, `npm test` green on a new disposable base `production_runtime_mvp_wu12`; earlier bases untouched; `work-unit-12-evidence.md` records commands and exit codes.

## Review Workload Forecast

Estimated authored implementation: 700–1,100 lines (SQL read module ~150, query service ~150, router+mount ~120, contract mirror ~150, UI view+filters ~250, harness ~250). 800-line budget risk: High. Chained PRs recommended: Yes. Decision needed before apply: No. Delivery: auto-chain, feature-branch-chain; single reviewable unit (WU12) atop the accepted chain.

## Risks and Rollback

Filter mapping could over- or under-report if the design's JOINs are misread against what audit actually stores (risk acknowledged in the brief; design pins exact predicates from audited code, not assumption). Large logs make unbounded reads slow — mitigated by mandatory `limit` + keyset cursor and the existing 003 account index. Rollback: revert WU12 modules/UI/contract slice and the router mount; accepted WU6 activity route, `audit_records` data, and migrations untouched by construction.

## Open Decisions

Confirm only at review: whether the UI "Actividad" mini-feed later consolidates into "Auditoría" (kept separate this unit), and whether per-row detail (correlation replay lookup) is worth a follow-up unit.
