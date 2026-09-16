# Design: Agent Console

## Technical Approach

Additive management console over the accepted WU15 engine, never editing accepted seams beyond two declared, default-preserving read edits. Today's levers are fixed: policy in `api/application/agent_policy.py` constants (`AUTO_ACTION_TYPES`, `GATED_ACTION_TYPES`, `ESCALATION_MECHANICS`, `AUTO_EVENT_CREATION = True`), voice in `api/application/agent_persona.py` module constants, KB as seed-only rows, scheduling on the WU13 module constants (`BUSINESS_TIMEZONE`, `BUSINESS_DAYS`, `BUSINESS_START_HOUR`, `BUSINESS_END_HOUR`, `SLOT_MINUTES` in `api/domain/calendar_event.py`), lead writes on the hardcoded `TransitionLeadStageCommand.execute_transition` path. Migration `009_agent_console` persists per-account equivalents of each lever with seeds byte-equal in effect to the code constants, so install is zero behavior change: every consumer reads persisted settings with a pure-code fallback when a row is missing, keeping old bases working. All writes go through new `CrmCommandBase` subclasses (`api/application/crm_commands_wu5.py:87` template: reserve idempotency → scoped write → audit + outbox → commit); all reads carry server-derived `client_account_id` first. The three resolved owner decisions (2026-09-16) are baked in: calendar binding = per-account scheduling-destination configuration (1a, external sync deferred); the safety skeleton is never editable via any UI (2a, server-enforced); the console absorbs the seed wiring, folding 008 + console defaults into the managed composition reset path (3a, fixing the WU15-evidence D3 gap where reset runs only `RESET_FILE` + `SEED_FILE` in `composition_wu8_mvp.py:340-346` and drops 008 rows). Mounted as additive routes on the existing `build_agent_router` — the `composition_wu8_mvp.py:365` include line stays byte-identical.

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Settings shape: rows, not JSONB** | `agent_settings` (one row per account: `global_auto`, `calendar_auto_enabled`) + `agent_policy_matrix` (one row per account × action type, `verdict ∈ {auto,escalate}`, PK `(client_account_id, action_type)`) | A matrix row is constraint-enforced (`CHECK` over the 7 real action types) and queryable; a JSONB blob would hide typos in action names until dispatch time. The verdict table renders directly in the UI editor. `default_agent_mode` as a stored column is explicitly REJECTED: the column default `'manual'` on `conversations.agent_mode` stays (accepted seam), non-demo conversations stay `manual`, the two demo conversations stay `auto` via `mvp_seed_008.sql`, and per-conversation mode is the managed surface via the promoted state routes — no dead config. |
| **Global auto semantics** | `global_auto BOOLEAN NOT NULL DEFAULT TRUE` is the master switch; `calendar_auto_enabled BOOLEAN NOT NULL DEFAULT TRUE` is the managed `AUTO_EVENT_CREATION` | Seeded `TRUE/TRUE` reproduces today's behavior exactly. `global_auto=FALSE` skips auto dispatch (draft still records `policy_decision`); `calendar_auto_enabled=FALSE` reproduces the `calendar_gated_kill_switch` escalation with the same reason code. Per-conversation `agent_mode` stays the finer switch; precedence is `global_auto ∧ mode=auto ∧ verdict=auto`. |
| **Prompt versioning** | `agent_prompts`: `version INT ≥ 1`, `tone TEXT`, `instructions TEXT DEFAULT ''`, `composed_snapshot TEXT`, `created_by/at`, `is_active BOOL`, `UNIQUE (client_account_id, version)` + partial unique `WHERE is_active` (one active per account). Save = INSERT new version + flip active in one locked transaction; revert = INSERT copying target content, never UPDATE | History is append-only like audit; revert is itself auditable. Seed v1 per demo account = `tone = AGENT_TONE`, `instructions = ''`, so the composed output is byte-identical to `build_system_prompt()` today (harness asserts this oracle). `composed_snapshot` freezes what the model saw, independent of later code edits. |
| **Skeleton immutability (2a)** | Skeleton = `AGENT_IDENTITY`, `AGENT_ROLE`, `AGENT_LANGUAGE`, `AGENT_GUARDRAILS`, `_SYSTEM_TEMPLATE` structure. Enforced in the write command: any payload key outside `{tone, instructions}` fails closed 400; the composer signature `build_system_prompt(*, tone=AGENT_TONE, instructions="")` has no skeleton parameter | Client honor is not a boundary. The probe asserts the composed final keeps the skeleton text byte-identical; prompt edits can only weaken tone, never remove never-invent-facts / handoff-on-complaint / defer-on-uncertainty. |
| **KB deactivation seam** | `009` adds `knowledge_base_articles.deactivated_at TIMESTAMPTZ NULL`; `KnowledgeBaseRepository.find_best_match` gains `AND a.deactivated_at IS NULL` (declared accepted-read edit #2 of 2; default NULL = visible → zero behavior change) | The 008 DDL has no status column, so soft-deactivate needs one home. Hard DELETE would be resurrected for seed-designated markers by the 008 DELETE-then-INSERT converge on explicit reseed; `deactivated_at` survives reseed and keeps retrieval honest. Console rows carry `demo_seed_key NULL` (account-owned, never claimed by seeds). |
| **KB self-test probe** | `POST /v1/demo/agent/kb/self-test {question}` reuses `KnowledgeBaseRepository.find_best_match(context, question)` + `normalize_tokens` overlap count for display; returns the matched article id/category/question or an explicit `{match:false}` | Same adapter, same scoring (`KB_SCORING_SQL`), never a fabricated answer: a miss is escalate-by-design, surfaced truthfully in the UI ("escaladoría"). Category/input validation reuses the 8-value CHECK domain. |
| **Calendar seam (1a, declared accepted-read edit #1 of 2)** | `business_slot_starts(day, *, tz_name=…, start_hour=…, end_hour=…, slot_minutes=…, working_days=…)` with defaults equal to the module constants; `free_slots(…, calendar=None)` uses the passed config, else constants. Per-account `agent_calendar_config` row (window/slot/days + `destination_label` label). The WU13 availability service reads the same row, so UI availability and agent booking agree | The constants are module-level, and `free_slots` additionally hardcodes `step = timedelta(minutes=60)` (`postgres_agent_drafts.py:141`, not `SLOT_MINUTES`) — there is no injection point today, so honest additive kwargs are the minimal seam. `destination_name` is a label only: events stay in the tenant-scoped `calendar_events` table (no schema change; external sync deferred until `external_id` exists). |
| **Allowlist enforcement** | `agent_field_allowlist`: UNIQUE `(client_account_id, column_name)`, `column_name CHECK IN ('stage_id','status','selected_property_id')` verified against the leads DDL; presence = allowed. Seed: `stage_id` + `status` per demo account only. Checked at dispatch time in the `transition_lead` branch (and any future field write); disallowed → `outcome=denied, reason=lead_field_not_allowed` recorded truthfully in `execution_result`, draft still finalizes (overlap-denial precedent) | `TransitionLeadStageCommand._apply` writes `stage_id` AND `status` together (`crm_commands_wu5.py:494-498`), so the branch requires BOTH allowed — gating only `stage_id` would be a false sense of control. `selected_property_id` is in the CHECK domain but unseeded (denied until the console allows it). Fail closed, never crash the saga. |
| **Seed wiring (3a / D3 fix)** | `composition_wu8_mvp.py` gains `MANAGED_SEED_FILES = [SEED_FILE, SEED_008_FILE, SEED_009_FILE]`; the seed route runs it, reset runs `[RESET_FILE] + MANAGED_SEED_FILES`. New `infra/migrations/validation/mvp_seed_009_console.sql` mirrors the 008 layout (advisory lock, ownership gate, DELETE-then-INSERT of owned markers). `mvp_seed_005.sql` stays harness-only (unchanged); harness `SEED_FILES` stay independent | Dashboard reset no longer drops KB articles, the `auto` flip, or console defaults. Disposable-base semantics preserved: each harness still builds its own base via runner + explicit seed list. The agent-router include line itself is untouched. |
| **Router choice** | Extend `build_agent_router(conn_kwargs, model_provider)` additively; no second router, no second mount | One agent surface, one mount (`composition_wu8_mvp.py:365` byte-identical), shared `_context`/`_denial`/correlation handling. Console routes are agent-owned the way state/resume already are. |
| **Settings read path** | Per-request load in analyze (`load_console_settings` adapter, single SELECTs, no cache — WU13 "correctness > latency" precedent); missing rows → code constants (`AUTO_ACTION_TYPES`, `GATED_ACTION_TYPES`, `AUTO_EVENT_CREATION`) | No invalidation bugs, no stale verdicts; old bases without 009 rows behave exactly as today. `evaluate()` gains keyword-only overrides defaulting to the constants (pure signature preserved, unit oracle pins defaults). |
| **Audit** | Console commands write `audit_records` + `outbox_events` via the accepted `PostgresAuditOutboxRepository` (`agent.settings.update`, `agent.prompt.version`, `agent.kb.*`, `agent.calendar.update`, `agent.allowlist.update`); prompt saves additionally persist `composed_snapshot` on the version row | Same durable/tenant-scoped discipline as 004–007; prompt history is queryable without joining audit. |

Seeded policy matrix (per demo account — equals the OD-1 default verdict table, zero behavior change):

| action_type | verdict | Source |
|---|---|---|
| `send_reply`, `register_interest`, `create_follow_up`, `transition_lead` | `auto` | `AUTO_ACTION_TYPES` minus gated |
| `create_event`, `reschedule_event` | `auto` | `AUTO_ACTION_TYPES` ∩ `GATED_ACTION_TYPES` (auto while `calendar_auto_enabled`, = `AUTO_EVENT_CREATION = True`) |
| `handoff_to_human` | `escalate` | `ESCALATION_MECHANICS` (mechanics-only, never a reply) |

## Contracts, Invariants, and Flow

New endpoints on the agent router (all under `/v1/demo`, all return `X-Correlation-Id` + `correlation_id`; unknown keys → 400; forged/foreign ids → 404 non-enumerating; strict enums via `runtime-v1.ts` parsers):

- `GET /agent/settings` → `{global_auto, calendar_auto_enabled, matrix:[{action_type, verdict}]}`; `PUT /agent/settings {global_auto?, calendar_auto_enabled?, matrix?}` (matrix entries validated against the 7 real types; verdicts `auto|escalate`).
- `GET /agent/prompts` (versions list) / `POST /agent/prompts {tone, instructions}` (skeleton keys rejected) / `GET /agent/prompts/active` / `GET /agent/prompts/:version` / `POST /agent/prompts/:version/revert` / `GET /agent/prompts/composed-preview` → `{version, composed_text}` (read-only, exact text the model would receive).
- `GET /agent/kb/articles?category?` / `POST /agent/kb/articles {category, question, keywords[], answer}` / `PUT /agent/kb/articles/:id` / `DELETE /agent/kb/articles/:id` (soft, sets `deactivated_at`) / `POST /agent/kb/self-test {question}` → `{match:true, article:{id, category, question}, hits} | {match:false}`.
- `GET /agent/calendar-config` / `PUT /agent/calendar-config {window_start, window_end, slot_minutes, working_days, destination_label}` (invalid windows fail closed 400).
- `GET /agent/lead-fields` / `PUT /agent/lead-fields {allowed:[…]}` (each member inside the CHECK domain, else 400).
- Promoted unchanged: `GET/POST /agent/state` (`AGENT_STATE_ROUTE`), `POST /agent/handoff/resume` (`AGENT_RESUME_ROUTE`) — same shapes (`AgentStateResponse`), canonical-slot semantics kept (`CANONICAL_ACCOUNT_SLOT`; note the web client already sends `?conversation_id=` which the server ignores — documented, not expanded).

Flow: `PUT /agent/settings` → `UpdateAgentSettingsCommand.execute_settings` (reserve → upsert matrix + settings → audit `agent.settings.update` + outbox → commit) → next `POST /agent/analyze` loads the overlay and dispatches under the new verdicts. Prompt save → `CreateAgentPromptVersionCommand` (reject skeleton keys → version = max+1 → flip `is_active` under per-account lock → audit + outbox). Analyze composes the system prompt from the active row (fallback: `build_system_prompt()` defaults); the grounded answer chain (`build_answer_prompt`) is untouched.

Invariants: one active prompt per account (partial unique index); version monotonic per account; `deactivated_at`-set articles invisible to retrieval but restorable; allowlist presence = allowed, absent = denied; every console write tenant-scoped + idempotent (`console:{scope}:{entity}:{key}` keys); `execution_mode='agent_policy'` attribution rules unchanged.

SQL shape (009 UP — next free number after 008; DOWN mirrors the 005/006/007 convention, reversing each part):

```sql
CREATE TABLE agent_settings (
    client_account_id uuid PRIMARY KEY REFERENCES client_accounts (id) ON DELETE RESTRICT,
    global_auto boolean NOT NULL DEFAULT TRUE,
    calendar_auto_enabled boolean NOT NULL DEFAULT TRUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE agent_policy_matrix (
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    action_type text NOT NULL CHECK (action_type IN
      ('send_reply','register_interest','create_follow_up','transition_lead',
       'create_event','reschedule_event','handoff_to_human')),
    verdict text NOT NULL CHECK (verdict IN ('auto','escalate')),
    PRIMARY KEY (client_account_id, action_type)
);
CREATE TABLE agent_prompts (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    version integer NOT NULL CHECK (version >= 1),
    tone text NOT NULL, instructions text NOT NULL DEFAULT '',
    composed_snapshot text NOT NULL,
    created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    is_active boolean NOT NULL DEFAULT FALSE,
    CONSTRAINT agent_prompts_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT agent_prompts_account_version_unique UNIQUE (client_account_id, version)
);
CREATE UNIQUE INDEX agent_prompts_one_active_idx
    ON agent_prompts (client_account_id) WHERE is_active;
CREATE TABLE agent_field_allowlist (
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    column_name text NOT NULL CHECK (column_name IN ('stage_id','status','selected_property_id')),
    UNIQUE (client_account_id, column_name)
);
CREATE TABLE agent_calendar_config (
    client_account_id uuid PRIMARY KEY REFERENCES client_accounts (id) ON DELETE RESTRICT,
    window_start text,
    window_end text,
    slot_minutes integer CHECK (slot_minutes > 0),
    working_days integer[],
    destination_label text
);
ALTER TABLE knowledge_base_articles ADD COLUMN deactivated_at timestamptz;
```

Settings overlay (adapter; per-request, fallback to constants):

```python
async def load_console_settings(context, runner):
    """(effective_auto: frozenset, calendar_allowed: bool, global_auto: bool).
    Missing rows -> (AUTO_ACTION_TYPES, AUTO_EVENT_CREATION, True)."""
```

## Files and Composition

- `infra/migrations/009_agent_console.up.sql` / `.down.sql` (above; DOWN drops the four tables and the `deactivated_at` column, restoring the 008 shape; precondition mirrors 008: disposable demo bases, reset first).
- `infra/migrations/validation/mvp_seed_009.sql` (009 defaults seed: settings TRUE/TRUE, 7-row matrix, prompt v1 = `AGENT_TONE`/`''`, allowlist `stage_id`+`status`, calendar config = `BUSINESS_*` constants; 008 seed layout: `\set ON_ERROR_STOP on`, transaction, `pg_advisory_xact_lock`, ownership gate, DELETE-then-INSERT of owned markers).
- `api/application/agent_policy.py` — `evaluate()` gains keyword-only `auto_types`/`calendar_allowed` defaulting to the constants (pure, oracle-pinned).
- `api/application/agent_persona.py` — `build_system_prompt(*, tone=AGENT_TONE, instructions="")` (additive kwargs; defaults byte-identical).
- `api/application/agent_commands_wu16.py` — `UpdateAgentSettingsCommand` (`agent.settings.update`), `CreateAgentPromptVersionCommand` (`agent.prompt.version`, skeleton-key rejection), `CreateKbArticleCommand` / `UpdateKbArticleCommand` / `DeactivateKbArticleCommand` (`agent.kb.*`), `UpdateCalendarConfigCommand` (`agent.calendar.update`), `UpdateLeadFieldAllowlistCommand` (`agent.allowlist.update`) — all `CrmCommandBase`.
- `api/adapters/postgres_agent_console.py` — `load_console_settings`, `load_active_prompt`, `load_calendar_config`, `load_allowlist` (account predicate first, `None`/empty → code defaults).
- `api/adapters/postgres_knowledge_base.py` — `AND a.deactivated_at IS NULL` in `KB_SCORING_SQL` (edit #2).
- `api/domain/calendar_event.py` + `api/adapters/postgres_agent_drafts.py` — calendar kwargs + config threading (edit #1; hardcoded 60-min step replaced by config value).
- `api/routes/agent_wu14.py` — console endpoints on `build_agent_router`; analyze consults settings/allowlist/active prompt; `_dispatch_actions` gains the allowlist check in the `transition_lead` branch.
- `api/composition_wu8_mvp.py` — `MANAGED_SEED_FILES` + seed/reset route use (router include untouched).
- `contracts/runtime-v1.ts` (+ test) — `AgentSettings`, `AgentPromptVersion`, `KbArticle`, `CalendarConfig`, `LeadFieldAllowlist` + strict parsers + fixtures; `web/app/demo-api.ts` — typed methods (`getSettings`/`updateSettings`, prompt CRUD + `composedPreview` + `revertPrompt`, KB CRUD + `kbSelfTest`, calendar config, lead fields) reusing the `RuntimeHttpClient` + `X-Correlation-Id` pattern; `web/app/page.tsx` — `ViewKey += "agente"`, `VIEW_LABELS.agente = "Agente"`, five sections (Configuración, Prompt, Base de conocimiento, Calendario, Campos del prospecto; Spanish labels, existing `panel`/`conversation`/`status-pill`/`agent-draft-card` classes from `demo.css`, inbox thread + handoff flag reused).
- Units: `api/test_agent_console_settings.py` (fallback == constants oracle), `api/test_agent_console_prompts.py` (skeleton-immutability probe, preview byte-equality, revert-inserts), `api/test_agent_console_kb.py` (CRUD + self-test hit/no-match), `api/test_agent_console_allowlist.py` (deny + dual-column gate), `api/test_agent_console_calendar.py` (override slot oracle vs pure twin).
- Harness: `api/test_agent_console_wu16.py` on NEW disposable base `production_runtime_mvp_wu16` (runner 001–009 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_008.sql` + `mvp_seed_009_console.sql`).

## Testing Strategy

Harness scenarios (StubModelProvider, offline; every effect via the accepted command surfaces, never hand-inserted): K1 settings round-trip per account + tenant isolation; kill-switch flip re-gates calendar writes to ESCALATE (`calendar_gated_kill_switch`); `global_auto=FALSE` suppresses auto dispatch while `policy_decision` still records. K2 fallback oracle: with console rows deleted, the verdict table matches the code-constant corpus exactly. K3 prompt versioning: saves create versions + audit; skeleton-key payload fails closed; composed preview of seeded v1 is byte-identical to `build_system_prompt()`; revert inserts a new version. K4 KB CRUD round-trip + probe returns the seeded article for a seeded question and `{match:false}` for an unrelated one; cross-account invisible; deactivated article no longer matches. K5 calendar override (e.g. 10–16h / 30-min / weekdays-only) alters `free_slots` outcomes and the UI availability identically; destination label round-trips. K6 allowlist: removing `status` denies `transition_lead` with `lead_field_not_allowed` in `execution_result`; seeded `stage_id`+`status` transition via `execute_transition` succeeds; unknown field rejected at write. K7 forged account / foreign / ghost ids non-enumerating on all console routes. K8 stub runs K1–K7 with no API key (`langchain_openai` never imported). K9 `test_agent_autonomy_wu15` pinned intact on its own base. K10 idempotent replays (settings/prompt/KB/calendar/allowlist) zero extra effects. UI: jsdom tests mirroring `web/app/agent-drafts.test.tsx` (stubbed fetch; matrix editor, prompt editor + preview + history, KB table + self-test box, calendar form, allowlist checklist; unknown-key and denial envelopes surface with correlation id). Regression sweep: `compileall`, typecheck, `npm test`, WU3–WU15 harnesses green.

## Risks and Rollback

Prompt edits weakening guardrails — mitigated by the immutable skeleton + server-side rejection (never client honor) + full append-only history with revert-as-insert + composed snapshots. Settings misconfiguration breaking auto behavior — mitigated by seeds equal to constants, `global_auto` master above per-conversation mode, and the calendar kill-switch; fallback keeps old bases alive. KB quality regressions — mitigated by the self-test probe on the accepted adapter plus intact seeds (deactivation reversible). Allowlist overreach — mitigated by seeding only `stage_id`+`status`, CHECK-bounding to real DDL columns, and the dual-column gate on transitions. Calendar misconfiguration (empty window) — 400-validated (`start_hour < end_hour`, bounded enums); an empty-but-valid window yields no slots, which escalates honestly rather than booking wrong. Rollback: run `009` down on a reset demo base + remove the console router slice, `agent_commands_wu16.py`, contract/UI additions, and the `MANAGED_SEED_FILES` line; accepted WU5–WU15 surfaces and prior data untouched by construction (the two accepted-read edits restore constant behavior by default).
