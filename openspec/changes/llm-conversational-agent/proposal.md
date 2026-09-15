# Proposal: LLM Conversational Agent

## Intent

Add the first intelligence layer to the CRM: a tenant-scoped conversational agent that, for an inbound message, interprets intent and extracted criteria via a swappable `ModelProvider`, deterministically matches those criteria against the accepted WU11 property catalog, and proposes a **draft** of actions — register interest, create follow-up, transition lead, and **schedule/reschedule visits against the WU13 calendar** (create event, see availability, reschedule) — plus a draft reply, without executing anything. The advisor approves or rejects the draft **inline in the conversation view**; approval dispatches the proposed actions through the accepted atomic commands (WU5/WU11/WU13), so every effect is idempotent, audited, and outbox-recorded by construction. This replaces nothing: the accepted WU5 deterministic inbound effect, the WU9 simulator, and the WU13 calendar keep working unchanged; the agent is a new additive surface that consumes them.

## Scope

### In Scope
- `ModelProvider` port with two adapters: `OpenCodeGoProvider` (OpenAI-compatible HTTP to `https://opencode.ai/zen/go/v1/chat/completions`, model `deepseek-v4-pro` default, `Bearer` auth from `OPENCODE_GO_API_KEY`) and `StubModelProvider` (deterministic, no network) for tests and offline demo.
- `ConversationAgent` orchestration: interpret (LLM, one structured call) → match (deterministic SQL over `properties`) → **schedule (deterministic slot pick over WU13 availability)** → propose (deterministic intent→action mapping incl. `create_event`/`reschedule_event`) → persist draft.
- A first-class **agent persona** (`agent_persona.py`): identity ("Sofía"), role (asesora inmobiliaria virtual), tone (cordial, profesional, empática, concisa), language (español neutro/profesional), and guardrails (never confirm availability or schedule visits; always defer to the human advisor). The persona shapes the draft `reply_text` only; interpretation is persona-independent.
- **Dependency**: the accepted `calendar-agenda` change (WU13) provides `calendar_events`, `CreateCalendarEventCommand`/`UpdateCalendarEventCommand`/`CancelCalendarEventCommand`, and `GET /v1/demo/calendar/availability`. The agent consumes those reads/commands; it never re-implements event SQL or slot math.
- New `agent_drafts` table (migration `007`) with `status IN (proposed, approved, rejected, executed)`, `intent`, `criteria` jsonb, `matched_property_ids` jsonb, `proposed_actions` jsonb, `reply_text`, `execution_result` jsonb, tenant-scoped with `client_account_id` predicate on every read/write.
- Two atomic commands (`AnalyzeInboundMessageCommand` → `proposed`; `DecideAgentDraftCommand` → `approved`/`rejected`), each following the accepted `CrmCommandBase` template (reserve idempotency → scoped write → one audit + one outbox → commit). Approval **dispatches** the proposed actions to accepted commands (`CreateFollowUpCommand`, `TransitionLeadStageCommand`, `RegisterPropertyInterestCommand`, `CreateCalendarEventCommand`, `UpdateCalendarEventCommand`) with per-action idempotency keys derived from the draft, then records `execution_result` + one `agent.draft.executed` audit.
- Agent router (`agent_wu13`) mounted additively on the accepted WU8 app: `POST /v1/demo/agent/analyze`, `GET /v1/demo/agent/drafts`, `GET /v1/demo/agent/drafts/{id}`, `POST /v1/demo/agent/drafts/{id}/approve`, `POST /v1/demo/agent/drafts/{id}/reject`.
- TS contract mirror in `contracts/runtime-v1.ts` for the draft response (strict parser) and typed methods in `web/app/demo-api.ts`; inline draft cards with Aprobar/Rechazar in the conversation view.

### Out of Scope / No-goals
- No Kapso/WhatsApp real channel; the reply is a draft only, never auto-sent.
- No multi-turn memory beyond message + contact/lead context.
- No roles/permissions (operator surface, like WU12); no RLS, productive auth, Docker, CI/CD.
- No modification of the accepted WU5 inbound effect, WU11 reads, or WU9 simulator internals.
- No free-text property search from arbitrary criteria beyond the pinned extraction fields; no embedding/vector search.

## Capabilities

### New Capabilities
- `llm-conversational-agent`: interpret inbound message, deterministic property matching, propose a confirmable action draft, execute approved draft via accepted commands.

