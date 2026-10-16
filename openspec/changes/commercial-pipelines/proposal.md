# Proposal: Commercial Pipelines (advisor-managed funnels and stages)

## Intent

Ship an advisor-managed "Embudo comercial" unit (a WU19-ish slot after the accepted `configuration` (WU17) and `agent-custom-field-writes` (WU18) surfaces) on top of the real `pipelines` + `pipeline_stages` DDL (`infra/migrations/004_commercial_crm.up.sql:43-72`). Today the funnel is read-only end to end: the only route is `GET /v1/demo/pipeline` (`api/composition_wu8_mvp.py:320`) served by `CrmReadService.pipeline` (`api/queries/crm_reads.py:107`) over `pipeline_views` (`api/adapters/postgres_crm_reads.py:163-183`, pipelines ordered by `created_at, id`, stages with lead counts ordered by `position, id`); the "Embudo" view (`web/app/page.tsx:1473-1498`, `ViewKey "pipeline"` at line 58, fetched via `demo-api.ts` `pipeline()` at line 224 + `parseCrmPipelineResponse` in `contracts/runtime-v1.ts:504`) renders kanban columns with zero create/edit controls; and no create/update/delete pipeline endpoint exists anywhere (verified by grep — only the GET route). Seeds own the only funnels (`mvp_seed.sql:119-124`: `Ventas Aurea` + `Nueva consulta` pos 0, `Alquileres Litoral` + `Visita agendada` pos 0; `mvp_seed_008.sql:123-125`: `Calificado` pos 1 per pipeline). This unit adds advisor CRUD over the existing tables — create/rename pipeline, add/rename/reorder/delete stage — all through the accepted command discipline (idempotency + audit + outbox + tenant scope, never raw SQL from the route). Install is zero behavior change: no seed changes to existing demo pipelines, no accepted-surface edits beyond consumption.

## Scope

### In Scope

