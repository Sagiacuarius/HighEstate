# Design: Configuration (timezone, custom fields, tags)

## Technical Approach

Additive management surface over the accepted WU16 console, turning three fixed levers into per-account configuration behind the same command discipline (idempotency → scoped write → audit + outbox → commit) and the same read discipline (account predicate first, missing rows → constants). Today the business timezone is the module constant `BUSINESS_TIMEZONE = "America/Argentina/Buenos_Aires"` (`api/domain/calendar_event.py:21`), forced by `load_calendar_config` (`api/adapters/postgres_agent_console.py:152-154`, `tz_name=BUSINESS_TIMEZONE`); `leads` has no custom columns (`004_commercial_crm.up.sql:74-96`); and `agent_field_allowlist.column_name` is a closed `CHECK IN ('stage_id','status','selected_property_id')` (`009_agent_console.up.sql:70-71`). Migration `010_configuration` persists: a `timezone` column on `agent_calendar_config`, a `custom_field_definitions` catalogue plus an EAV `lead_custom_field_values` store, and an allowlist that can reference defined custom-field names via a composite FK. Contact tags reuse the existing `contacts.tags text[]` array (`004_commercial_crm.up.sql:14`) — no new table. The three resolved owner decisions (2026-09-16) are baked in: EAV value model; advisor/console-only custom-field writes (no new proposed-action type, the agent still writes only `stage_id`+`status` through `TransitionLeadStageCommand._apply`, `crm_commands_wu5.py:494-498`); tags on contacts only. The console routes mount additively on the existing `build_agent_router` — the `app.include_router(build_agent_router(...))` line at `composition_wu8_mvp.py:372` stays byte-identical.

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Timezone column** | `ALTER TABLE agent_calendar_config ADD COLUMN timezone text` — NULL, no DB default; read-time fallback to `BUSINESS_TIMEZONE`. `load_calendar_config` returns `tz_name = row["timezone"] or BUSINESS_TIMEZONE`. Seed `010` sets the two demo rows to the constant (converging UPDATE) for a concrete round-trip target | Nullable-with-fallback matches the existing nullable-column pattern (`window_start`/`working_days` are already nullable and fall back). NULL ⇒ the 009-seeded rows and any pre-010 base produce byte-identical `free_slots` output at install (zero behavior change). No `NOT NULL DEFAULT` backfill, so `ALTER` is instant and the down-file is a plain `DROP COLUMN`. |
| **Timezone seam** | `CalendarConfig` (`postgres_agent_console.py:56`) already carries a `tz_name` field; `free_slots` already consumes it through `_resolve_calendar` → `business_slot_starts(day, tz_name=...)` (the Batch B additive kwargs). The ONLY code change in the free-slot path is `load_calendar_config` reading the new column (and `UpdateCalendarConfigCommand` accepting/validating a `timezone` field) | The injection point already exists — `_availability` in `agent_wu14.py:441-449` passes the `CalendarConfig` NamedTuple into `free_slots`, and `_resolve_calendar` reads `getattr(calendar, "tz_name")`. No `_resolve_calendar` edit is required for the NamedTuple path. `business_slot_starts`'s `tz_name` kwarg defaults to the constant, so bare calls stay exact. |
| **UI availability agreement (factual gap in WU16 design)** | The WU13 availability service `CalendarReadService.availability` (`api/queries/calendar_reads_wu13.py:101-128`) currently reads `ZoneInfo(BUSINESS_TIMEZONE)` + bare `business_slot_starts(day)` — it does NOT read `agent_calendar_config` (the agent-console design's "reads the same row" claim is inaccurate). This unit threads `load_calendar_config(context, self._runner)` into that method so `GET /calendar/availability` uses the same `tz_name`/window/days/slot as agent booking | Otherwise the calendar UI and the agent would disagree on availability the moment a non-BA timezone is set — violating the "both read the same config" acceptance. The service already holds `self._runner` (`calendar_reads_wu13.py:76`), so the edit is a pure read-side substitution with constant fallback. |
| **Custom fields: definitions + EAV values (OD-1)** | `custom_field_definitions` (one row per account × name: `name`, `type CHECK IN ('text','number','select','date')`, `choices text[]` for `select`, `UNIQUE (client_account_id, name)`, `demo_seed_key`) + `lead_custom_field_values` (EAV: `lead_id`, `field_id`, `value text`, `UNIQUE (lead_id, field_id)`, composite FKs pinning `(lead_id, client_account_id)` and `(field_id, client_account_id)`). Value stored as `text` regardless of type; coercion/validation is the command layer's job | The value set is sparse and heterogeneous; a typed `text` EAV avoids a column-per-type model and any future ALTER-per-field. `value` as canonical text keeps `number`/`date` reversible (e.g. `"2026-01-15"`), and `UNIQUE (lead_id, field_id)` guarantees one value per field per lead. |
| **Value type validation** | Pure helper `coerce_value(type, value, choices)`: `text` → bounded string; `number` → numeric literal (reject `bool`, store canonical decimal); `date` → ISO-8601 `YYYY-MM-DD`; `select` → must be `∈ choices` | Validation is deterministic and unit-testable; the DB stores only already-validated text, so reads never re-validate. The definition's `type` is the single source of truth; a value can't be written for an undefined field. |
| **Allowlist CHECK → composite FK** | Drop the closed `column_name` CHECK; add `field_definition_id uuid NULL` to `agent_field_allowlist` with composite FK `(client_account_id, field_definition_id) → custom_field_definitions (id, client_account_id)` and a dual CHECK: `(field_definition_id IS NULL AND column_name IN ('stage_id','status','selected_property_id')) OR (field_definition_id IS NOT NULL AND btrim(column_name) <> '')`. `column_name` mirrors the definition `name` for custom rows | The old CHECK can only reference literals, so it can never admit account-dynamic names. A composite FK is the clean answer: referential integrity for custom names is DB-enforced (not one command path), the fixed-column enum survives unweakened, and deleting a definition that is still referenced is blocked by `ON DELETE RESTRICT` until the delete command clears children. The looser-CHECK alternative (drop to `btrim <> ''` + validate in `UpdateFieldAllowlistCommand`) is simpler but leaves orphaned names possible and integrity in exactly one code path. Migration ordering: `custom_field_definitions` must be created BEFORE the FK; the down-file drops the FK + `field_definition_id`, restores the closed CHECK, then drops the value/definition tables. |
| **Allowlist write lockstep** | `UpdateFieldAllowlistCommand` (today `agent_commands_wu16.py:636`, rejects anything outside `ALLOWLIST_COLUMNS = frozenset({"stage_id","status","selected_property_id"})` at line 55) widens: each allowed name is either a fixed column or a defined `custom_field_definitions.name` for the account. `_apply` resolves non-fixed names to `id` via a scoped SELECT inside the transaction; an undefined name raises `CrmCommandValidationError`. `load_field_allowlist` (`postgres_agent_console.py:114`) returns `column_name` unchanged (fixed and custom names both live there) | Same command, same operation `agent.allowlist.update`, same DELETE-then-INSERT — the only change is the validity domain and storing `field_definition_id` for custom rows. The dual-column gate (`_TRANSITION_WRITES = {stage_id, status}`, `agent_wu14.py:125`) is untouched: custom names in the allowlist are inert for the agent this unit (no proposed-action type writes them), and remain forward-compatible entries. |
| **Contact tags (OD-3)** | No new table: `AddContactTagsCommand` / `RemoveContactTagsCommand` mutate `contacts.tags text[]` in place (array `||` append / `array_remove`-style filter), dedupe + `trim`/`lower` canonicalization, account-scoped by `contact_id`. A read `list_contacts_by_tag` filters `%s = ANY(tags)` | `contacts.tags text[]` already exists and `Contact.tags` is a tuple in the domain (`postgres_commercial_crm.py:89`); a `text[]` mutation is a single `UPDATE`, no migration. Canonical (lowercased) tags make `=`/filter deterministic and avoid `"VIP"` vs `"vip"` drift. |
| **Router + routes** | Extend `build_agent_router` additively (settings-style `_console_ok`/`_console_body`/`_console_key`); timezone rides the existing `GET/PUT /agent/calendar-config` as an additive `timezone` key, not a new endpoint | One agent surface, one mount, shared correlation/denial handling. Custom fields and tags get new `/agent/custom-fields`, `/agent/leads/{lead_id}/custom-values`, `/agent/contacts?...` routes (below). |
| **Read path / fallback** | New `api/adapters/postgres_configuration.py`: `load_custom_field_definitions`, `load_custom_field_values`, `list_contacts_by_tag` — account predicate first, missing rows → empty, no cache (WU13 "correctness > latency" precedent). `load_field_allowlist`/`load_calendar_config` extended in `postgres_agent_console.py` | No invalidation bugs, old bases behave as today: no definitions → no custom values, no `timezone` column → constant, allowlist absent → `{stage_id, status}`. |
| **Audit / outbox** | Commands write `audit_records` + `outbox_events` via the accepted `PostgresAuditOutboxRepository` (`configuration.custom_field.*`, `configuration.contact_tag.*`); `UpdateCalendarConfigCommand` reuses `agent.calendar.update`, `UpdateFieldAllowlistCommand` reuses `agent.allowlist.update` | Same durable/tenant-scoped discipline as 004–009; no new outbox event machinery. |

## Contracts, Invariants, and Flow

New endpoints on the agent router (all under `/v1/demo`, all return `X-Correlation-Id` + `correlation_id`; unknown keys → 400; forged/foreign ids → 404 non-enumerating; strict enums via `runtime-v1.ts` parsers):

- `GET /agent/calendar-config` / `PUT /agent/calendar-config` — extended with `timezone` (additive key; existing `window_start`/`window_end`/`slot_minutes`/`working_days`/`destination_label` unchanged). Invalid IANA `timezone` (`ZoneInfo(value)` raises) → 400.
- `GET /agent/custom-fields` → `_console_ok(correlation, "custom_fields", {count, items:[{id, name, type, choices}]})`; `POST /agent/custom-fields {name, type, choices?}` → `{resource_id, name, type, choices}`; `PUT /agent/custom-fields/{field_id} {name?, type?, choices?}`; `DELETE /agent/custom-fields/{field_id}` (clears values + allowlist refs, then the definition, in one transaction).
- `GET /agent/leads/{lead_id}/custom-values` → `_console_ok(correlation, "custom_values", {lead_id, values:[{field_id, name, type, value}]})`; `PUT /agent/leads/{lead_id}/custom-values {values:{[name]: value}}` — each name must be a defined field, value coerced per its `type` (`number`/`date` coercion, `select ∈ choices`, `text` free), upserted; unknown name or bad value → 400.
- `GET /agent/contacts?tag=` → `_console_ok(correlation, "contacts", {count, items:[{id, name, tags}]})`; `PUT /agent/contacts/{contact_id}/tags {add?: string[], remove?: string[]}` → `_console_ok(correlation, "tags", {contact_id, tags})` (dedupe + canonicalize, returns the final array).

Flow: `POST /agent/custom-fields` → `CreateCustomFieldDefinitionCommand` (reserve → INSERT → audit `configuration.custom_field.define` + outbox → commit) → the name becomes legal in `PUT /agent/lead-fields` and writable via `PUT /agent/leads/{id}/custom-values`. `PUT /agent/calendar-config {timezone}` → `UpdateCalendarConfigCommand` → next `GET /calendar/availability` and next agent `schedule_visit` both compute slots in the new zone. `PUT /agent/contacts/{id}/tags` → `AddContactTagsCommand`/`RemoveContactTagsCommand` mutate `contacts.tags` in place.

Invariants: one definition per account × name (`UNIQUE (client_account_id, name)`); one value per lead × field (`UNIQUE (lead_id, field_id)`); every value's `field_id` belongs to the same `client_account_id` as the lead (composite FKs); an allowlist row is either a fixed `leads` column or a defined custom field for the same account (dual CHECK + composite FK); the agent's transition gate still requires `{stage_id, status}`; every console write tenant-scoped + idempotent (`console:{scope}:{entity}:{key}` keys); `execution_mode='agent_policy'` attribution rules unchanged.

SQL shape (010 UP — next free number after 009; DOWN mirrors the 005/006/007 convention, reversing each part in reverse order):

```sql
CREATE TABLE custom_field_definitions (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    name text NOT NULL CHECK (btrim(name) <> ''),
    type text NOT NULL CHECK (type IN ('text','number','select','date')),
    choices text[],
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT custom_field_definitions_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT custom_field_definitions_account_name_unique UNIQUE (client_account_id, name),
    CONSTRAINT custom_field_definitions_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX custom_field_definitions_account_idx ON custom_field_definitions (client_account_id, id);

CREATE TABLE lead_custom_field_values (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    lead_id uuid NOT NULL,
    field_id uuid NOT NULL,
    value text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lead_custom_field_values_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT lead_custom_field_values_lead_field_unique UNIQUE (lead_id, field_id),
    CONSTRAINT lead_custom_field_values_lead_account_fk FOREIGN KEY (lead_id, client_account_id)
        REFERENCES leads (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT lead_custom_field_values_field_account_fk FOREIGN KEY (field_id, client_account_id)
        REFERENCES custom_field_definitions (id, client_account_id) ON DELETE RESTRICT
);
CREATE INDEX lead_custom_field_values_account_lead_idx ON lead_custom_field_values (client_account_id, lead_id, id);

ALTER TABLE agent_calendar_config ADD COLUMN timezone text;

-- allowlist widening: closed CHECK -> composite FK (custom_field_definitions
-- created above, so the FK resolves).
ALTER TABLE agent_field_allowlist DROP CONSTRAINT agent_field_allowlist_column_name_check;
ALTER TABLE agent_field_allowlist ADD COLUMN field_definition_id uuid;
ALTER TABLE agent_field_allowlist ADD CONSTRAINT agent_field_allowlist_column_name_check
    CHECK ((field_definition_id IS NULL AND column_name IN ('stage_id','status','selected_property_id'))
        OR (field_definition_id IS NOT NULL AND btrim(column_name) <> ''));
ALTER TABLE agent_field_allowlist ADD CONSTRAINT agent_field_allowlist_field_account_fk
    FOREIGN KEY (client_account_id, field_definition_id)
    REFERENCES custom_field_definitions (id, client_account_id) ON DELETE RESTRICT;
```

Value coercion (pure helper, `api/domain/custom_field.py`):

```python
CUSTOM_FIELD_TYPES = ("text", "number", "select", "date")

def coerce_value(type_, value, choices):
    # number -> Decimal(str(value)), reject bool; date -> date.fromisoformat;
    # select -> value must be in choices; text -> bounded str. Raises on mismatch.
```

## Files and Composition

- `infra/migrations/010_configuration.up.sql` / `.down.sql` (above; DOWN: drop `agent_field_allowlist_field_account_fk` + `field_definition_id`, restore the closed `agent_field_allowlist_column_name_check`, drop `lead_custom_field_values`, `custom_field_definitions`, drop `agent_calendar_config.timezone` — order matters because of the FKs; precondition mirrors 009: disposable demo bases, reset first).
- `infra/migrations/validation/mvp_seed_010.sql` (mirrors the 009 layout — `\set ON_ERROR_STOP on`, single transaction, `pg_advisory_xact_lock(hashtext('mvp-commercial-demo-seed-reset'))`, fail-closed ownership gate, converge): UPDATEs the two `agent_calendar_config` rows (`demo-a-cal-config` / `demo-b-cal-config`) to `timezone = 'America/Argentina/Buenos_Aires'`. No custom fields, no tags seeded.
- `api/application/configuration_commands_wu17.py` — `CreateCustomFieldDefinitionCommand` (`configuration.custom_field.define`), `UpdateCustomFieldDefinitionCommand` (`configuration.custom_field.update`), `DeleteCustomFieldDefinitionCommand` (`configuration.custom_field.delete`, clears values + allowlist refs first), `SetLeadCustomFieldValuesCommand` (`configuration.custom_field.value.set`), `AddContactTagsCommand` (`configuration.contact_tag.add`), `RemoveContactTagsCommand` (`configuration.contact_tag.remove`) — all `CrmCommandBase`.
- `api/application/agent_commands_wu16.py` — `UpdateCalendarConfigCommand` gains `timezone` (validate via `ZoneInfo`), `UpdateFieldAllowlistCommand` widens to fixed ∪ defined names and stores `field_definition_id` (operation names unchanged).
- `api/adapters/postgres_agent_console.py` — `load_calendar_config` reads `timezone` (`tz_name = row["timezone"] or BUSINESS_TIMEZONE`); `load_field_allowlist` unchanged in shape (returns names).
- `api/adapters/postgres_configuration.py` — `load_custom_field_definitions`, `load_custom_field_values`, `list_contacts_by_tag`.
- `api/queries/calendar_reads_wu13.py` — `CalendarReadService.availability` threads `load_calendar_config(context, self._runner)` instead of the bare constants (the WU16 design's "same row" claim, now actually realized).
- `api/domain/custom_field.py` — `CUSTOM_FIELD_TYPES`, `coerce_value`.
- `api/routes/agent_wu14.py` — new console endpoints on `build_agent_router`; `timezone` key added to `_CALENDAR_KEYS`; new `_CUSTOM_FIELD_KEYS` / `_CUSTOM_VALUES_KEYS` / `_TAGS_KEYS`; `_dispatch_actions` transition gate untouched.
- `contracts/runtime-v1.ts` (+ test) — `CUSTOM_FIELD_TYPES`, `CustomFieldDefinition`, `CustomFieldDefinitionsResponse`, `CustomFieldWriteResponse`, `CustomFieldValuesResponse`, `CustomFieldValuesWriteResponse`, `ContactTagsResponse`, `ContactsByTagResponse` + strict `isRecord`-guarded parsers + fixtures; `AgentCalendarConfig` gains `timezone`; `parseAllowedColumns` widens from `LEAD_FIELD_COLUMNS.includes(...)` to non-empty-string (server is the authority; custom names are account-dynamic and not enumerable client-side). `web/app/demo-api.ts` — typed methods (`getCustomFields`/`createCustomField`/`updateCustomField`/`deleteCustomField`, `getLeadCustomValues`/`setLeadCustomValues`, `listContactsByTag`/`updateContactTags`, `CalendarConfigInput` + `timezone`) reusing the `RuntimeHttpClient` pattern. `web/app/page.tsx` — "Agente" view gains "Campos personalizados" + "Etiquetas" sections next to the existing five, and the "Calendario" form gains a `timezone` input (Spanish labels, existing `panel`/`audit-filters`/`data-table` classes).
- Units: `api/test_configuration_timezone.py` (constant oracle + non-BA shift), `api/test_configuration_custom_fields.py` (coercion/validation matrix), `api/test_configuration_tags.py` (add/remove/dedupe/canonicalization), `api/test_configuration_allowlist.py` (fixed ∪ custom names, undefined rejected, dual-column gate intact).
- Harness: `api/test_configuration_wu17.py` on NEW disposable base `production_runtime_mvp_wu17` (runner 001–010 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_008.sql` + `mvp_seed_009.sql` + `mvp_seed_010.sql`).

## Testing Strategy

Harness scenarios (StubModelProvider, offline; every effect via the accepted command surfaces, never hand-inserted): T1 timezone round-trip — `PUT /agent/calendar-config {timezone:"America/New_York"}` changes `GET /calendar/availability` and agent `free_slots` outcomes for that account only; deleting the row restores the constant oracle. T2 custom-field CRUD — define/edit/delete a field per account; `type` CHECK rejects unknown types; cross-account invisible. T3 value round-trip — `PUT /agent/leads/{id}/custom-values` coerces `number`/`date`, rejects `select` outside `choices`, accepts `text`; read returns canonical text; `UNIQUE (lead_id, field_id)` upserts. T4 allowlist extension — `PUT /agent/lead-fields` accepts a defined custom-field name (FK satisfied), rejects an undefined name; deleting a still-referenced definition is blocked until un-referenced; the seeded `{stage_id, status}` transition via `execute_transition` still succeeds. T5 contact tags — add/remove dedupes + canonicalizes; `GET /agent/contacts?tag=` filters; account-scoped. T6 forged/ghost ids non-enumerating on every new route; stub runs T1–T5 offline with no API key (`langchain_openai` never imported). T7 idempotent replays (calendar-config/custom-field/value/tags/allowlist) zero extra effects. T8 regression — WU14/WU15/WU16 harnesses (and earlier) stay green on their own bases; `compileall`, typecheck, `npm test`.

## Risks and Rollback

Timezone change on existing `timestamptz` — mitigated: `calendar_events.starts_at`/`ends_at` are stored UTC (`006_calendar_events.up.sql:13-14`); the change only alters the `ZoneInfo` used to compute the window, never stored instants, and the seeded default reproduces the constant. Allowlist CHECK→FK ordering — mitigated by creating `custom_field_definitions` before the FK and by the down-file dropping the FK before the table; undefined names are still rejected, and the default `{stage_id, status}` holds. Custom-field value drift — mitigated by the `type` CHECK, the composite FKs, and command-layer coercion. Tags casing/duplicates — mitigated by canonicalization (trim/lower + dedupe) at write. Rollback: run `010` down on a reset demo base + remove the console slice (`configuration_commands_wu17.py`, `postgres_configuration.py`, the `calendar_reads_wu13.py` availability threading, the contract/UI additions, and the `timezone`/`field_definition_id` handling in `agent_commands_wu16.py`/`postgres_agent_console.py`); accepted WU5–WU16 surfaces and prior data untouched by construction (the CHECK restore returns `agent_field_allowlist` to the closed enum, and the WU13 availability threading falls back to the constant when no config row exists).
