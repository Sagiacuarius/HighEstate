# Proposal: Configuration (timezone, custom fields, tags)

## Intent

Ship the roadmap's "Configuración" unit (README.md — "custom fields (alimentan el allowlist de campos del agente), timezone y tags", the next unit after the agent console) on top of the accepted `agent-console` (WU16) and `agent-autonomy` (WU15) surfaces. Three things remain fixed in code or closed DDL today, deferred here by the console brief (`openspec/changes/agent-console/brief.md`): the business timezone is hardcoded (`BUSINESS_TIMEZONE = "America/Argentina/Buenos_Aires"` in `api/domain/calendar_event.py:21`, with `agent_calendar_config` carrying window/slot/days but no timezone and `load_calendar_config` forcing `tz_name=BUSINESS_TIMEZONE` at `postgres_agent_console.py:152-154`); `leads` has no custom columns (`004_commercial_crm.up.sql:74-96` — `id, client_account_id, demo_seed_key, contact_id, selected_property_id, pipeline_id, stage_id, status, source, created_at, updated_at`; nothing else); and `agent_field_allowlist.column_name` is a closed `CHECK IN ('stage_id','status','selected_property_id')` (`009_agent_console.up.sql:71`), so it cannot reference any custom field. This unit (a) makes timezone per-account, (b) adds per-account custom fields on leads that the console allowlist can reference, and (c) adds tags attachable to leads/properties — all managed through the accepted command discipline (idempotency + audit + outbox + tenant scope). Everything extends accepted surfaces additively; nothing accepted breaks, and install is zero behavior change (default timezone = `BUSINESS_TIMEZONE`, allowlist default = `{stage_id, status}`, no custom fields or tags seeded).

## Scope

### In Scope

