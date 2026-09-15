# Design: Calendar / Agenda

## Technical Approach

Additive domain over accepted seams. `CalendarEvent` is persisted in a new `calendar_events` table (migration `006`); three atomic commands follow the accepted `CrmCommandBase` template (idempotency reserve → scoped write → one audit + one outbox → commit). Availability is a pure deterministic function over business hours and non-cancelled events. Mounted as a `calendar_wu13` APIRouter on the accepted WU8 app exactly like WU9/WU12 (shared resolver/cache/runner, one additive `include_router`).

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Entity | `CalendarEvent` domain: `id, client_account_id, title, description, starts_at, ends_at, contact_id, lead_id, property_id, status, source, created_at, updated_at` | A visit/appointment is first-class; `contact/lead/property` are optional references (a generic event is valid). |
| Persistence | `calendar_events` (migration 006); `status CHECK (scheduled,cancelled,completed)`, `source CHECK (manual,agent)`, `starts_at < ends_at` CHECK; index `(client_account_id, starts_at)` | Matches the durable/tenant-scoped discipline of 004/005; the CHECKs enforce invariants at the DB layer. |
| Commands | `CreateCalendarEventCommand` (`calendar.create`), `UpdateCalendarEventCommand` (`calendar.update`), `CancelCalendarEventCommand` (`calendar.cancel`) — all `CrmCommandBase` subclasses | Reuses the accepted atomic template: idempotency, audit, outbox, redacted denial. |
| Delete semantics | Cancel is a soft delete (`status='cancelled'`); no physical DELETE | Audit trail is append-only; a cancelled visit stays visible and attributable. |
| Overlap rule | Two non-cancelled events in the same scope cannot overlap in time: `a.starts_at < b.ends_at AND b.starts_at < a.ends_at`; enforced in the create/update `_apply` with a locked scope predicate | Prevents double-booking; parameterized, no invented columns. |
| Timezone | Store UTC `timestamptz`; compute availability and render in a fixed `BUSINESS_TIMEZONE` (`America/Argentina/Buenos_Aires`) | Simplification for the demo; keeps the availability math deterministic and testable. |
| Availability | Business hours Mon–Sat 09:00–18:00, 60-min slots; free = business slots minus any overlap with non-cancelled events | User-chosen Option A. Pure function, no cache (correctness > latency). |
| Reads | `GET /v1/demo/calendar/events?from&to` (default excludes cancelled, `include_cancelled` opt-in) and `GET /v1/demo/calendar/availability?from&to` | Filters validated before SQL (ISO range, sane span); account predicate first. |
| Mount point | `build_calendar_router(conn_kwargs)` APIRouter included on the WU8 app under `/v1/demo/calendar` | WU9/WU12 precedent; accepted routes byte-identical. |
| UI | New `ViewKey` "calendario" + nav item; week view (7 day columns), event cards, create/edit/cancel forms, `statusLabel`/Spanish copy, empty/error states | Manual CRUD lives here; the agent (WU14) reuses the read API, not this UI. |

## Contracts, Invariants, and Flow

`POST /v1/demo/calendar/events {title, description?, startsAt, endsAt, contactId?, leadId?, propertyId?}` → 200 `{event}`. `PUT /v1/demo/calendar/events/{id} {…}` → 200 `{event}`. `DELETE /v1/demo/calendar/events/{id}` → 200 `{event, status:'cancelled'}` (soft). `GET /v1/demo/calendar/events?from&to[&include_cancelled]` → `{items:[CalendarEvent], count, account_id, as_of, correlation_id}`. `GET /v1/demo/calendar/availability?from&to` → `{items:[{startsAt, endsAt}], count, account_id, as_of, correlation_id}`.

Availability algorithm (pure, deterministic): for each calendar day in `[from, to]` whose weekday is Mon–Sat, generate slot starts at 09:00, 10:00, …, 17:00 (60-min) in `BUSINESS_TIMEZONE`; convert each slot `[start, start+60m)` to UTC; drop any slot that overlaps a non-cancelled event (per the overlap predicate above); emit the remaining slots in chronological order. The harness validates the output against an independent time-walk oracle, including the 09:00-slot end-overlap case.

Invariants: every read/write carries server-derived `client_account_id`; `starts_at < ends_at`; no two non-cancelled events in scope overlap; cancel never physically deletes; commands are idempotent; cross-account ids are non-enumerating (404/empty). `source=agent` is accepted by the schema but not written in WU13 (reserved for WU14 dispatch).

SQL shape (availability overlap, single statement, parameters only, account predicate first):
```sql
SELECT id, starts_at, ends_at FROM calendar_events
WHERE client_account_id = %s
  AND status <> 'cancelled'
  AND starts_at < %s   -- slot_end (UTC)
  AND ends_at   > %s   -- slot_start (UTC)
```
(Each candidate slot checks this predicate; free = no rows returned. The service materializes the candidate slots in memory and queries overlaps per slot, or folds into a single range query — pinned in the adapter; either way, parameters only.)

## Files and Composition

- `api/domain/calendar_event.py` — `CalendarEvent`, `EVENT_STATUSES`, `EVENT_SOURCES`, `BUSINESS_HOURS`, `BUSINESS_TIMEZONE`, `SLOT_MINUTES`.
- `api/adapters/postgres_calendar.py` — event CRUD SQL + availability overlap query.
- `api/application/calendar_commands_wu13.py` — the three atomic commands.
- `api/queries/calendar_reads_wu13.py` — event list + `compute_free_slots` availability service (validation, projection).
- `api/routes/calendar_wu13.py` — `build_calendar_router`; wired in `composition_wu8_mvp.py` with one `include_router`.
- `infra/migrations/006_calendar_events.up.sql` / `.down.sql`.
- `contracts/runtime-v1.ts` — `CalendarEventResponse`/`AvailabilityResponse` + parsers + `contracts/runtime-v1.test.ts`.
- `web/app/demo-api.ts` — `createEvent`/`updateEvent`/`cancelEvent`/`listEvents`/`availability`; `web/app/page.tsx` — "Calendario" view; `demo.css` only if a class is missing.
- `api/test_calendar_wu13.py` — real-PostgreSQL harness on NEW base `production_runtime_mvp_wu13`.

## Testing Strategy

Harness scenarios: C1 create tenant-scoped event + forged account ignored; C2 malformed times/enums → 400; C3 update fields incl. reschedule; C4 overlap → 409/400; C5 `starts_at >= ends_at` → 400; C6 cancel is soft (no physical delete, audited); C7 idempotent replay (create/update/cancel) zero extra effects; C8 events-in-range read + cross-account invisible; C9 availability matches time-walk oracle incl. 09:00 end-overlap + cancelled events free the slot; C10 audit rows visible in `GET /v1/demo/audit`; C11 A/B isolation; C12 accepted WU6 `agenda` response unchanged (regression pin). UI: vitest jsdom renders the real page with stubbed fetch (week + events, create/edit/cancel wiring, empty/error). Regression: compileall + WU3–WU12 harnesses + typecheck + npm test; new base only; `work-unit-13-evidence.md`; status "implemented, pending review".

## Risks and Rollback

Timezone/overlap boundary bugs — pinned by UTC storage, fixed `BUSINESS_TIMEZONE`, and the explicit overlap predicate + independent oracle. Soft-delete leaking into views — reads default to excluding `cancelled`. Rollback: remove mount + WU13 modules/UI/contract slice + run `006` down; accepted WU6 agenda read and prior data untouched.
