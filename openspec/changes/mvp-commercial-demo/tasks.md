# Tasks: MVP Commercial Demo

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 1,500–2,200 authored lines |
| 800-line budget risk | High |
| Chained PRs recommended | Yes |
| PRs / delivery | 10; auto-chain; feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

Each PR targets the prior boundary: PR1 → `feature/mvp-commercial-demo`; PR2 → PR1; …; PR10 → PR9. Tests and evidence stay with the behavior they prove. Labels and canonical simulator payload remain open decisions, not facts.

### Suggested Work Units

| Unit | Objective; tasks; dependencies; areas | Acceptance; verification; runtime; rollback; estimate |
|---|---|---|
| 1 | Define CRM ports/contracts and selector intent; **RED first**: unknown route, forged/malformed/foreign scope, non-enumerating denial. Depends WU2/WU5/WU6; `api/`, `contracts/`. | `pytest api/test_*`; denial/no-mutation. Revert domain/contracts. **150–220** |
| 2 | Add `004_commercial_crm` up/down, runner, and marked deterministic seed/reset for two accounts; never edit `001–003`. Depends 1; `infra/migrations/`. | `python infra/migrations/runner.py`; apply/FK/reset/concurrency. Remove 004/seed runner. **180–260** |
| 3 | Add explicit entities and tenant-scoped PostgreSQL repositories for all CRM aggregates and composite relations. Depends 1–2, WU3.5/WU4; `api/domain`, `api/adapters`. | `pytest api/test_postgres_crm.py`; real-Postgres A/B isolation. Remove repositories, retain schema. **220–320** |
| 4 | Wire normal FastAPI PostgreSQL composition and provider-neutral normalized inbound boundary. Depends 1–3; `api/composition.py`, `api/app.py`, `api/inbound/`. | `pytest api/test_inbound.py`; valid/invalid local event. Revert composition adapter. **120–180** |
| 5 | Add atomic lead, pipeline, follow-up commands with WU5/WU6 hash, idempotency, transaction, audit/outbox. Depends 3–4; `api/application/commands/`. | `pytest api/test_crm_commands.py`; loop/replay/conflict. Revert handlers. **180–260** |
| 6 | Add versioned read APIs for dashboard, Inbox, leads, properties, pipeline, agenda, activity; derive counts from DB. Depends 3–5; `api/queries`, `api/routes`, `contracts/`. | `pytest api/test_crm_reads.py`; scoped GET-after-command. Remove read routes. **160–240** |
| 7 | Add server-owned dev identity, membership selector, context composition, scoped cache keys, seed/reset controls. Depends 4–6; `api/identity`, `web/runtime-client.ts`. | `pytest api/test_demo_context.py`; A↔B switch/reload. Revert context wiring. **130–190** |
| 8 | Replace health page with real-data dashboard; adapt `demo/agencia-inmobiliaria.css`, never fixture state. Depends 6–7; `web/app`, CSS adapter. | `npm test && npm run build`; browser views/reload/switch. Revert UI/CSS. **220–320** |
| 9 | Add dev-only simulation endpoint and labeled Inbox button using the approved canonical payload. Depends 5,7–8; `api/routes`, `web`. | `pytest api/test_simulator.py`; click twice, one effect/result. Revert endpoint/button. **90–140** |
| 10 | Add PostgreSQL E2E harness/evidence for full loop, A/B isolation, reset, replay/conflict, denial. Depends 2–9; `api/tests`, `web/tests`, `openspec/.../evidence.md`. | `pytest -m e2e`; browser/API journey proves reload durability. Remove harness/evidence. **180–280** |

## Phase Checklist

- [ ] Phase 1: contracts, migration, seed, entities, repositories, composition (WU1–4).
- [ ] Phase 2: atomic commands and read APIs (WU5–6).
- [ ] Phase 3: selector, dashboard, simulator (WU7–9).
- [ ] Phase 4: PostgreSQL E2E evidence and isolation proof (WU10).

## Explicitly Out of Scope

Docker, VPS, CI/CD, Kapso, real WhatsApp, LLM/LangGraph, productive auth, meetings, calendar, forms, WACRM, and complete automations.
