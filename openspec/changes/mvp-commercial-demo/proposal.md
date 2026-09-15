# Proposal: MVP Commercial Demo

## Intent

Turn the existing visual mockup and runtime foundation into one local, persisted commercial loop: a demo user selects a `ClientAccount`, receives a simulated inbound message, and sees lead/contact, pipeline, conversation, follow-up, and activity evidence update in PostgreSQL. This proves product value and tenant isolation before external integrations or production identity.

## Scope

### In Scope
- Product-owned entities and PostgreSQL migration after `003_audit_outbox` for contacts/leads, properties, pipeline/stages, conversations/messages, and follow-ups; never modify `001–003`.
- Tenant-scoped repositories, predicates, FastAPI read models and validated commands, using existing transaction, idempotency, audit, and outbox capabilities.
- Deterministic local inbound simulator via visible Inbox button and reproducible internal endpoint; create/update lead, transition pipeline, persist follow-up.
- Next.js dashboard using real data while reusing mockup aesthetics/navigation, with marked demo-user/account selectors and local seed/reset for two distinct accounts.
- Small, independently reviewable chained work units: contracts/domain; migration/seed; repositories/composition; commands/simulator; read API; dashboard; end-to-end evidence.

### Out of Scope / No-goals
- Do not use `tenant_resource` as a generic CRM; do not treat HTML fixtures as live data.
- No Kapso integration claim, provider credentials, webhook/outbound delivery, production authentication, LLM agent, LangGraph, complete visual automations, meetings, calendar, forms, Docker, VPS, CI/CD, or deployment.

## Capabilities

### New Capabilities
- `commercial-crm-demo`: persisted account-scoped CRM vertical slice and local simulator.
- `demo-account-switching`: visible local account/user context and seed/reset behavior.

### Modified Capabilities
- None.

## Approach

Use explicit product tables and provider-neutral normalized inbound events behind `Agency → ClientAccount`. Add a normal FastAPI PostgreSQL composition, versioned contracts, and typed Next.js calls. Derive dashboard counts and views from persisted records; label simulation and demo identity visibly.

## Acceptance

- [ ] End-to-end: select either account, simulate inbound message, observe durable conversation/message, lead create/update, pipeline transition, follow-up, and audit/activity evidence after reload.
- [ ] Switching accounts shows distinct properties/leads/conversations and no cross-account reads or writes.
- [ ] Replaying the same command is idempotent; failures use existing correlation/error conventions.
- [ ] Seed/reset restores two deterministic accounts without changing foundation migrations.

## Review Workload Forecast

Estimated authored implementation: 1,200–1,800 lines; 800-line budget risk: High. Decision needed before apply: No. Chained PRs recommended: Yes. 400-line budget risk: High. Delivery: auto-chain with feature-branch-chain; each work unit has its own verification and rollback boundary.

## Risks and Rollback

Composition or tenant predicate gaps could expose test-only behavior or data. Mitigate with isolation checks, distinct seeds, and end-to-end evidence. Roll back by reverting feature-chain slices and removing only new migration/data/API/UI paths; preserve `001–003` and WU1–WU6 seams.

## Open Decisions

Confirm only: exact demo account/user labels and the canonical simulator payload; choose the migration runner/seed command during design without expanding scope.
