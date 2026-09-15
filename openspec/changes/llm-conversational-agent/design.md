# Design: LLM Conversational Agent

## Technical Approach

Additive intelligence layer over accepted seams, never editing them. The agent is a pure "interpret → match → propose → persist" pipeline; only the interpretation calls the LLM. Matching and action derivation are deterministic SQL/mapping so the system never trusts free text for effects. Drafts are durable, tenant-scoped, audited rows; approval is a saga that dispatches each proposed action to the accepted atomic command (each idempotent and audited), then records the aggregate result. Mounted as an `agent_wu13` APIRouter on the accepted WU8 app exactly like WU9/WU12 (shared resolver/cache/runner state, one additive `include_router`).

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| LLM surface | `ModelProvider.generate(system, user, schema) -> dict` port; only `OpenCodeGoProvider` and `StubModelProvider` implement it | The model has zero access to DB, commands, or context beyond the prompt. This is the blast-radius boundary. |
| Provider transport | OpenAI-compatible `POST https://opencode.ai/zen/go/v1/chat/completions`, `Authorization: Bearer <OPENCODE_GO_API_KEY>`, `model: deepseek-v4-pro`, `response_format` json object, `temperature: 0`; key from env, absent → `StubModelProvider` fallback | opencode-go is an OpenAI-compatible gateway (verified: base `https://opencode.ai/zen/go/v1/chat/completions`). JSON mode + strict schema keeps parsing fail-closed. |
| Agent persona | First-class `agent_persona.py` module: `IDENTITY` ("Sofía"), `ROLE` (asesora inmobiliaria virtual), `TONE` (cordial, profesional, empática, concisa), `LANGUAGE` (español neutro/profesional), `GUARDRAILS` (never confirm availability or schedule visits; always defer to the human advisor). `build_system_prompt()` renders persona + extraction instructions. | The persona gives the agent a consistent brand voice and bounds what it may say to leads. It is injected into the system prompt and shapes `reply_text` only. |
| Interpretation schema | Bounded enum + optional numeric/bool fields: `intent ∈ {search_properties, schedule_visit, reschedule, ask_question, update_preferences, general}`, `criteria {operation_type, property_type, city, budget_max, rooms_min, bathrooms_min, parking, preferred_date, preferred_time}`, `reply_text` | The model fills a fixed shape; unknown enum values / wrong types are rejected before persistence (400/502). No free-form action JSON from the model. |
| Matching | Deterministic SQL over `properties` (agent-owned method, not WU11's `list_properties`): `client_account_id` predicate first, then `operation_type`, `property_type`, `city ILIKE`, `price_amount <= budget_max`, `rooms >= rooms_min`, `bathrooms >= bathrooms_min`, `status='available'`, ordered by price, LIMIT 10 | WU11 reads stay untouched; the agent needs rooms/bathrooms/operation filters WU11 does not expose. Budget semantics: `rent/temporary_rent` monthly, `sale` total — same `price_amount` column, documented limitation (expenses excluded). |
| Action derivation | Deterministic `intent → ProposedAction[]` mapping, not the LLM: search → `register_interest(top_match)` + `create_follow_up("Enviar propiedades al lead")`; schedule_visit → `create_event(title, slot)` (from availability) + `create_follow_up("Preparar visita")`; reschedule → `reschedule_event(event_id, new_slot)`; ask_question/update_preferences/general → one `create_follow_up` | Keeps effects auditable and reproducible; the LLM only classifies intent and extracts criteria. |
| Scheduling | Deterministic slot pick over the accepted WU13 availability read: query `GET /v1/demo/calendar/availability` for a window (next 7 days, or ±3 days around `preferred_date`), honor `preferred_time` when present (morning = 09:00–12:59, afternoon = 13:00–17:00), else pick the first free slot. Emit `create_event`/`reschedule_event` with concrete `starts_at`/`ends_at` (60-min). | The agent never invents a free slot; it reads availability and picks. The slot is a proposal only — persisted on approval via WU13 commands. |
| Draft persistence | Migration `007_agent_drafts`: `id, client_account_id, conversation_id, message_id, contact_id, lead_id, status ∈ {proposed,approved,rejected,executed}, intent, criteria jsonb, matched_property_ids jsonb, proposed_actions jsonb, reply_text, model_provider, model_name, created_at, decided_at, decided_by, execution_result jsonb` + account index + conversation index | Mirrors the durable/tenant-scoped discipline of 004/005; jsonb columns hold bounded, schema-validated payloads only. |
| Analyze command | `AnalyzeInboundMessageCommand` (atomic, `CrmCommandBase`): reserve idempotency → resolve canonical conversation/contact/lead → call `ConversationAgent` → INSERT draft `proposed` → audit `agent.draft.proposed` + outbox → commit | One transaction makes interpretation + draft durable together; replay returns the stored draft (idempotent). |
| Decide command | `DecideAgentDraftCommand` (atomic): load+lock draft (scope-checked, must be `proposed`) → approve sets `approved` / reject sets `rejected` → audit `agent.draft.approved|rejected` + outbox | The decision is atomic; a rejected or non-`proposed` draft fails closed (409). |
| Approval execution | Saga dispatch: after `DecideAgentDraftCommand` approves, the ROUTER dispatches each `ProposedAction` to its accepted command (`CreateFollowUpCommand.execute_follow_up`, `TransitionLeadStageCommand.execute_transition`, `RegisterPropertyInterestCommand.execute_register`, `CreateCalendarEventCommand` with `source=agent`, `UpdateCalendarEventCommand` for reschedule) with key `agent-draft:{draft_id}:{index}`, collects outcomes, then records `execution_result` + one `agent.draft.executed` audit via a small finalize helper | Reuses the exact accepted command semantics (idempotency + audit + outbox) rather than duplicating SQL. Non-atomic by design (saga); each action idempotent, and `execution_result` records exactly what ran so a retry is safe. |
| Draft reads | `GET /v1/demo/agent/drafts?conversation_id=` and `GET /v1/demo/agent/drafts/{id}` — tenant-scoped, no cache (drafts are write-churny; correctness > latency) | Matches WU12's no-cache decision for the operator trail. |
| Mount point | `build_agent_router(conn_kwargs, model_provider)` APIRouter included on the WU8 app under `/v1/demo/agent` (shared `app.state.demo_composition` resolver/cache) | WU9/WU12 precedent; accepted routes byte-identical. |
| UI | Inline draft cards in the conversation view (after the inbound message): intent label, extracted criteria chips, matched property previews (title/price/operation), proposed actions list, reply text, Aprobar/Rechazar buttons; `statusLabel` reuse for intent/status; error state uses envelope + correlation | Confirmation is inline in the conversation, as chosen. |

## Contracts, Invariants, and Flow

Flow: `POST /v1/demo/agent/analyze {body}` → resolve session context → canonical conversation (same slot as WU9) → contact/lead → `ConversationAgent.analyze(context, body)` → interpret (model) → match (SQL) → propose (map) → `AnalyzeInboundMessageCommand` persists `proposed` draft → 200 `{draft}`. The frontend renders the draft inline; Aprobar → `POST /v1/demo/agent/drafts/{id}/approve` → decide (approved) → dispatch actions → record `execution_result` → 200 `{draft, execution_result}`; Rechazar → `POST .../reject` → decide (rejected) → 200 `{draft}`.

Draft response shape (contract mirror `AgentDraftResponse`):
`{ draft_id, status, intent, criteria, matched_property_ids, proposed_actions: [{type, params}], reply_text, model_provider, model_name, conversation_id, contact_id, lead_id, created_at, decided_at, execution_result, account_id, correlation_id }`. Parser rejects unknown nested keys and invalid enums (same strictness as accepted read mirrors). `matched_property_ids` in the analyze response carries preview objects (property_id, title, price_amount, operation_type, city) fetched from the same deterministic match; only ids are persisted on the draft.

Invariants: every read/write carries server-derived `client_account_id`; draft status is monotonic; interpretation is schema-validated before persistence; the model receives only `(system_prompt, message_body)`; approval never re-runs a non-`proposed` draft; cross-account draft ids are non-enumerating (404/empty).

SQL shape (draft search, single statement, parameters only, account predicate first):
```sql
SELECT id, title, operation_type, price_amount, city, property_type, rooms, bathrooms
FROM properties
WHERE client_account_id = %s
  AND status = 'available'
  [AND operation_type = %s]      -- when criteria.operation_type present
  [AND property_type = %s]       -- when criteria.property_type present
  [AND city ILIKE %s]            -- when criteria.city present
  [AND price_amount <= %s]       -- when criteria.budget_max present
  [AND (rooms >= %s OR rooms IS NULL)]      -- when criteria.rooms_min present
  [AND (bathrooms >= %s OR bathrooms IS NULL)] -- when criteria.bathrooms_min present
ORDER BY price_amount ASC, id
LIMIT 10
```

## Files and Composition

- `api/application/model_provider.py` — `ModelProvider` protocol, `OpenCodeGoProvider` (httpx/psycopg-free, stdlib `urllib` acceptable; timeout + fail-closed parse), `StubModelProvider` (keyword classifier), `INTENT_SCHEMA` + `validate_interpretation`.
- `api/application/agent_persona.py` — persona constants (`IDENTITY`, `ROLE`, `TONE`, `LANGUAGE`, `GUARDRAILS`) + `build_system_prompt()` (persona + extraction instructions).
- `api/domain/agent_draft.py` — `AgentDraft`, `ProposedAction`, intent/criteria enums, `INTENTS`.
- `api/application/conversation_agent.py` — `ConversationAgent` orchestration (interpret → match → schedule → propose), pure of any HTTP/DB access except the injected `ModelProvider` + a matcher callable + an availability reader callable.
- `api/adapters/postgres_agent_drafts.py` — `AgentDraftRepository` (insert/get/decide/finalize, scope-checked) + `search_properties_for_criteria` SQL.
- `api/application/agent_commands_wu13.py` — `AnalyzeInboundMessageCommand`, `DecideAgentDraftCommand`, `finalize_agent_execution` helper (scoped UPDATE + `agent.draft.executed` audit).
- `api/routes/agent_wu13.py` — `build_agent_router(conn_kwargs, model_provider)` with analyze/list/get/approve/reject; wired in `composition_wu8_mvp.py` with one `include_router` (additive).
- `infra/migrations/007_agent_drafts.up.sql` / `.down.sql` — table + indexes.
- `contracts/runtime-v1.ts` — `AgentDraftResponse`/`ProposedAction` + strict parser + `contracts/runtime-v1.test.ts` fixtures.
- `web/app/demo-api.ts` — `analyzeMessage`, `listAgentDrafts`, `approveDraft`, `rejectDraft`; `web/app/page.tsx` — inline draft cards + Aprobar/Rechazar; `demo.css` only if a class is missing.
- `api/test_model_provider.py` — stub + provider parse/validation unit scenarios (no network).
- `api/test_agent_wu14.py` — real-PostgreSQL harness on NEW base `production_runtime_mvp_wu14` (runner 001–007 + both seeds; drafts generated by running analyze through the real router with a `StubModelProvider` — never hand-inserted).

## Testing Strategy

Harness scenarios: A1 analyze persists a `proposed` draft with server-scoped account + forged account ignored; A2 interpretation schema rejects unknown intent/criteria and malformed model JSON (fail-closed, nothing persisted); A3 deterministic match equals a direct-SQL oracle (operation_type/city/budget/rooms/bathrooms; cross-account invisible); A4 idempotent analyze replay (same key → stored draft); A5 approve dispatches each proposed action to its accepted command with per-action keys and each produces its own audit/outbox; A6 approve-twice yields one execution; A7 approve on rejected/executed → 409; A8 reject executes nothing and audits `agent.draft.rejected`; A9 reads tenant-scoped + foreign/ghost draft ids non-enumerating; A10 execution_result records successes/denials truthfully (inject a denied action); A11 accepted WU5 inbound + WU9 simulator unchanged (regression pin); A12 A/B isolation; A13 schedule_visit proposes a `create_event` at a real free WU13 availability slot (preferred date/time honored when present); A14 reschedule proposes a `reschedule_event` to a free slot (event id resolves in scope); A15 an occupied slot is never proposed and approval of a stale slot fails closed on overlap; A16 approved calendar events land with `source=agent` and are visible in the WU13 calendar read + audit screen. UI: vitest jsdom renders the real page with stubbed fetch asserting draft card + approve/reject wiring (WU8 pattern). Regression: compileall + WU3–WU13 harnesses on their documented bases + typecheck + npm test; new base only; `work-unit-14-evidence.md` with exit codes; status "implemented, pending review".

## Risks and Rollback

Non-deterministic model output — bounded by strict schema + fail-closed validation; the model never drives effects directly. Missing API key at runtime — `StubModelProvider` fallback with `model_provider` surfaced on the draft. Saga non-atomicity — idempotent actions + truthful `execution_result`. Rollback: remove router mount + WU13 modules + UI + contract section, and run `006` down; accepted WU5/WU9/WU11 surfaces and all prior data untouched by construction.
