# Tasks: Calendar / Agenda (WU13)

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 800–1,100 authored lines |
| 800-line budget risk | High |
| Chained PRs recommended | Yes — single unit WU13 on the feature-branch-chain after WU12 |
| 400-line budget risk | High |

Decision needed before apply: No. Delivery: auto-chain, feature-branch-chain. Tests and evidence stay with the behavior they prove.

## Status note

No implementation code for this change exists yet. `web/app/page.tsx` and `web/app/demo.css` carry the accepted WU12 "Auditoría" view — audit the working tree before implementing; extend what is there, do not re-scaffold, and do not revert accepted-adjacent edits without flagging.

## Phase Checklist

- [ ] Phase 0 — Working-tree audit: confirm no calendar/event code pre-exists; confirm the WU12 audit-screen view and `composition_wu8_mvp.py` mounts are as expected; record findings in evidence.
  - Verify: inventory + `npm.cmd run typecheck` green before starting.
- [ ] Phase 1 — Migration `006_calendar_events.up.sql` / `.down.sql`: table + tenant index + CHECKs (`status`, `source`, `starts_at < ends_at`).
  - Verify: runner applies 001–006 clean on a fresh base; `down` reverts.
- [ ] Phase 2 — `api/domain/calendar_event.py`: `CalendarEvent`, enums, `BUSINESS_HOURS`, `BUSINESS_TIMEZONE`, `SLOT_MINUTES`.
  - Verify: unit scenarios for the availability slot generator (Mon–Sat, 09:00–17:00 slots).
- [ ] Phase 3 — `api/adapters/postgres_calendar.py`: event CRUD SQL + availability overlap query (parameters only, account predicate first).
  - Verify: harness C1, C6, C8, C9 partially; cross-account invisible.
- [ ] Phase 4 — `api/application/calendar_commands_wu13.py`: the three atomic commands (create/update/cancel), overlap validation, soft-delete cancel.
  - Verify: harness C1–C7, C10.
- [ ] Phase 5 — `api/queries/calendar_reads_wu13.py`: event list + `compute_free_slots` availability service; `api/routes/calendar_wu13.py` router + one additive `include_router` in `composition_wu8_mvp.py`.
  - Verify: harness C8, C9 (oracle match), 401/403/404/405 envelope probes.
- [ ] Phase 6 — Contract mirrors `contracts/runtime-v1.ts` (`CalendarEventResponse`, `AvailabilityResponse`) + tests; typed `createEvent`/`updateEvent`/`cancelEvent`/`listEvents`/`availability` in `web/app/demo-api.ts`.
  - Verify: vitest parser tests incl. rejecting invalid enums / unknown keys.
- [ ] Phase 7 — Dashboard "Calendario" view: nav item, week view, create/edit/cancel forms, Spanish `statusLabel`, empty/error states.
  - Verify: jsdom vitest rendering the real page with stubbed fetch (week + events render, forms wire to typed endpoints, empty/error).
- [ ] Phase 8 — Harness `api/test_calendar_wu13.py` on NEW base `production_runtime_mvp_wu13` (runner 001–006 + both seeds; events created through the real commands, never hand-inserted).
  - Verify: C1–C12 PASS matrix, run twice for determinism, exit 0.
- [ ] Phase 9 — Regression + evidence: `python -m compileall -q api infra`; WU3–WU12 harnesses on their documented bases; `npm.cmd run typecheck` + `npm.cmd test`; `next build`/`next start` browser or DOM check with identical `NEXT_PUBLIC_*` env; write `work-unit-13-evidence.md`; status "implemented, pending review"; no `tasks.md` box checked without a real run behind it.

## Explicitly Out of Scope

Per-advisor calendars, per-account business hours, reminders/notifications, recurrence, multi-timezone, Google/Outlook sync, hard delete, agent-driven events (`source=agent` reserved for WU14), roles/permissions, RLS, productive auth, Docker, CI/CD, touching bases wu2–wu12.
