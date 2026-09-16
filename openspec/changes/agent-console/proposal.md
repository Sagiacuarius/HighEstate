# Proposal: Agent Console

## Intent

Ship a management console ("Agente" sidebar item) over the accepted `agent-autonomy` engine. The engine's levers are fixed in code or seed SQL today — policy in `api/application/agent_policy.py` constants, prompt in `api/application/agent_persona.py`, knowledge base as seed-only rows, calendar on the hardcoded WU13 scheduling constants, classification as stage-name transitions — and the owner decided (2026-09-16, `openspec/changes/agent-console/brief.md`) that administering them must be a managed console, not dev-only surfaces. Persisted per-account settings replace the code constants, the system prompt becomes versioned editable text under an immutable safety skeleton, the KB gains full CRUD plus a retrieval self-test, calendar binding configures the agent's scheduling destination, and lead updates move behind a per-field allowlist. Zero behavior change at install: default seeds equal the current code constants.

## Scope

### In Scope

- **Agent settings** (`agent_settings` table, migration `009`): per-action-type policy matrix (`AUTO`/`ESCALATE` per action type, seeded equal to `AUTO_ACTION_TYPES` / `GATED_ACTION_TYPES` in `api/application/agent_policy.py`), global auto switch plus default-conversation auto (replacing the `AUTO_EVENT_CREATION = True` constant and the `conversations.agent_mode` dev-only toggle as the managed surface), and a calendar kill-switch (the managed form of `AUTO_EVENT_CREATION`). Seeded defaults are byte-equal in effect to the current constants; the policy module reads settings with a pure-code fallback.
- **Prompt authoring** (`agent_prompts` versioned table + audit): the safety skeleton (never-invent-facts, handoff-on-complaint, schema boundaries — the content of `AGENT_GUARDRAILS` and the `_SYSTEM_TEMPLATE` JSON-shape rules in `api/application/agent_persona.py`, composed by `build_system_prompt()`) is immutable and enforced server-side, never by honor; the editable layer is tone/instructions (`AGENT_TONE`-class fields plus custom instructions, composed via `build_answer_prompt(article_or_price_context)`); every save creates a new version with audit, and the UI shows a composed-final preview.
- **Knowledge-base management**: full CRUD over `knowledge_base_articles` (`id, client_account_id, demo_seed_key, category, question, keywords text[], answer, created_at, updated_at`; `category CHECK IN ('expensas','escritura','garantia','documentos','financiacion','amenities','requisitos','visitas')` per `008_agent_autonomy.up.sql`), replacing the seed-SQL dependency for KB maintenance, plus a "test retrieval" probe endpoint that reuses the accepted `KnowledgeBaseRepository.find_best_match` / `KB_SCORING_SQL` adapter (`api/adapters/postgres_knowledge_base.py`) with its `normalize_tokens` scoring.
- **Calendar binding (recommended interpretation: per-account scheduling configuration)**: business-hours window, slot length, working days, and the destination calendar/account the agent books into — today hardcoded as `BUSINESS_TIMEZONE = "America/Argentina/Buenos_Aires"`, `BUSINESS_DAYS = (0,1,2,3,4,5)`, `BUSINESS_START_HOUR = 9`, `BUSINESS_END_HOUR = 18`, `SLOT_MINUTES = 60` (`api/domain/calendar_event.py`, consumed by `free_slots` in `api/adapters/postgres_agent_drafts.py`). External sync (Google Calendar) is explicitly deferred until `external_id` exists.
- **Lead-field allowlist** (`agent_lead_field_allowlist` table): per-field allowlist over the real `leads` columns verified in `004_commercial_crm.up.sql` (`stage_id`, `status`, `selected_property_id` are the only agent-writable candidates; `contact_id`/`pipeline_id` are identity, `source`/`created_at`/`updated_at` are system-owned). The initial seed allows `stage_id` + `status` only — exactly what `TransitionLeadStageCommand.execute_transition` (`api/application/crm_commands_wu5.py:463`) writes. Custom fields are deferred to the Configuración unit; the allowlist UI shows only fields that exist.
- **Managed state/resume surface**: the dev-only `GET`/`POST /v1/demo/agent/state` (`AGENT_STATE_ROUTE`) and `POST /v1/demo/agent/handoff/resume` (`AGENT_RESUME_ROUTE`) in `api/routes/agent_wu14.py` are promoted to console-owned routes with the same shapes; the canonical-slot derivation (`CANONICAL_ACCOUNT_SLOT`) stays.
- **Console view**: new `"agente"` entry in `ViewKey`/`VIEW_LABELS` (`web/app/page.tsx`, today `resumen | inbox | leads | properties | pipeline | agenda | activity | auditoria | calendario`) with five sections mirroring the In-Scope bullets; conversation/inbox thread and `agent-draft-card` structures are reused, not rebuilt.
- **Harness**: `api/test_agent_console_wu16.py` on new disposable base `production_runtime_mvp_wu16` (runner 001–009 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_008.sql` + console defaults seed), following the README harness pattern (`python -m api.test_*` against a disposable `production_runtime_mvp_wuNN` base).

### Out of Scope / No-goals

- No real channel (Kapso/WhatsApp): console manages the demo agent only.
- No RLS, productive auth, Docker, CI/CD.
- No embeddings/vector search; no pgvector; no change to the `KB_SCORING_SQL` retrieval algorithm.
- No skeleton/guardrail editing via any surface (immutable by design, not a deferred feature).
- No Google/external calendar sync; no `external_id` plumbing.
- No lead custom fields (Configuración unit owns them); no modification of accepted WU5–WU15 surfaces beyond consumption.
- No multi-turn conversational memory beyond the accepted last-interest fallback.

## Capabilities

### New Capabilities

- `agent-console`: persisted per-account agent settings, versioned prompt authoring with immutable skeleton, KB CRUD with retrieval self-test, scheduling-destination configuration, lead-field allowlist administration, all behind the accepted command/audit/outbox discipline.

### Modified Capabilities

- `agent-autonomy` (declared honestly): the `agent_policy` constants (`AUTO_ACTION_TYPES`, `GATED_ACTION_TYPES`, `ESCALATION_MECHANICS`, `AUTO_EVENT_CREATION`) become database-backed defaults — the module keeps its pure signature with a code-constant fallback, but the effective verdict reads settings. The dev-only state/resume endpoints (`AGENT_STATE_ROUTE`, `AGENT_RESUME_ROUTE`) become managed console routes; `agent_mode` default remains `manual` on non-demo conversations and `auto` on the two seeded demo conversations unless the console changes it. Composition seed wiring: the dashboard reset path (`composition_wu8_mvp.py`: reset runs only `RESET_FILE` + `SEED_FILE`, so 008 rows are dropped on reset) is addressed by folding the console's default seeds into the managed seed path alongside `mvp_seed_008.sql` — reset behavior for 008-era rows is unchanged otherwise. What stays: the policy evaluation order, the grounded-reply rule, the handoff mechanics, and every deterministic layer remain code.

## Approach

Migration `009_agent_console` (next free number after `008_agent_autonomy`): `agent_settings` (per-account policy matrix JSON + global auto + calendar kill-switch), `agent_prompts` (versioned rows: immutable skeleton hash + editable tone/instructions + composed-final snapshot), `agent_lead_field_allowlist` (per-account field rows over verified `leads` columns), plus a scheduling-config row set (window/slot/days/destination). Down file mirrors the 005/006/007 convention. Read/write commands mirror the accepted `CrmCommandBase` (`api/application/crm_commands_wu5.py:87`): reserve idempotency → scoped write → audit + outbox. TS contract mirrors in `contracts/runtime-v1.ts` + typed `demo-api.ts` methods; new `"agente"` view + five sections in `web/app/page.tsx` mounted additively next to the existing agent router include (`composition_wu8_mvp.py:365` stays byte-identical); harness `api/test_agent_console_wu16.py` on `production_runtime_mvp_wu16`.

## Acceptance

- [ ] Zero behavior change at install: with console defaults seeded, the policy verdict table matches the code-constant oracle for the full scenario corpus (`AUTO_EVENT_CREATION = True` semantics preserved).
- [ ] Settings round-trip: policy matrix + global auto + kill-switch persist per account, are tenant-scoped, and flipping the kill-switch re-gates calendar writes to ESCALATE.
- [ ] Prompt versioning: saves create new versions with audit; an attempt to edit the skeleton fails closed server-side (probe asserts the composed final keeps the skeleton text byte-identical).
- [ ] KB CRUD + self-test: create/edit/delete round-trips over `knowledge_base_articles`; the probe endpoint returns the expected article for a seeded question and no-match for an unrelated one.
- [ ] Calendar config changes the agent's booking window in the harness (edited hours/days alter `free_slots` outcomes for the account).
- [ ] Allowlist enforcement: a disallowed field write is denied; `stage_id` + `status` transition via `execute_transition` succeeds when allowed.
- [ ] Forged/ghost ids non-enumerating across all console routes; stub-offline still runs the full harness with no API key.

## Review Workload Forecast

| Slice | Estimate |
|---|---|
| Migration 009 + adapters (settings/prompts/allowlist/scheduling) | ~180 |
| Settings/prompts/allowlist commands (CrmCommandBase pattern) | ~220 |
| Console routes (settings/prompts/KB/probe/calendar/allowlist/state) | ~150 |
| Contracts + typed demo-api client | ~120 |
| "Agente" view + five sections | ~300 |
| Harness `test_agent_console_wu16` | ~320 |
| **Total** | **~1,100–1,400, High, chained PRs Yes** |

Decision needed before apply: No (owner requirements in `brief.md` are settled; remaining choices are design-phase).

## Risks and Rollback

Prompt edits weakening guardrails: mitigated by the immutable skeleton + server-side enforcement (never client-honor) + full version history with revert. Settings misconfiguration breaking auto behavior: mitigated by defaults equal to code constants, per-conversation override above the global switch, and the calendar kill-switch. KB CRUD quality regressions: mitigated by the self-test probe reusing the accepted adapter plus intact seeds. Allowlist overreach: mitigated by seeding only `stage_id` + `status` and validating against real DDL. Rollback: drop `009` tables + console router slice + "Agente" view; accepted WU5–WU15 surfaces and prior data untouched by construction.

## Open Decisions — resolved at review (owner, 2026-09-16)

1. **Calendar binding = per-account scheduling-destination configuration** (window/slot/days/destination calendar the agent books into). External calendar sync (Google) stays deferred.
2. **Skeleton edits are never allowed via UI** — safety skeleton changes only in code with deploy + review; the editable tone/instructions layer keeps version history + audit.
3. **The console absorbs the seed wiring** — folding the 008 + console default seeds into the managed composition reset path, so dashboard reset no longer drops KB articles or the auto flip (fixes WU15-evidence D3).
