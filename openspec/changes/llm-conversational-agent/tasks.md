# Tasks: LLM Conversational Agent (WU14)

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 1,100–1,600 authored lines |
| 800-line budget risk | High |
| Chained PRs recommended | Yes — single unit WU14 on the feature-branch-chain after WU13 |
| 400-line budget risk | High |

Decision needed before apply: No. Delivery: auto-chain, feature-branch-chain. Tests and evidence stay with the behavior they prove.

## Status note

No implementation code for this change exists yet. This unit depends on the accepted `calendar-agenda` change (WU13): its `calendar_events` table, `CreateCalendarEventCommand`/`UpdateCalendarEventCommand`/`CancelCalendarEventCommand`, and `GET /v1/demo/calendar/availability` read. `web/app/page.tsx` and `web/app/demo.css` carry the accepted WU12 "Auditoría" view and the WU13 "Calendario" view — audit the working tree before implementing; extend what is there, do not re-scaffold, and do not revert the accepted-adjacent edits without flagging.

## Phase Checklist

- [ ] Phase 0 — Working-tree audit: confirm no agent/LLM code pre-exists; confirm the WU12 audit-screen view and `composition_wu8_mvp.py` mount lines are as expected; record findings in evidence.
  - Verify: inventory + `npm.cmd run typecheck` green before starting.
- [ ] Phase 1 — Migration `007_agent_drafts.up.sql` / `.down.sql`: `agent_drafts` table + account and conversation indexes; FK pattern matching 004/005; CHECK on `status` and non-negative numerics where applicable.
  - Verify: runner applies 001–007 clean on a fresh base; `down` reverts.
- [ ] Phase 2 — `api/application/model_provider.py`: `ModelProvider` port, `INTENT_SCHEMA`, `validate_interpretation` (enum + type checks, unknown → fail), `OpenCodeGoProvider` (OpenAI-compatible POST, timeout, JSON parse, fail-closed), `StubModelProvider` (keyword classifier over body). Also `api/application/agent_persona.py`: persona constants + `build_system_prompt()` (persona + extraction instructions, español neutro, guardrails).
  - Verify: `api/test_model_provider.py` — schema accepts valid shapes, rejects unknown intent/criteria and malformed JSON; stub maps sample bodies to expected intents; provider parse errors surface as typed failures (no half-object).
- [ ] Phase 3 — `api/domain/agent_draft.py`: `AgentDraft`, `ProposedAction`, `INTENTS`; `api/application/conversation_agent.py`: orchestrate interpret → match → **schedule (slot pick over WU13 availability)** → propose (pure, injected `ModelProvider` + matcher callable + availability reader callable).
  - Verify: unit scenarios — search intent with criteria yields top-match interest + follow-up; schedule_visit yields `create_event` at a free slot; reschedule yields `reschedule_event` to a free slot; no model write access (port surface is `generate` only).
- [ ] Phase 4 — `api/adapters/postgres_agent_drafts.py`: `AgentDraftRepository` (insert/get/decide/finalize, scope-checked) + `search_properties_for_criteria` SQL (account predicate first, optional filters, params only).
  - Verify: harness A3 (match == direct-SQL oracle) and A9 (scope) partially; cross-account invisible.
- [ ] Phase 5 — `api/application/agent_commands_wu14.py`: `AnalyzeInboundMessageCommand` (atomic: interpret → persist `proposed` → audit/outbox), `DecideAgentDraftCommand` (atomic: approve/reject → audit/outbox), `finalize_agent_execution` (scoped UPDATE + `agent.draft.executed` audit). Approval dispatch maps to accepted `CreateFollowUpCommand`, `TransitionLeadStageCommand`, `RegisterPropertyInterestCommand`, `CreateCalendarEventCommand` (source=agent), `UpdateCalendarEventCommand` with per-action keys.
  - Verify: harness A1, A2, A4, A5, A6, A7, A8, A10, A15, A16.
- [ ] Phase 6 — `api/routes/agent_wu13.py`: `build_agent_router` with analyze/list/get/approve/reject; one additive `include_router` in `composition_wu8_mvp.py`.
  - Verify: harness A1 (forged account ignored), 401/403/404/405 envelope probes, `agent.draft.*` audit rows visible in `GET /v1/demo/audit`.
- [ ] Phase 7 — Contract mirror `contracts/runtime-v1.ts` (`AgentDraftResponse`, `ProposedAction`) + `contracts/runtime-v1.test.ts`; typed `analyzeMessage`/`listAgentDrafts`/`approveDraft`/`rejectDraft` in `web/app/demo-api.ts`.
  - Verify: vitest parser tests incl. rejecting unknown nested keys / invalid enums.
- [ ] Phase 8 — Inline draft cards in `web/app/page.tsx` conversation view: intent label, criteria chips, matched property previews, proposed actions, reply text, Aprobar/Rechazar; envelope error + correlation on failure.
  - Verify: jsdom vitest rendering the real page with stubbed fetch (card renders, approve/reject wire to typed endpoints, error state).
- [ ] Phase 9 — Harness `api/test_agent_wu14.py` on NEW disposable base `production_runtime_mvp_wu14` (runner 001–007 + both seeds; drafts generated via the real router with `StubModelProvider`).
  - Verify: A1–A16 PASS matrix, run twice for determinism, exit 0.
- [ ] Phase 10 — Regression + evidence: `python -m compileall -q api infra`; WU3–WU13 harnesses on their documented bases; `npm.cmd run typecheck` + `npm.cmd test`; `next build`/`next start` browser or DOM check with identical `NEXT_PUBLIC_*` env; write `work-unit-14-evidence.md` with exact commands + exit codes; status "implemented, pending review"; no `tasks.md` box checked without a real run behind it.

## Explicitly Out of Scope

Kapso/WhatsApp real channel or auto-send of the reply, multi-turn memory beyond message + contact/lead context, roles/permissions, RLS, productive auth, Docker, CI/CD, modifying accepted WU5/WU9/WU11/WU13 surfaces (the agent consumes them), embedding/vector search, free-form action JSON from the model, touching bases wu2–wu13.
