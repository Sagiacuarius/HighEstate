# Exploration: Local MVP Commercial Demo

## Current State

The repository has a proven local runtime and persistence foundation, but it does not yet expose product behavior. The static demo communicates the intended commercial experience for Aurea Realty, while the executable application currently proves only health, tenant-context, generic tenant-resource CRUD, transactional idempotency, and durable audit/outbox behavior.

### Evidence matrix

| Area | Visible in demo | Persisted/operational now | Existing API | Simulation/fixture status | Still missing |
|---|---|---|---|---|---|
| Agency / ClientAccount | Workspace selector and breadcrumb | PostgreSQL schema, repositories, tenant isolation | `GET /tenant/context/{client_account_id}` | WU3.5/WU4 validation seeds only | Production identity/session and product account UI |
| Leads / contacts | Conversation names and pipeline cards | No lead/contact tables or repositories | None | Static HTML only | CRUD, search, tags, ownership, pipeline relation |
| Properties | Two property cards and property search implication | Only generic `tenant_resource` | Generic resource routes, not a property API | Static HTML only | Property model, CRUD, account-scoped search |
| Pipeline | Three illustrative columns and cards | No pipeline entities | None | Static HTML only | Stages, lead transitions, counts |
| Inbox / conversations | Four recent conversations and WhatsApp labels | No conversation/message tables | None | Static HTML only | Threads, messages, unread state, local inbound simulation |
| Agenda | Three events and an agenda count | No task/follow-up/appointment model | None | Static HTML only | Basic follow-ups linked to leads |
| WhatsApp | “Connected via Kapso” presentation | No provider or channel implementation | None | Claim is visual only | Provider contract and real integration; not needed for first local demo |
| Conversational agent | Atelier branding and command modal | No agent runtime or product tool execution | Command API targets only generic resource | JavaScript echoes user text as a draft message | Deterministic local simulation or real agent; defer real agent |
| Automations | Three flow cards and operator suggestion | Durable audit/outbox seam exists, no automation model | None for automation | Static cards; command modal is non-operational | Automation definition, confirmation, executor, run history |
| Audit | No user-facing audit view | Durable audit/outbox for the WU6 command path | No product audit query API | WU6 fake consumer tests delivery seam | Audit events for CRM actions and a minimal activity view |

### Architectural findings

- `web/app/page.tsx` is intentionally a health-only Next.js entrypoint. `RuntimeHttpClient` supports only `/health/live` and `/health/ready`; it has no product query/mutation client.
- `api/app.py` wires health and the generic tenant boundary. The PostgreSQL compositions are test-oriented (`composition_wu4.py`/`composition_wu5.py`); the normal application process does not yet compose a product database service.
- `tenant_resource` is a representative isolation fixture, not a safe substitute for leads, properties, conversations, or pipeline records. Reusing it would hide domain boundaries and make the demo hard to evolve.
- PostgreSQL 18.6 is available natively on the local machine. Existing evidence proves schema constraints, scoped repositories, command idempotency, and audit/outbox behavior, but not product-domain persistence.
- The static demo already supplies a strong visual direction and navigation vocabulary. It must be treated as UX evidence and fixture content, not as persisted truth or real metrics.
- WACRM remains an audited reference. No WACRM code, dependency, or contract is required to prove the first local vertical slice.

## Affected Areas

- `web/app/page.tsx` — replace the health-only page with the first interactive commercial surface while retaining the existing runtime configuration boundary.
- `web/runtime-client.ts` — add typed product API calls only after the API contracts are defined; preserve correlation and error-envelope handling.
- `api/app.py` and a new product composition root — wire local PostgreSQL-backed CRM services without making test compositions the production composition.
- `api/` — add tenant-scoped product queries/commands for leads, properties, conversations/messages, pipeline stages, and follow-ups.
- `src/domain/` and `src/application/ports/` — add provider-neutral core contracts where appropriate; keep real-estate fields in a vertical module.
- `infra/migrations/` — add a forward-only product-domain migration after `003_audit_outbox`; do not alter accepted foundation migrations.
- `infra/migrations/validation/` — add repeatable local demo seed data for two client accounts, clearly separate from tests and production data.
- `contracts/` — define versioned response/request shapes for the demo API, including account scope and stable error/correlation behavior.
- `demo/agencia-inmobiliaria.html`, `demo/agencia-inmobiliaria.css`, `demo/agencia-inmobiliaria.js` — reference only for visual parity and interaction intent; no implementation change is required by exploration.
- `openspec/config.yaml` — existing constraints already prohibit treating fixtures as persisted behavior and defer Docker, CI/CD, and external providers.

## Minimum Valuable Vertical Slice

The smallest credible local demo should prove one end-to-end commercial loop:

1. Select `ClientAccount A` or `ClientAccount B` within the agency shell.
2. View account-scoped properties, leads, pipeline stages, conversations, and follow-ups.
3. Trigger a **local simulated inbound message** for a seeded contact.
4. Persist the conversation/message and create or update the related lead.
5. Move the lead to a pipeline stage and create a basic follow-up.
6. Show the resulting records and a compact activity/audit entry.
7. Switch accounts and demonstrate that properties, leads, and conversations do not cross the tenant boundary.

