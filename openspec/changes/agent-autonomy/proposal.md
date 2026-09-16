# Proposal: Agent Autonomy

## Intent

Make the conversational agent automatic for the demo lead. The accepted `llm-conversational-agent` flow — every reply is a draft, every action waits on an advisor's approve click — is superseded for the demo path by an explicit owner product decision (2026-09-16): the human advisor is the **escalation target, not a mandatory reviewer**. A bounded, deterministic policy layer decides per draft which effects execute on their own — answer a FAQ from a new seeded knowledge base, answer a price question from the live property catalog, match and search properties, transition the lead's pipeline stage — while anything low-confidence or handoff-triggering persists but does not execute and surfaces to the advisor instead. A first-class `handoff_to_human` action pauses auto-replies for a conversation, flags it in the inbox, and creates a follow-up. Every effect, automatic or manual, still runs through the accepted saga: the draft row remains the durable audit record, and per-action idempotency, audit and outbox hold unchanged. The model integration is upgraded to LangChain (owner-mandated) strictly behind the existing `ModelProvider` port — the deterministic layers and the offline `StubModelProvider` survive, so the demo works with no API key.

## Scope

### In Scope
- **Auto-execution policy** (`agent_policy` application module): deterministic mapping from intent + confidence signals to auto-execute vs pause-and-escalate. Default auto set: FAQ answer, price answer, property search/register-interest, lead classification. Anything else, low confidence, or a handoff trigger escalates. `ConversationAgent._derive_actions` gains the `handoff` and price-lookup outputs; the model never picks actions.
- **Superseded draft gate (honest capability modification)**: auto-policy decisions execute without a human click. The existing approve/reject endpoints remain as a manual override surface, not the primary path.
- **Seeded knowledge base**: new table(s) via migration `008` with curated FAQ answers for typical client/inmobiliaria interactions (expensas, escritura/registro, garantía/fiadores, documentos para alquilar, financiación/PH, amenities, requisitos, políticas de visita). Retrieval is deterministic SQL keyword/simple scoring over tenant-scoped rows (every read carries the `client_account_id` predicate, matching the codebase-wide boundary; the shared-demo alternative is rejected as it would create the first cross-tenant read path); **no pgvector, no embeddings in this unit**. A no-match retrieval is treated as low confidence: escalate, never fabricate.
- **Pricing answered from the catalog, not from text**: price questions resolve against the live `properties` row via the existing deterministic search read (current `price_amount`/fields). Reference resolution ("¿cuánto sale?" about the last discussed property) implies conversation-state context: this unit ships a minimal last-matched-property fallback and flags full conversational memory as a LangGraph design candidate for the design phase.
- **First-class handoff**: new `handoff` intent and `handoff_to_human` proposed-action type, triggered by (a) explicit requests ("quiero hablar con un humano/asesor") and (b) complaint/objection classification signals. Effects: conversation flagged for the advisor (visible in the inbox), follow-up created, auto-replies **paused** for that conversation until a human resumes manually; a per-conversation pause state column arrives with migration `008`.
- **`transition_lead` wired end-to-end**: verified gap — the type exists in `PROPOSED_ACTION_TYPES` but the planner never emits it and the dispatcher has no branch (it records `unsupported`). Deterministic classification maps extracted signals (explicit budget + timeline + city, qualified search, scheduled visit) to stage updates via the accepted WU5 `TransitionLeadStageCommand`, with its own idempotency key, audit and outbox.
- **LangChain integration behind the `ModelProvider` port** (owner-mandated): the real provider's internals move to `langchain-openai` `ChatOpenAI` pointed at the opencode-go OpenAI-compatible base URL; FAQ/answer assembly is a LangChain chain. Versions pinned in `api/requirements.txt`. The port surface and every deterministic layer (schema validation, matching, slot picking, action mapping, policy) are unchanged, and `StubModelProvider` remains network-free and LangChain-free so the demo still runs with no API key.
- **Auto path through the existing saga**: draft rows stay the durable audit record; the status path and `agent_drafts` intent/decided columns are extended via migration `008` (`decided_by` is a uuid column today, so the policy marker shape — e.g. `agent-policy`-style attribution — is design-phase). Per-action idempotency + audit + outbox behave exactly as under human approval today.
- **Outbound demo replies persisted** as `messages` rows with `direction='outbound'` — no migration needed: the column and its `CHECK (direction IN ('inbound','outbound'))` have existed since migration `004`, and the dashboard inbox already renders per-direction bubbles.
- **Roadmap note**: full custom-field updates on the lead depend on the planned Configuración unit; this change classifies via existing stage transitions only. This unit is proposed in a WU15-ish slot named `agent-autonomy`; a follow-up `agent-console` unit (sidebar management view: settings, prompt editing, KB CRUD, calendar binding, lead-field allowlists — owner decision 2026-09-16, requirements in `openspec/changes/agent-console/brief.md`) follows, and the Configuración unit may slide further in the roadmap.
- New/extended routes on the agent router (mount pattern unchanged), TS contract mirrors in `contracts/runtime-v1.ts` + typed `demo-api.ts` methods, dashboard inbox handoff flag / paused indicator, and harnesses for policy, KB, handoff and the transition lead path.

### Out of Scope / No-goals
- No Kapso/WhatsApp real channel: demo outbound = a persisted outbound message visible in the dashboard inbox.
- No RLS, productive auth, Docker, CI/CD.
- No embeddings/vector search; no pgvector.
- No undo UX for auto-actions (to be designed separately).
- No modification of accepted WU5–WU13 surfaces; no lead custom-field editing.
- No multi-turn conversational memory beyond the minimal last-property fallback.

