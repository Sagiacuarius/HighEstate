# Work Unit: Agent Autonomy (automatic agent + knowledge base + human handoff)

## Context

The skeleton is complete through the conversational agent (change:
`llm-conversational-agent`): catalog (WU11), atomic commands (WU5), inbound +
simulator (WU4/WU9), reads (WU6), dashboard (WU8), audit (WU12), calendar
(WU13) and the draft-based agent (WU14). The demo problem: nothing reaches the
lead until an advisor approves each draft. The product decision (owner,
2026-09-16) is that the agent must be **automatic** for a demo lead — no
per-message approval gate; the human advisor is the **escalation target**, not
a mandatory reviewer. Two gaps make that impossible today: the agent has no
knowledge to answer FAQ/pricing questions, and `transition_lead` sits in
`PROPOSED_ACTION_TYPES` but is never emitted by the planner and has no
dispatcher branch (verified dead vocabulary).

## Objective

1. **Auto-execution policy** (`agent_policy` module): FAQ answer, price answer,
   property search/match and lead classification auto-execute; anything
   low-confidence or handoff-triggering persists but does not execute and
   escalates to the advisor. Existing approve/reject remain as a manual
   override surface, not the primary path.
2. **Seeded knowledge base**: new table (migration `008`) with curated FAQ
   articles for typical client<->inmobiliaria interactions (expensas,
   escritura/registro, garantia/fiadores, documentos para alquilar,
   financiacion/PH, amenities, requisitos, politicas de visita). Retrieval is
   deterministic SQL keyword/simple match — explicitly no embeddings/vector
   search in this unit.
3. **Pricing from the catalog**: price questions answered from the live
   `properties` query (current fields), never static text.
4. **Handoff as a first-class action** `handoff_to_human`: triggers on explicit
   request ("quiero hablar con un humano/asesor") and on complaint/objection
   signals (new intent + classification signal). Effect: conversation flagged
   for the advisor (visible in the inbox), follow-up created, auto-replies
   **paused** for that conversation; a human resumes manually.
5. **Wire `transition_lead` end-to-end**: extracted signals (explicit
   budget+timeline+city, etc.) deterministically map to stage updates via the
   accepted WU5 `TransitionLeadStageCommand`; the dispatcher gains the branch.
6. **LangChain integration** (owner-mandated) behind the unchanged
   `ModelProvider` port: real provider internals use `langchain-openai`
   `ChatOpenAI` against the OpenAI-compatible base URL; answer assembly as a
   LangChain chain; pinned versions in `api/requirements.txt`. The offline
   `StubModelProvider` must survive and cannot call LangChain — the demo must
   work with no API key.
7. **Outbound replies persisted** as `messages` rows with
   `direction='outbound'` — the column and CHECK already exist (migration 004)
   and the dashboard inbox already renders per-direction bubbles; no direction
   migration is needed.

## Design notes

- The draft row remains the durable audit record: the status path is extended
  with a policy decision marker (`decided_by='agent-policy'` style; exact
  values and CHECK-list extensions are design-phase — note that `decided_by`
  is today a `uuid` column in `agent_drafts`, so the marker shape needs the
  008 migration). Per-action idempotency + one audit + one outbox must hold on
  the auto path exactly as under approval today.
- Resolution of "cuanto sale?" against the last discussed property implies
  conversation-state context; this unit ships a minimal last-matched-property
  fallback, with LangGraph noted as the design-phase candidate for real
  conversational state.
- Knowledge-base articles are tenant-scoped (seeded per demo account): every
  existing read carries a `client_account_id` predicate, and a shared table
  would create the first cross-tenant read path in the codebase.
- Whether ANY action type keeps a human gate is a design-phase question.
- Dependency ordering: full custom-field updates on the lead depend on the
  planned Configuracion unit; this change classifies via existing stage
  transitions only. This unit takes the WU15-ish slot; Configuracion may slide.

## Out of scope

- No Kapso/WhatsApp real channel (demo outbound = persisted outbound message
  visible in the dashboard inbox).
- No RLS, productive auth, Docker, CI/CD.
- No embeddings/vector search (no pgvector in this unit).
- No undo UX for auto-actions (to be designed separately).
- No modification of accepted WU5–WU13 surfaces; no lead custom-field editing.

## Regression

- `typecheck`, `npm test`, `compileall`, harnesses WU3–WU14 without regression
  (the WU14 draft-lifecycle expectations are intentionally updated to the new
  primary path, not silently broken).
- Base demo nueva `production_runtime_mvp_wu15` (runner 001–008 + seeds).
- No declarar aceptación; crear `work-unit-15-evidence.md`.
