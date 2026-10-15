# Proposal: Agent Custom Field Writes (signal auto-fill)

## Intent

Ship the agent's optional, signal-driven auto-fill of custom field values on top of the accepted `configuration` (WU17) surface, and close the one piece of debt it deliberately left open (D4). WU17 delivered passive per-account custom fields — `custom_field_definitions` (name + `type CHECK IN ('text','number','select','date')` + `choices text[]` for `select`, `UNIQUE (client_account_id, name)`) and the EAV store `lead_custom_field_values` (one row per `lead_id × field_id`) in migration `010_configuration` — plus the already-correct value-write command `SetLeadCustomFieldValueCommand` (`configuration_commands_wu17.py`, operation `configuration.custom_field.value.set`, `_coerce_value` doing `number`/`date`/`select∈choices`/`text` validation). It also widened the allowlist at the schema level (`agent_field_allowlist.field_definition_id` + composite FK in `010`), so the read path `load_field_allowlist` (`postgres_agent_console.py:114`) already returns fixed columns ∪ custom names. But two things remain closed today: the allowlist WRITE path `UpdateFieldAllowlistCommand` (`agent_commands_wu16.py:691`, operation `agent.allowlist.update`) still validates against `ALLOWLIST_COLUMNS = frozenset({"stage_id","status","selected_property_id"})` (line 56) and its `_apply` inserts only `column_name` (no `field_definition_id`) — debt D4, so the advisor cannot authorize which custom fields the agent may touch; and `PROPOSED_ACTION_TYPES` (`agent_draft.py:23`) has no action type for writing custom field values, so today only the advisor (or future automations) can. This unit (a) makes the agent able to write a custom-field value through a NEW proposed action type `set_custom_field_value` that REUSES `SetLeadCustomFieldValueCommand` unchanged, (b) introduces an OPTIONAL, producer-scoped binding — a closed-catalog signal (`phone`, `email`, `presupuesto_max`, `barrio`, `tipo_propiedad`, `fecha_objetivo`, `medio_de_contacto`) → a custom field, configured in a NEW console section "Fuentes del agente" (never inside the field definition), and (c) closes D4 by widening the allowlist write path. The custom field stays passive (it never learns where its value comes from); the binding is per-producer because advisor (manual), agent (binding), and future automations are three independent producers of the same field. Install is zero behavior change: no bindings seeded ⇒ the agent writes no custom field.

## Scope

### In Scope

