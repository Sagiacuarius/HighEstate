# Work Unit 13 Evidence: Calendar / Agenda (change: calendar-agenda)

Status: **implemented, pending review** — verified directly (compileall, typecheck, vitest 79/79, harness 22/22 twice, regresión WU12/WU11/WU10/WU6) contra PostgreSQL 18.6 real.

## Scope

Calendario real: entidad `calendar_events` (migración 006) con comandos atómicos crear/actualizar/cancelar (soft-delete), disponibilidad determinista (horario comercial Lun–Sáb 9–18, franjas 60 min, libre = horas − eventos no cancelados), reads `GET /v1/demo/calendar/events` + `/availability`, y vista "Calendario" en el dashboard con CRUD manual. Base del agente WU14 (que agendará vía estos comandos/reads). Sin tocar WU5/WU6/WU9/WU11/WU12.

## Archivos

- `infra/migrations/006_calendar_events.up.sql` / `.down.sql` — tabla + índices + CHECKs + FKs scope-pinned.
- `api/domain/calendar_event.py` — `CalendarEvent`, enums, `BUSINESS_*`, `business_slot_starts`, `overlaps`.
- `api/adapters/postgres_calendar.py` — `CalendarEventRepository` + `OVERLAP_SQL` compartido.
- `api/application/calendar_commands_wu13.py` — `CreateCalendarEventCommand`, `UpdateCalendarEventCommand`, `CancelCalendarEventCommand`.
- `api/queries/calendar_reads_wu13.py` — `CalendarReadService` (list + availability).
- `api/routes/calendar_wu13.py` — router montado aditivo en WU8.
- `api/composition_wu8_mvp.py` — un `include_router` (WU13).
- `contracts/runtime-v1.ts` + `.test.ts` — `CalendarEventsResponse`/`AvailabilityResponse`/`CalendarCommandResponse` + parsers estrictos.
- `web/app/demo-api.ts` — `calendarEvents`/`calendarAvailability`/`createCalendarEvent`/`updateCalendarEvent`/`cancelCalendarEvent`.
- `web/runtime-client.ts` — helpers `put`/`delete` (body opcional en `call`).
- `web/app/page.tsx` — vista "Calendario" (grilla mensual con crear inline por celda, editar y cancelar por chip).
- `web/app/demo.css` — estilos de grilla (`calendar-grid`, `calendar-cell`, `calendar-chip`, inline form).
- `api/test_calendar_wu13.py` — harness real.

## Decisiones implementadas

- **Cancel = soft-delete** (`status='cancelled'`); la fila persiste y audita. La lista por defecto excluye cancelados (`include_cancelled` opt-in).
- **Overlap half-open** `[starts_at, ends_at)`; crear/actualizar validan en transacción con `FOR UPDATE` (lost-update safe). Cancelados no ocupan.
- **Zona horaria**: UTC en base; disponibilidad y UI en `BUSINESS_TIMEZONE` fijo (`America/Argentina/Buenos_Aires`). Migra a setting en WU15.
- **Update**: solo `scheduled→completed` vía `status`; `cancelled` es terminal y no admite update (usar Cancel). Referencias opcionales con sentinel `_UNSET` (ausente = no cambia).
- **UI en grilla mensual**: cada celda muestra sus eventos; "+" crea inline (título + horas, fecha fija a la celda); "×" en el chip cancela; clic en el chip edita (formulario para mover de fecha). Google Calendar sync queda como futuro (requiere `external_id`, no implementado).
- **Idempotencia**: las tres operaciones reservan `(actor, operation, key)`; replay devuelve el resultado guardado sin efectos extra.
- **Disponibilidad**: un query de eventos no-cancelados en rango + filtrado en memoria con `business_slot_starts` + `overlaps`; el harness la valida contra un oracle independiente.

## Verificación (PostgreSQL 18.6 real)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api infra` | exit 0 |
| `npm.cmd run typecheck` | exit 0 |
| `npm.cmd test` | 9 files / **79 tests** passed (76 + 3 calendar) |
| `python -m api.test_calendar_wu13` | **22/22** passed, dos corridas consecutivas (base `production_runtime_mvp_wu13`) |
| `python -m api.test_audit_screen_wu12` | 13/13 (regresión) |
| `python -m api.test_property_catalog_wu11` | 16/16 (regresión) |
| `python -m api.test_e2e_mvp` | 12/12 (regresión) |
| `python -m api.test_crm_reads` | 13/13 (regresión) |
| `npm.cmd --workspace web run build` | exit 0 |

C1–C12 + 401/404/405: scope tenant + B invisible, 400 validación (6 casos + orden invertido), update/reprogramar, overlap 403 + no-overlap ok, cancel soft + lista filtra, replay crear/actualizar/cancelar, rango, oracle disponibilidad 8/9 + cancel libera 9/9, audits `crm.calendar.*` visibles, A/B isolation, agenda WU6 intacta.

## Limitaciones

- Sin calendario por-asesor (un solo asesor demo); sin horarios por cuenta (config global).
- Sin recordatorios, recurrencia, multi-timezone, sync externo.
- Sin hard-delete; sin `GET` por id (solo listado por rango).
- `source=agent` reservado para WU14 (WU13 escribe `manual`).

## WACRM

Referencia auditada únicamente; sin fork, copia ni dependencia.