- **Per-account timezone** (`agent_calendar_config` gains a `timezone` column in migration `010`): the scheduling window already lives there (`window_start`/`window_end`/`slot_minutes`/`working_days`/`destination_label`). `load_calendar_config` (`api/adapters/postgres_agent_console.py:139`) stops forcing `tz_name=BUSINESS_TIMEZONE` and reads the row value; `business_slot_starts` (`api/domain/calendar_event.py:46`) and `free_slots` (`api/adapters/postgres_agent_drafts.py`) consume it, falling back to `BUSINESS_TIMEZONE`/`BUSINESS_*` constants when the row or column is missing. Default seed mirrors the constant, so `free_slots` output is identical at install.
- **Custom fields** (`custom_field_definitions` + `lead_custom_field_values`, migration `010`): `custom_field_definitions` per account (`name`, `type` `CHECK IN ('text','number','select','date')`, plus `choices` for `select`; `UNIQUE (client_account_id, name)`) and a value store keyed to `(definition_id, lead_id)`, account-scoped. Reads/writes go through `CrmCommandBase`-style commands (`configuration.custom_field.define`, `configuration.custom_field.value.set`). No custom fields are seeded.
- **Allowlist extension** (the honest DDL change): `agent_field_allowlist.column_name`'s closed CHECK is dropped and re-added to also admit custom-field names — the gate is that a name is valid when it is either a real `leads` column or a defined `custom_field_definitions.name` for the same account (exact constraint form settled at design). `UpdateFieldAllowlistCommand` (`api/application/agent_commands_wu16.py:636`, which today rejects anything outside `ALLOWLIST_COLUMNS = frozenset({"stage_id","status","selected_property_id"})`, line 55) widens in lockstep. Default allowlist stays `{stage_id, status}`.
- **Tags on contacts only** (`contacts.tags text[]`, already exists at `004_commercial_crm.up.sql:14`): no new `tags` table and no join tables — the console gains CRUD over the existing per-contact tag array (`configuration.contact_tag.*` commands). Leads/properties do NOT gain tags this unit.
- **Console routes + view extension**: new routes under `/v1/demo/agent/configuration/...` (prefix settled at design) on the existing `build_agent_router`; the "Agente" view (`web/app/page.tsx:1441`) gains "Campos personalizados" and "Etiquetas" sections next to "Campos del prospecto" and "Calendario"; TS contract parsers + typed `demo-api.ts` methods mirror the `runtime-v1.ts` / `RuntimeHttpClient` patterns.
- **Harness**: RECOMMEND a NEW `api/test_configuration_wu17.py` on a new disposable base `production_runtime_mvp_wu17` (runner 001–010 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_008.sql` + `mvp_seed_009.sql` + `mvp_seed_010.sql`), rather than extending the WU16 harness — keeps the accepted console harness byte-stable. Scenarios below.

### Out of Scope / No-goals

- No real channel (Kapso/WhatsApp); the console manages the demo agent only.
- No RLS, productive auth, Docker, CI/CD.
- No Google/external calendar sync; no `external_id` plumbing.
- No agent-initiated custom-field writes via a new proposed-action type this unit (advisor/console-only — see Open Decisions); the existing `transition_lead` path and its dual-column gate (`_TRANSITION_WRITES = {stage_id, status}`, `api/routes/agent_wu14.py:125`) are untouched.
- No custom-field schema versioning or historical data migration; no tags on conversations/messages.
- No modification of accepted WU5–WU16 surfaces beyond consumption and the single declared CHECK widening.

## Capabilities

### New Capabilities

- `configuration`: per-account timezone, per-account custom-field definitions + values on leads, contact tags (existing `contacts.tags text[]` array managed via console), and the allowlist extension to reference custom fields — all behind the accepted command/audit/outbox discipline.

### Modified Capabilities

- `agent-console` (declared honestly): `agent_calendar_config` gains a `timezone` column and `load_calendar_config` reads it (resolving the 009 "no tz column" note at `postgres_agent_console.py:152`); `agent_field_allowlist`'s closed CHECK is widened to admit custom-field names for the owning account; the calendar seam (`business_slot_starts`/`free_slots`) threads the account timezone with constant fallback. Nothing else in WU15/WU16 changes: the accepted `transition_lead` dispatch, `TransitionLeadStageCommand._apply` (writes `stage_id` AND `status` together, `crm_commands_wu5.py:494-498`), and the dual-column gate stay byte-identical.

## Approach

Migration `010_configuration` (next free number after `009_agent_console`; down-file mirrors the 005/006/007 convention as a real DROP/restore script): `agent_calendar_config` += `timezone` (seeded to `BUSINESS_TIMEZONE`); new `custom_field_definitions` (`client_account_id`, `name`, `type` CHECK in the small set, `choices` for `select`, `UNIQUE (client_account_id, name)`); new `lead_custom_field_values` (EAV, FK to `(definition_id, client_account_id)` and `(lead_id, client_account_id)`); and the `agent_field_allowlist.column_name` CHECK drop/re-add to admit custom-field names. Tags add NO new table: contact tags ride the existing `contacts.tags text[]` (004). Seed `mvp_seed_010.sql` mirrors the 009 layout (advisory lock, ownership gate, DELETE-then-INSERT of `demo-a/b-*` markers) and seeds only the timezone default — no custom fields, no tags. Commands in a new `api/application/configuration_commands_wu17.py` on the `CrmCommandBase` template (`crm_commands_wu5.py:87`: reserve idempotency → scoped write → audit + outbox → commit); reads in `api/adapters/postgres_configuration.py` (account predicate first, missing rows → constants/empty, no cache). Console routes mount additively on `build_agent_router` (`composition_wu8_mvp.py:372` include stays byte-identical). TS mirrors in `contracts/runtime-v1.ts` (strict `isRecord`-guarded parsers + `runtime-v1.test.ts` fixtures) + typed `demo-api.ts` methods; `web/app/page.tsx` adds the two sections under the existing "Agente" view.

## Acceptance

- [ ] Zero behavior change at install: timezone seeded to `BUSINESS_TIMEZONE` → `business_slot_starts`/`free_slots` output matches the constant oracle; allowlist defaults to `{stage_id, status}`; no custom fields or tags seeded.
- [ ] Timezone round-trip: setting a non-BA account timezone changes `free_slots` outcomes for that account only; missing row → constants; events stay `timestamptz` UTC (`006_calendar_events.up.sql:13-14`), so existing event rows are unaffected.
- [ ] Custom-field CRUD: define/edit/delete a custom field per account; value round-trip (`set`/`read`) over `lead_custom_field_values`; type CHECK rejects unknown types; cross-account invisible.
- [ ] Allowlist references custom fields: `PUT /agent/lead-fields` accepts a defined custom-field name; an undefined name is rejected; the seeded `{stage_id, status}` transition via `execute_transition` still succeeds.
- [ ] Contact tags CRUD: add/remove tags on a contact's existing `tags text[]`; list/filter contacts by tag; account-scoped.
- [ ] Forged/ghost ids non-enumerating on every new route; stub-offline runs the full harness with no API key.
- [ ] Full regression: WU14–WU16 harnesses (and earlier) stay green; `test_configuration_wu17.py` passes on its own base.

## Review Workload Forecast

| Slice | Estimate |
|---|---|
| Migration 010 (timezone col + custom-field defs/values + allowlist CHECK widen) | ~120 |
| Configuration commands (`configuration_commands_wu17.py`) + read adapter | ~180 |
| Console routes (timezone/custom-fields/tags/allowlist) | ~120 |
| Contracts + typed demo-api client | ~100 |
| "Agente" view extension (Campos personalizados + Etiquetas) | ~160 |
| Harness `test_configuration_wu17` | ~200 |
| **Total** | **~600–900, Moderate, chained PRs maybe** |

Decision needed before apply: No (owner requirements in `brief.md` are settled; the three Open Decisions are design-phase).

## Risks and Rollback

Timezone migration breaking existing events: mitigated — `calendar_events.starts_at`/`ends_at` are `timestamptz` (UTC) and the change only alters the timezone-aware window computation (`ZoneInfo`), never stored instants; the seeded default reproduces the constant. Allowlist widening a closed CHECK: mitigated by dropping and re-adding the constraint with the widened domain in one migration, validating against both the real `leads` DDL and `custom_field_definitions` (an undefined custom-field name is still rejected), and keeping the default `{stage_id, status}`. Custom-field value-model drift: mitigated by the type CHECK and scope-pinned FKs. Rollback: run `010` down on a reset demo base + remove the console slice, `configuration_commands_wu17.py`/`postgres_configuration.py`, and the contract/UI additions; accepted WU5–WU16 surfaces and prior data untouched by construction (the CHECK restore returns `agent_field_allowlist` to the closed enum).

## Open Decisions — resolved at design

1. **Custom-field value model**: EAV `lead_custom_field_values` store. RESOLVED (owner 2026-09-16): EAV.
2. **Agent write of custom fields**: advisor/console-only this unit. RESOLVED (owner 2026-09-16): advisor/console-only; agent write deferred (no new proposed-action type).
3. **Tags scope**: contacts only, reusing the existing `contacts.tags text[]` array — no new table. RESOLVED (owner 2026-09-16).