- **Signal catalog + deterministic extraction**: a closed, code-owned catalog of the seven owner-approved signals; each resolves deterministically (e.g. `presupuesto_max`↔criteria `budget_max`, `barrio`↔`city`, `tipo_propiedad`↔`property_type`, `fecha_objetivo`↔`preferred_date`, which are already `CRITERIA_KEYS` in `agent_draft.py:27`; `phone`/`email`/`medio_de_contacto` from the validated body). The model never free-writes a value — same grounding discipline as `send_reply` (KB/catalog-derived text only).
- **Binding table** (migration `011`, next free number after `010`): a producer-scoped `signal → field_definition_id` mapping per account (exact shape is Open Decision 3), with `ON DELETE` semantics that clear the binding when a referenced `custom_field_definitions` row is deleted.
- **New proposed action type `set_custom_field_value`**: added to `PROPOSED_ACTION_TYPES` (`agent_draft.py:23`), carrying `{field_id, value}` (resolved via the binding + deterministic extraction, never raw model text); `ConversationAgent._derive_actions` (`conversation_agent.py:306`) emits it only when a binding exists for that lead's account and the signal resolved to a value.
- **Dispatch reuse**: a new `_dispatch_actions` branch (`agent_wu14.py:336`) calls `SetLeadCustomFieldValueCommand.execute_set(context, lead_id=…, field_id=…, value=…, idempotency_key=key)` — the coercion + type validation + `select∈choices` stay in the existing command, no fork. Idempotency keys keep the `agent-draft:{draft_id}:{index}` form.
- **Policy verdict for the new action**: `set_custom_field_value` slots into `agent_policy.evaluate(...)` (`agent_policy.py:162`) as a lead-write, with the same allowlist gating as `transition_lead` (the field's name must be allowed for the account, mirroring `_TRANSITION_WRITES`); the migration `011` widens the `agent_policy_matrix.action_type` CHECK (today the 7 real types) to admit it. Default AUTO vs ESCALATE is Open Decision 1.
- **Allowlist write widening (closes D4)**: `UpdateFieldAllowlistCommand` (`agent_commands_wu16.py:691`) accepts either a fixed column or a defined `custom_field_definitions.name` for the account, resolves non-fixed names to `id` in-transaction, and stores `field_definition_id` (undefined names → `CrmCommandValidationError`), mirroring the schema already shipped in `010`. The dual-column transition gate `_TRANSITION_WRITES = {"stage_id","status"}` (`agent_wu14.py:140`) is untouched.
- **Console "Fuentes del agente" section**: new routes on the existing `build_agent_router` (settings-style `_console_ok`/`_console_body`/`_console_key`) and a new section in the "Agente" view (`web/app/page.tsx`, next to the existing "Configuración"/"Prompt"/"Base de conocimiento"/"Calendario"/"Campos del prospecto"/"Campos personalizados"/"Etiquetas" sections), with strict `runtime-v1.ts` parsers + typed `demo-api.ts` methods mirroring the accepted patterns.
- **Harness**: a NEW `api/test_agent_custom_field_writes_*.py` on a disposable base `production_runtime_mvp_wu18` (runner 001–011 + the seed files), rather than extending the WU17 harness, keeping the accepted harness byte-stable.

### Out of Scope / No-goals

- No automations themselves (roadmap item 4); the binding is agent-only this unit (the producer-scoped shape is forward-compatible with a future automations producer, but none is built).
- No model free-writes: the value is always a deterministically extracted signal, never `reply_text` or model prose.
- No editing of accepted surfaces beyond the two declared CHECK widenings (allowlist write domain + `agent_policy_matrix.action_type` CHECK) and the additive `PROPOSED_ACTION_TYPES`/`_derive_actions`/`_dispatch_actions` entries.
- No real channel (Kapso/WhatsApp), RLS, productive auth, Docker/CI.
- No binding to signals outside the closed catalog; no custom-field schema versioning.

## Capabilities

### New Capabilities

- `agent-custom-field-writes`: a closed signal catalog with deterministic extraction, a producer-scoped signal→field binding (migration `011`), a new proposed action type `set_custom_field_value`, its policy verdict + dispatch, and the "Fuentes del agente" console section — all behind the accepted command/audit/outbox discipline.

### Modified Capabilities

- `configuration` (WU17, declared honestly): the allowlist WRITE path (`UpdateFieldAllowlistCommand`, `PUT /agent/lead-fields`) widens from the closed `ALLOWLIST_COLUMNS` to fixed ∪ defined custom names and persists `field_definition_id` — resolving D4, which the `010` schema and the `load_field_allowlist` read already anticipate (and which `DeleteCustomFieldDefinitionCommand._apply` already clears via `DELETE … WHERE field_definition_id = %s`). No WU17 command is forked; `SetLeadCustomFieldValueCommand` is reused as-is.
- `agent-autonomy` / `agent-console` (additive): `PROPOSED_ACTION_TYPES` gains `set_custom_field_value`; `agent_policy_matrix.action_type` CHECK gains the 8th type; `_derive_actions`/`_dispatch_actions`/`evaluate` gain the new branch; nothing else changes.

## Approach

Migration `011_agent_custom_field_writes` (next free number after `010_configuration`; down-file mirrors the 005/006/007 convention as a real DROP/restore): the binding table (shape settled by Open Decision 3) plus the `agent_policy_matrix.action_type` CHECK drop/re-add to admit `set_custom_field_value`. No binding rows are seeded. A new `api/application/agent_custom_field_bindings_commands.py` (or an addition to the WU17 module family) carries the `CrmCommandBase` binding CRUD (operation `agent.field_binding.*`); the deterministic extraction lives as a pure helper (no I/O, no model text) so it is unit-testable with the stub. `ConversationAgent` gains an injected signal/binding lookup (alongside the existing `search`/`availability`/`upcoming`/`kb_lookup`/`price_read`/`last_interest`/`stage_id_for_name` callables, `conversation_agent.py:112`); `_derive_actions` emits `set_custom_field_value` only when a binding exists and the signal resolved. `_dispatch_actions` delegates to `SetLeadCustomFieldValueCommand.execute_set`; the allowlist gate for the new action reads the same `allowed_lead_fields` frozenset as `transition_lead`. `agent_policy.evaluate` treats the new action as a gated lead-write (default verdict per Open Decision 1). Console routes mount additively on `build_agent_router`; TS mirrors in `contracts/runtime-v1.ts` + `demo-api.ts`; `web/app/page.tsx` adds the "Fuentes del agente" section.

## Acceptance

- [ ] Zero behavior change at install: no bindings seeded ⇒ the agent emits no `set_custom_field_value` and writes no custom field; existing WU17/WU16/WU15 harnesses stay green on their own bases.
- [ ] Binding CRUD: create/edit/delete a signal→field binding per account; unknown signal (outside the catalog) or unknown field rejected; cross-account invisible; deleting a bound `custom_field_definitions` clears the binding.
- [ ] Deterministic extraction: for each catalog signal, the resolved value is byte-deterministic from the validated interpretation/body; a signal with no resolvable value emits no action (never a guessed/free-written value).
- [ ] Agent write reuses the existing command: an auto-executed `set_custom_field_value` writes a `lead_custom_field_values` row through `SetLeadCustomFieldValueCommand`; `number`/`date`/`select∈choices`/`text` coercion is enforced by the existing `_coerce_value`; `UNIQUE (lead_id, field_id)` upserts.
- [ ] Allowlist write widened (D4 closed): `PUT /agent/lead-fields` accepts a defined custom-field name (stores `field_definition_id`, FK satisfied), rejects an undefined name; the seeded `{stage_id, status}` transition via `execute_transition` still succeeds; the dual-column gate `_TRANSITION_WRITES` is untouched.
- [ ] Policy + dispatch: `set_custom_field_value` appears in the verdict table and the `agent_policy_matrix` CHECK; the dispatch branch records `outcome` truthfully (`success`/`denied`/`invalid`/…) with per-action idempotency keys.
- [ ] Forged/ghost ids non-enumerating on every new route; stub-offline runs the full harness with no API key.
- [ ] Full regression: WU14–WU17 harnesses (and earlier) stay green; the new harness passes on its own base.

## Review Workload Forecast

| Slice | Estimate |
|---|---|
| Migration 011 (binding table + `agent_policy_matrix` CHECK widen) | ~70 |
| Signal catalog + deterministic extraction (pure helper + units) | ~90 |
| Binding commands + read adapter | ~80 |
| `_derive_actions` / `_dispatch_actions` / `evaluate` additions | ~60 |
| Allowlist write widening (D4) in `UpdateFieldAllowlistCommand` | ~50 |
| Console routes + "Fuentes del agente" view + contracts/demo-api | ~130 |
| Harness `test_agent_custom_field_writes` | ~120 |
| **Total** | **~400–600, Low–Moderate** |

Decision needed before apply: No (owner decisions in `brief.md` are settled 2026-10-14; the three Open Decisions are design-phase). Smaller than WU17 (~600–900) — this unit reuses the whole WU17 value-write command rather than building a parallel path, and adds one action type + one binding table + one console section.

## Risks and Rollback

Wrong auto-write corrupting a lead's field: mitigated by the closed catalog (no free-writes), the existing `_coerce_value` validation (a `select` value must be `∈ choices`, `number`/`date` must coerce), and the allowlist gate — a field the advisor has not allowed is denied with `lead_field_not_allowed`, mirroring the `transition_lead` gate. Binding drift after a field is edited/deleted: mitigated by the composite FK / `ON DELETE` semantics clearing the binding, and the fact that a `select` field's `choices` change re-validates on the next write through the existing command. Allowlist D4 widening a closed CHECK: the `010` migration already dropped and re-added the allowlist CHECK as a dual CHECK + composite FK; this unit only makes the command match the schema it already ships against, keeping the default `{stage_id, status}` and still rejecting undefined names. Policy matrix CHECK widening admitting an 8th type: mirrored in `CONSOLE_ACTION_TYPES` and the seeded/default matrix so no console surface regresses. Rollback: run `011` down on a reset demo base + remove the binding command/adapter, the `_derive_actions`/`_dispatch_actions`/`evaluate` additions, the console slice and the contract/UI additions, and revert the allowlist-write widening; accepted WU5–WU17 surfaces and prior data untouched by construction (no binding rows exist outside the unit).

## Open Decisions — resolved at design (owner, 2026-10-14)

1. **Default verdict for `set_custom_field_value`: AUTO** (allowlist-gated, like `transition_lead`). RESOLVED.
2. **Extraction trigger scope: only when at least one binding exists** for the lead's account (skip entirely when none — no binding ⇒ no action). RESOLVED.
3. **Binding storage shape: one row per `(client_account_id, signal_key)`**, `UNIQUE (client_account_id, signal_key)`; no producer column yet (deferred until automations exist). RESOLVED.
