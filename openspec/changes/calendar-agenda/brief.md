# Work Unit: Calendario / Agenda (eventos + disponibilidad + CRUD manual)

## Contexto

Hoy la "agenda" del dashboard (WU6 `reads.agenda`) es solo una lectura de
`follow_ups` agrupados por fecha: no hay entidad "evento", ni disponibilidad,
ni forma de crear/editar/cancelar citas. El siguiente paso antes del agente es
un **calendario real** con el que el asesor gestione visitas manualmente, y que
luego el agente (WU14) use para crear/reagendar.

## Objetivo

1. Entidad `CalendarEvent` + tabla `calendar_events` (migración 006):
   `id, client_account_id, title, description, starts_at, ends_at, contact_id,
   lead_id, property_id, status, source, created_at, updated_at`.
   - `status ∈ {scheduled, cancelled, completed}`.
   - `source ∈ {manual, agent}` (agente cuando venga de un draft aprobado en WU14).
2. Comandos atómicos (patrón `CrmCommandBase`: idempotencia + audit + outbox):
   - `CreateCalendarEventCommand` (crear, manual/agente).
   - `UpdateCalendarEventCommand` (actualizar campos/hora → reagendar manual).
   - `CancelCalendarEventCommand` (cancelar → `cancelled`; "eliminar" es soft, no hard).
3. Reads:
   - `GET /v1/demo/calendar/events?from&to` — listar eventos en rango.
   - `GET /v1/demo/calendar/availability?from&to` — franjas libres.
4. Disponibilidad determinista (**Opción A**):
   - Horario comercial: **Lun–Sáb 09:00–18:00**, franjas de **60 min**.
   - Libre = franjas del horario comercial menos las que solapan eventos
     `scheduled`/`completed` (los `cancelled` no ocupan).
5. UI: vista "Calendario" (semanal), con crear/editar/cancelar evento manual.

## Notas de diseño

- Los eventos se guardan en UTC (`timestamptz`); la disponibilidad y la UI
  trabajan en una zona fija (`BUSINESS_TIMEZONE`, default
  `America/Argentina/Buenos_Aires`), documentada como simplificación de demo.
- `source=agent` queda reservado para WU14; en WU13 todo es `manual`.
- El solapamiento se valida en el comando (no dos eventos `scheduled` del mismo
  asesor en el mismo rango); cancelar no es un DELETE físico (audit).
- La disponibilidad es una read determinista, sin cache (correctitud > latencia).

## Fuera de alcance

- No calendario por-asesor (un solo asesor demo).
- No horarios por cuenta (config global de demo).
- No recordatorios/notificaciones, no recurrencia, no multi-timezone.
- No hard-delete de eventos.
- No integración con Google/Outlook Calendar.

## Regresión

- `typecheck`, `npm test`, `compileall`, harness WU3–WU12 sin regresión.
- Base demo nueva `production_runtime_mvp_wu13` (runner 001–006 + seeds).
- No declarar aceptación; crear `work-unit-13-evidence.md`.