This slice gives a portfolio viewer visible CRM value, a working tenant story, durable local data, and a believable path toward WhatsApp and agents without pretending those integrations already exist.

## Approaches

| Approach | Pros | Cons | Complexity |
|---|---|---|---|
| A. Keep the static HTML and enrich it with client-side fixtures | Fastest visual result; almost no backend work | No persistence, no real tenant proof, actions reset on reload, weak evidence for a commercial platform | Low |
| B. Build a local persisted CRM vertical slice with simulated inbound messages | Demonstrates visible value and durable behavior; exercises existing PostgreSQL, tenant predicates, idempotency, and audit seams; no credentials or provider assumptions | Requires new domain tables, API contracts, composition, seed data, and a focused Next.js UI | Medium |
| C. Start with Kapso plus a conversational LLM agent | Closest to the long-term product narrative; real channel demonstration | Requires external contracts, credentials, webhooks, delivery/retry behavior, model policy, cost controls, and more failure modes before the core CRM is visible | High |
| D. Implement automation builder/operator first | Highlights the stated differentiation and WACRM reference | Depends on leads, events, actions, confirmation, versions, and execution history; large surface with little standalone value before CRM data exists | High |

## Recommendation

Choose **Approach B** as the first change: a local, PostgreSQL-backed commercial demo slice for Leads, Properties, Pipeline, Inbox/Conversations, and basic Follow-ups, with a deterministic local inbound-message simulator.

Keep the first change deliberately bounded:

- Use explicit product tables and repositories, not `tenant_resource` as a generic polymorphic CRM.
- Use the existing server-derived `TenantContext` and mandatory `client_account_id` predicates for every operation.
- Use a small versioned API contract and a real Next.js surface; retain the static demo as design reference rather than duplicating two applications.
- Seed two client accounts with intentionally different properties/leads/conversations so the isolation story is immediately visible.
- Represent “incoming WhatsApp” as a local simulator or development-only endpoint. It should create a normalized internal message event, not claim Kapso connectivity.
- Implement lead creation/update and follow-up as explicit validated commands, reusing the existing transaction/idempotency/audit conventions where the command is mutating.
- Defer real Kapso, LLM/LangGraph, visual automation editing, operator natural-language generation, external calendar, metrics beyond simple persisted counts, and production authentication.

The simulator is not a shortcut around architecture: it should enter through the same normalized internal event boundary that a future `WhatsAppProvider` adapter will use. This keeps the demo honest and makes the future provider an adapter replacement rather than a rewrite of CRM behavior.

### Suggested first-change boundaries

| Include | Exclude |
|---|---|
| CRM migration for properties, contacts/leads, pipeline/stages, conversations/messages, follow-ups | Kapso credentials, webhook verification, outbound delivery |
| PostgreSQL repositories and tenant-scoped queries | RLS, because application checks remain the current accepted mechanism and RLS is a separate ADR |
| API routes for read models plus local inbound simulation and validated lead/follow-up commands | Real agent runtime, LLM provider, LangGraph |
| Next.js dashboard with account switch, property list, lead pipeline, inbox, agenda and simulator action | Full visual automation canvas and operator agent |
| Development seed/reset procedure and focused executable evidence | Docker, Compose, VPS, CI/CD, deploy |

## Risks

- **Scope expansion:** implementing all PDR MVP bullets at once would recreate the original 800-line/high-risk foundation pattern. Keep the first change to one demonstrable loop and chain later work units.
- **Fixture confusion:** the existing HTML numbers and WU validation seeds must not be presented as live product data. Label local demo seed data and expose its reset path.
- **Composition gap:** the production FastAPI process currently lacks a normal PostgreSQL product composition. Without solving that boundary, the UI would only call test harnesses.
- **Authentication gap:** current identities are server-injected test/reference adapters. The local demo may use an explicit development identity, but it must be visibly development-only and must not become a production auth claim.
- **Audit coverage:** WU6 proves audit/outbox for the generic command path, not every future CRM action. Product commands need explicit audit records and no false “complete audit” claim.
- **Read/write model drift:** summary cards and pipeline counts can diverge if they are independently hard-coded. Derive visible counts from persisted records or clearly mark them as unavailable.
- **Integration temptation:** WhatsApp and an LLM are not indispensable to demonstrate CRM value. Adding them now introduces external contracts and credentials that the user explicitly asked to postpone.
- **Review workload:** the requested review budget is 800 authored lines and auto-chain is enabled. Forecast the implementation as multiple reviewable slices if the UI, migration, API, and evidence exceed that budget.

## Ready for Proposal

**Yes.** The next phase should propose Approach B as a bounded local commercial-demo change, with the simulated inbound message as the only channel integration and with explicit non-goals for Kapso, agents, automations, external calendar, Docker, CI/CD, VPS, and WACRM adoption.