### Modified Capabilities
- None at the requirement level. `composition_wu8_mvp.py` gains one additive `include_router` (same seam WU9/WU12 used); accepted routes and their bytes stay untouched.

## Approach

Add a `model_provider` application module (port + adapters + intent schema), a `conversation_agent` orchestration service, a `postgres_agent_drafts` SQL adapter (draft persistence + agent-specific property search), two atomic commands in `agent_commands_wu13.py`, a read service, and an `agent_wu13` APIRouter mounted on the WU8 app. Migration `006` adds `agent_drafts`. The frontend adds inline draft cards to the conversation view that call analyze/approve/reject.

## Acceptance

- [ ] `POST /v1/demo/agent/analyze` returns a `proposed` draft whose `client_account_id` is server-resolved; a forged account id cannot change scope; the message body is the only client-controlled input that reaches the model.
- [ ] The model never receives a database connection or write access: the `ModelProvider` port surface is exactly `generate(system, user, schema) -> dict`, and both adapters return only parsed/validated interpretation dicts.
- [ ] Interpretation is schema-validated before any persistence: unknown `intent`/`criteria` shapes or malformed model JSON fail closed (400/502 envelope), never half-persist.
- [ ] Matching is deterministic and tenant-scoped: for a given `criteria`, the returned `matched_property_ids` equals a direct-SQL oracle over `properties` (operation_type, property_type, city, budget_max, rooms_min, bathrooms_min, `status='available'`); cross-account properties are invisible.
- [ ] Scheduling is deterministic and validated: for `schedule_visit`, the draft proposes a `create_event` action whose slot equals a free WU13 availability slot (preferred date/time honored when present, else the first free slot); for `reschedule`, the draft proposes a `reschedule_event` action to a free slot; an unavailable slot never appears; the proposed event is never persisted until approval.
- [ ] Draft lifecycle is monotonic and idempotent: `proposed → approved|rejected → executed`; re-analyzing the same body with the same idempotency key replays the stored draft; approving twice yields one execution; approving a rejected/executed draft fails closed (409).
- [ ] Approval dispatches each proposed action to its accepted command with a per-action idempotency key; each executed action produces its own audit/outbox record (calendar events land with `source=agent`); the draft records `execution_result` naming successes/denials; rejecting executes nothing.
- [ ] All reads and mutations carry the server-derived `client_account_id` predicate; foreign/ghost draft ids share one indistinguishable 404/empty treatment.
- [ ] UI renders inline draft cards (intent, criteria, matched properties, reply, Aprobar/Rechazar) in the conversation view; approve/reject call the typed endpoints; errors use the versioned envelope with matching correlation header/body.
- [ ] Regression: `compileall`, WU3–WU13 harnesses, `typecheck`, `npm test` green on new disposable base `production_runtime_mvp_wu14` (runner 001–007 + seeds); earlier bases untouched; `work-unit-14-evidence.md` records commands and exit codes.

## Review Workload Forecast

Estimated authored implementation: 1,100–1,600 lines (model provider + persona + schema ~320, agent orchestration + scheduling ~320, draft SQL adapter ~180, two atomic commands + finalize ~320, agent router ~200, read service ~100, migration 007 ~50, contract mirror ~160, UI inline cards + scheduling preview ~300, harness ~340). 800-line budget risk: High. Chained PRs recommended: Yes. Decision needed before apply: No. Delivery: auto-chain, feature-branch-chain; single reviewable unit (WU14) atop the accepted chain.

## Risks and Rollback

LLM output is non-deterministic: mitigated by a strict intent schema with fail-closed validation and the deterministic matching/action layers (the model only fills a bounded JSON; the system never trusts free text). Missing `OPENCODE_GO_API_KEY` at runtime: fall back to `StubModelProvider` and surface the provider name in the draft (`model_provider`) so the demo never 500s on a missing key. Approval is a saga (multi-command dispatch), so a mid-sequence failure leaves prior actions applied: each action is idempotent and the draft's `execution_result` records exactly what ran — the advisor sees the truth and can re-run safely. Rollback: delete the WU13 modules/UI/contract slice, the router mount, and migration `006` (down); accepted WU5/WU9/WU11 surfaces and all prior data untouched by construction.

## Open Decisions

Confirm only at review: whether to auto-run analyze on every simulator send (vs. an explicit "Analizar" button), and whether the draft reply should later feed an outbound (Kapso) send — both deferred.