## Capabilities

### New Capabilities
- `agent-autonomy`: policy-gated automatic execution of agent drafts, seeded deterministic knowledge-base retrieval, catalog-grounded price answers, `handoff_to_human` with per-conversation auto-reply pause, `transition_lead` classification, LangChain-backed real provider behind the unchanged port.

### Modified Capabilities
- `llm-conversational-agent` (declared honestly): the accepted requirements that the reply is "a draft only, never auto-sent" and that every draft waits for advisor approval are **superseded** for the auto-policy path. The draft lifecycle survives as the audit spine; approve/reject remain as override controls. The `agent_drafts` intent/status vocabulary is extended (migration `008`); accepted command seams (WU5/WU11/WU13) are consumed, not modified.

## Approach

Extend `conversation_agent.py` planning with KB-grounded answer and price-lookup outputs; add an `agent_policy` module (pure, testable) consulted by the analyze path; new `postgres_knowledge_base` adapter plus migration `008` (KB tables, draft vocabulary + pause-state extensions); add dispatcher branches in `agent_wu14.py` for `transition_lead` and `handoff_to_human` and an auto-finalize flow reusing the existing decision/finalize commands; swap `OpenCodeGoProvider` internals to pinned `langchain-openai` and express answer assembly as a LangChain chain; persist auto replies as outbound `messages`; the simulator and router gain the auto path; the UI gains the inbox handoff/paused affordances.

## Acceptance

- [ ] A policy-allowed message (FAQ, price, search, classification) executes end-to-end with **zero human clicks**: the draft records an auto decision attributed to the policy, every action carries its own idempotency key/audit/outbox record, and the reply is persisted as `direction='outbound'` and visible in the dashboard inbox.
- [ ] A low-confidence or handoff-triggering message executes nothing automatically: the conversation is flagged for the advisor, a follow-up exists, and further inbound messages receive no auto reply until the pause is cleared manually.
- [ ] FAQ answers come only from seeded KB rows (tenant-scoped, deterministic keyword retrieval; a harness oracle over the seeded corpus matches them); an unmatched question never yields a fabricated answer and escalates; no embeddings or vector code exist anywhere in the unit.
- [ ] Price answers read live catalog fields: editing the property's price changes the next answer; "¿cuánto sale?" resolves to the last discussed property within the unit's documented fallback.
- [ ] `transition_lead` is emitted by the planner for qualifying extracted signals and dispatched through the accepted `TransitionLeadStageCommand`; a replayed analyze produces no duplicate effects.
- [ ] The stub provider (no `OPENCODE_GO_API_KEY`) runs the full demo offline; `import langchain` is unreachable from the stub path; the LangChain-backed provider still returns schema-validated interpretations only (fail-closed on anything outside the bounded schema).
- [ ] Auto approval is still a saga: a mid-sequence failure records exactly what ran in `execution_result`; re-running is safe; `proposed → executed` (auto) and the human path share one monotonic lifecycle.
- [ ] All reads/mutations stay server-derived tenant-scoped; the composition mounts remain additive.
- [ ] Regression: `compileall`, WU3–WU14 harnesses (WU14 lifecycle expectations updated intentionally), `typecheck`, `npm test` green on new disposable base `production_runtime_mvp_wu15` (runner 001–008 + seeds); earlier bases untouched; `work-unit-15-evidence.md` records commands and exit codes.

## Review Workload Forecast

Estimated authored implementation: 1,300–1,600 lines (KB migration + adapter ~150, chain/policy layer ~250, planner/dispatcher extensions + handoff ~250, router/simulator glue ~120, contracts ~120, UI handoff/inbox ~150, harness ~350). 800-line budget risk: High (well above 400 changed lines per PR slice). Chained PRs recommended: Yes. Decision needed before apply: No (owner decisions are settled; remaining choices are design-phase). Delivery: auto-chain, feature-branch-chain; single reviewable unit (`agent-autonomy`) atop the accepted chain.

## Risks and Rollback

Auto-execution turns model errors into real effects: mitigated by the policy whitelist (only low-blast-radius actions are auto; anything uncertain pauses), fail-closed schema validation upstream of the policy, per-action idempotency + audit/outbox on the auto path, and the pause/escalate state as the manual kill switch. LangChain upgrades the dependency surface: mitigated by keeping the port boundary intact, pinning versions, and keeping the offline stub free of it so demo regressions fail loudly, not silently. KB keyword retrieval misses paraphrases: by design a miss is low confidence → human, never fabrication. Conversation-state ambiguity ("¿cuánto sale?" with no prior property) falls back to search/clarification, never to a guessed property. Superseding an accepted requirement risks silent scope drift: it is declared above as a Modified Capability with the audit spine intact. Rollback: delete the WU15 modules/UI/contract slice, drop the router mount, run migration `008` down; accepted WU5–WU14 surfaces and prior data untouched by construction.

## Open Decisions

Confirm only at review: (1) whether ANY action type (e.g. creating a calendar event) should keep a human gate even when the policy would allow it; (2) the exact auto-decision attribution in `agent_drafts` (`decided_by` is a uuid today — marker shape); (3) whether the auto path triggers on every simulator inbound or only when a per-conversation demo "auto mode" is on — both deferred to design.
