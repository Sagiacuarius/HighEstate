# Proposal: Calendar / Agenda

## Intent

Introduce a real calendar: a tenant-scoped `calendar_events` entity with atomic create/update/cancel commands and a deterministic business-hours availability model, exposed through a versioned read API and a "Calendario" view in the dashboard. This is the durable foundation the conversational agent (WU14) will build on to propose, see availability, and reschedule visits. Nothing existing is modified: the accepted WU6 `agenda` read (follow-up rollup) keeps its route and shape; the calendar is a new additive surface.

## Scope

### In Scope
- Migration `006_calendar_events`: `calendar_events` table (`id, client_account_id, title, description, starts_at, ends_at, contact_id, lead_id, property_id, status, source, created_at, updated_at`) with tenant index + overlap-friendly ordering index; `status` CHECK (`scheduled|cancelled|completed`), `source` CHECK (`manual|agent`), `starts_at < ends_at` CHECK.
- Three atomic commands following `CrmCommandBase` (reserve idempotency → scoped write → one audit + one outbox → commit): `CreateCalendarEventCommand` (`calendar.create`), `UpdateCalendarEventCommand` (`calendar.update`), `CancelCalendarEventCommand` (`calendar.cancel`). Update validates overlap within scope; cancel is a soft delete (`status='cancelled'`), never a physical DELETE.
- Deterministic availability: business hours **Mon–Sat 09:00–18:00**, 60-minute slots, in a fixed `BUSINESS_TIMEZONE` (default `America/Argentina/Buenos_Aires`); free = business slots minus any overlap with non-cancelled events.
- Versioned reads: `GET /v1/demo/calendar/events?from&to` (events in range) and `GET /v1/demo/calendar/availability?from&to` (free slots). Both carry the server-derived `client_account_id` predicate; invalid ranges/enums fail closed (400).
- Dashboard "Calendario" view: week view, create/edit/cancel event forms, `statusLabel`/Spanish copy, empty/error states with the versioned envelope + correlation.
- TS contract mirrors in `contracts/runtime-v1.ts` (calendar event + availability response parsers) and typed methods in `web/app/demo-api.ts`.

### Out of Scope / No-goals
- No per-advisor calendars (single demo advisor), no per-account business hours (global demo config), no reminders/notifications, no recurrence, no multi-timezone, no Google/Outlook sync, no hard delete.
- No agent-driven events in this unit (`source=agent` is reserved for WU14; WU13 writes `manual` only).
- No roles/permissions (operator surface), no RLS, productive auth, Docker, CI/CD.

## Capabilities

### New Capabilities
- `calendar-agenda`: durable events with manual CRUD and deterministic availability reads.

### Modified Capabilities
- None at the requirement level. `composition_wu8_mvp.py` gains one additive `include_router` (same seam WU9/WU12 used); the accepted WU6 `agenda` read stays byte-identical.

## Approach

Add `CalendarEvent` domain, a `postgres_calendar` SQL adapter (event persistence + availability query), three atomic commands in `calendar_commands_wu13.py`, a read service with the free-slot computation, and a `calendar_wu13` APIRouter mounted on the WU8 app. Migration `006` adds the table. The frontend adds a "Calendario" view with CRUD forms.

## Acceptance

- [ ] `POST /v1/demo/calendar/events` creates a tenant-scoped `scheduled` event; a forged `client_account_id` cannot change scope; malformed times/durations/enums fail closed (400).
- [ ] `PUT /v1/demo/calendar/events/{id}` updates fields incl. reschedule; an overlap with another non-cancelled event in scope fails closed (409/400); `starts_at >= ends_at` is rejected.
- [ ] `DELETE /v1/demo/calendar/events/{id}` (or cancel route) soft-cancels: `status='cancelled'`, no physical row removal, audited `calendar.cancel`.
- [ ] `GET /v1/demo/calendar/events?from&to` returns only events whose `client_account_id` is server-resolved and whose `[starts_at, ends_at)` intersects the range; cross-account invisible.
- [ ] `GET /v1/demo/calendar/availability?from&to` returns 60-min slots within Mon–Sat 09:00–18:00 (business timezone) excluding any overlap with non-cancelled events; a direct-SQL/time oracle in the harness matches it; a `scheduled` event at 10:00 removes the 10:00 slot and also the 09:00 slot whose end overlaps it.
- [ ] All commands are idempotent (replay returns the stored result with zero extra effects); audit rows (`calendar.create|update|cancel`) are visible in the accepted audit screen.
- [ ] UI: "Calendario" view renders week + events, create/edit/cancel wire to typed endpoints, empty/error states use the envelope; no changes to the accepted "Agenda" mini-feed.
- [ ] Regression: `compileall`, WU3–WU12 harnesses, `typecheck`, `npm test` green on new disposable base `production_runtime_mvp_wu13` (runner 001–006 + seeds); earlier bases untouched; `work-unit-13-evidence.md` records commands and exit codes.

## Review Workload Forecast

Estimated authored implementation: 800–1,100 lines (domain ~80, migration ~40, SQL adapter ~180, three commands ~260, availability/read service ~160, router ~120, contract mirror ~140, UI view+forms ~300, harness ~280). 800-line budget risk: High. Chained PRs recommended: Yes. Decision needed before apply: No. Delivery: auto-chain, feature-branch-chain; single reviewable unit (WU13) atop the accepted chain.

## Risks and Rollback

Overlap logic mis-matches timezone or inclusive/exclusive boundaries — pinned by storing UTC `timestamptz`, computing availability in `BUSINESS_TIMEZONE`, and an explicit overlap predicate + time oracle in the harness. Cancel-as-soft-delete could leave stale UI if the view does not filter `cancelled` — mitigated by the read filtering non-cancelled by default with an explicit `include_cancelled` opt-in. Rollback: remove router mount + WU13 modules/UI/contract slice and run `006` down; accepted WU6 agenda read and all prior data untouched by construction.

## Open Decisions

Confirm only at review: whether the accepted "Agenda" mini-feed later consolidates into "Calendario", and whether 60-minute fixed slots should later become configurable per advisor — both deferred.