- **Pipeline commands** (new `api/application/pipeline_commands_wu19.py` on the `CrmCommandBase` template, `crm_commands_wu5.py:87` — reserve idempotency → scoped write → audit + outbox → commit): `pipeline.create` (name, non-blank per the DDL CHECK), `pipeline.rename` (name only; no lead/stage movement), `pipeline_stage.create` (name + position; append-at-end or insert-at-position with sibling renumbering in one transaction), `pipeline_stage.rename` (with the agent-name guard below), `pipeline_stage.reorder` (up/down buttons first; `position` renumbering honoring `UNIQUE (pipeline_id, position)`), `pipeline_stage.delete` (fail-closed BLOCK when leads reference the stage; denial audit, no FK surprise).
- **Reads**: the accepted `pipeline_views` read is consumed unchanged (it already orders by `position, id`, so reorders render with no read change); a `lead_count`-per-stage pre-check powers the delete denial message.
- **Routes**: new write routes mounted additively beside the accepted `GET /v1/demo/pipeline` (same mount precedent as the simulator/console routers in `composition_wu8_mvp.py:399-415`); forged/ghost/foreign ids non-enumerating on every new route, following the sibling pattern.
- **Agent-name guard**: `pipeline_stage.rename` validates the new name against the agent-known names (`QUALIFY_STAGE_NAMES = ("Calificado", "Simulador: visita calificada")`, `VISIT_STAGE_NAME = "Visita agendada"`, `conversation_agent.py:56-58`, resolved per-lead-pipeline by `_stage_id_for_name`, `agent_wu14.py:500-508`); renaming away an agent-known name surfaces a warning and the agent path degrades to no-`transition_lead` (omits the transition and falls back to search-match, never a guessed id — the existing unresolvable-name behavior pinned by `test_conversation_agent.py` U14). `TransitionLeadStageCommand` itself (`crm_commands_wu5.py:456-514`, resolves stages by id and validates same-pipeline) is untouched.
- **Embudo view extension**: create/rename pipeline controls plus per-stage add/rename/up/down/delete controls in the existing "Embudo" section (`page.tsx:1473`), reusing `panel`/`pipeline`/`pipeline-col` classes (`demo.css:262-270`); strict `runtime-v1.ts` parsers + typed `demo-api.ts` methods mirroring the `parseCrmPipelineResponse` / `RuntimeHttpClient` patterns.
- **Harness**: RECOMMEND a NEW `api/test_commercial_pipelines_wu19.py` on a new disposable base `production_runtime_mvp_wu19` (runner 001–011 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_008.sql` + `mvp_seed_009.sql` + `mvp_seed_010.sql` + `mvp_seed_011.sql`), rather than extending any accepted harness — keeps every accepted harness byte-stable. Scenarios below.

### Out of Scope / No-goals

- No lead movement UX (moving leads stays on the accepted `lead.transition` path; delete-with-leads is BLOCK, not move — see Approach).
- No drag-and-drop reorder this unit (up/down buttons first; DnD later — see Open Decisions).
- No entry-stage requirement and no won/lost stage semantics: `leads.status` (`new/qualified/won/lost`) already carries outcome and `_first_stage` takes the lowest-position stage (`crm_commands_wu5.py:428-442`); no code assumes reserved stages, so none are introduced.
- No seed changes to existing demo pipelines/stages; no new tables; no real channel; no RLS, productive auth, Docker, CI/CD.
- No modification of accepted WU5–WU18 surfaces beyond consumption (the `transition_lead` dispatch, `_stage_id_for_name`, and `pipeline_views` stay byte-identical).

## Capabilities

### New Capabilities

- `commercial-pipelines`: per-account pipeline + stage lifecycle (create/rename pipeline; add/rename/reorder/delete stage with fail-closed delete) behind the accepted command/audit/outbox discipline, with the agent-name rename guard and position-ordering semantics.

### Modified Capabilities

- None accepted (declared honestly): the Embudo read (`pipeline_views`), the agent's by-name stage resolution, and `TransitionLeadStageCommand` are consumed, not touched. The Embudo view gains management controls; the pipeline GET response shape is unchanged.

## Approach

No migration with new tables: the unit reuses `pipelines` (`id PK`, `client_account_id FK RESTRICT`, `demo_seed_key`, `name CHECK non-blank`, `UNIQUE (id, client_account_id)`) and `pipeline_stages` (`position integer >= 0`, `UNIQUE (pipeline_id, position)`, `UNIQUE (pipeline_id, name)`, composite FK `(pipeline_id, client_account_id) REFERENCES pipelines … ON DELETE RESTRICT`) exactly as declared in `004_commercial_crm.up.sql:43-72`. (A migration file exists only if the team wants a marker convention for advisor rows — `demo_seed_key` NULL already distinguishes them, so none is expected; settled at design.) Commands in `api/application/pipeline_commands_wu19.py`: each validates pure rules first (non-blank name, position bounds, same-pipeline scope), locks the owning pipeline row (`FOR UPDATE`), applies the write, then the template's single-commit audit + outbox. Delete pre-counts `leads` on the stage and raises the scoped denial (`stage_has_leads`) before the `ON DELETE RESTRICT` FK could fire; rename checks `UNIQUE (pipeline_id, name)` collisions fail-closed and warns when an agent-known name disappears. Seed interplay: user rows carry `demo_seed_key` NULL while demo rows carry `demo-a/b-*` markers; `mvp_reset.sql:28-32` deletes strictly by `(client_account_id, demo_seed_key)` pairs after the account-ownership gate (`mvp_reset.sql:9-17`), so reset/preclean never touch user rows by construction — the harness preclean must identify the NULL-marked simulator stage (`Simulador: visita calificada`, `simulator_wu9.py:99-120`) by name/position, never by blanket NULL-marker deletion. New routes mount additively; TS mirrors in `contracts/runtime-v1.ts` (strict `isRecord`-guarded parsers + fixtures) + typed `demo-api.ts` methods; `page.tsx` adds the management controls inside the existing Embudo section.

## Acceptance

- [ ] Zero behavior change at install: no seed edits; existing demo pipelines/stages byte-identical; `pipeline_views` output unchanged; all accepted harnesses green.
- [ ] Pipeline round-trip: create pipeline with initial stages → rename pipeline (leads/stages unmoved) → per-account isolation (cross-account invisible, forged ids non-enumerating).
- [ ] Stage lifecycle: add at end and at position (siblings renumbered, `UNIQUE (pipeline_id, position)` holds); rename (duplicate name in same pipeline rejected; agent-known-name rename warns); reorder up/down reflected in the kanban order.
- [ ] Delete fail-closed: deleting a stage with leads is denied with a scoped denial audit and zero effects; deleting an empty stage succeeds; pipeline delete with stages/leads denied via the RESTRICT chain (or command pre-check — settled at design).
- [ ] Agent safety: renaming away `Calificado` (or the visit stage) makes the agent skip `transition_lead` for affected leads (omits, never a guessed id); restoring the name restores transitions; `execute_transition` by id still succeeds throughout.
- [ ] Seed/reset interplay: reset after advisor edits removes only marker-owned demo rows; user pipelines/stages survive; the NULL-marked simulator stage is handled by name/position scoping, not marker deletion.
- [ ] Idempotency: replayed create/rename/reorder/delete keys return the canonical stored result with zero extra effects; stub-offline runs the full harness with no API key.
- [ ] Full regression: WU14–WU18 harnesses (and earlier) stay green; `test_commercial_pipelines_wu19.py` passes on its own base.

## Review Workload Forecast

| Slice | Estimate |
|---|---|
| Pipeline commands (`pipeline_commands_wu19.py`: 6 ops + agent-name guard + delete pre-check) | ~220 |
| Write routes (create/rename/reorder/delete, non-enumerating denials) | ~120 |
| Contracts + typed demo-api client | ~80 |
| Embudo view management controls (pipeline + per-stage, up/down) | ~180 |
| Harness `test_commercial_pipelines_wu19` | ~200 |
| **Total** | **~500–800, Moderate, chained PRs maybe** |

Decision needed before apply: No (owner requirements in `brief.md` are settled; the three Open Decisions are design-phase).

## Risks and Rollback

Fail-closed delete racing lead creation: mitigated — the delete holds the pipeline lock and pre-counts leads inside the same transaction as the delete, so a concurrent lead insert either lands first (delete denies) or second (fails on the stage check); the `ON DELETE RESTRICT` FK backstops either way. Agent name-resolution breakage on rename: mitigated by the rename-time warning against `QUALIFY_STAGE_NAMES`/`VISIT_STAGE_NAME` plus the agent's existing no-guess behavior on unresolvable names (U14-pinned); no silent misrouting by construction. Position UNIQUE collisions on concurrent reorders: mitigated by the single-transaction renumber under the pipeline lock. Simulator-stage NULL-marker confusion in preclean: mitigated by name/position scoping (never blanket NULL-marker deletion). Rollback: no new tables by design — remove the write routes, `pipeline_commands_wu19.py`, the contract/UI additions, and the new harness; accepted WU5–WU18 surfaces and all existing data untouched by construction.

## Open Decisions — resolved at review (owner, 2026-10-14)

1. **Delete-with-leads: BLOCK**. RESOLVED — deleting a stage with leads fails closed with denial; the advisor moves leads first.
2. **Rename: warn-and-keep-by-name**. RESOLVED — renames validated against agent-known names with a UI warning; if the target disappears the agent omits \	ransition_lead\ (falls back to search-match, never guesses).
3. **Reorder: up/down buttons now, drag-and-drop later**. RESOLVED.
