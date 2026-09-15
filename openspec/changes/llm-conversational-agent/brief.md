# Work Unit: Agente Conversacional (LLM) — interpretar, matchear, proponer, agendar

## Contexto

Ya está el esqueleto completo del CRM: catálogo (WU11), comandos atómicos
(WU5), inbound + simulador (WU4/WU9), reads (WU6), dashboard (WU8), auditoría
(WU12) y **calendario/agenda (WU13)**: entidad `calendar_events`, comandos
`CreateCalendarEventCommand`/`UpdateCalendarEventCommand`/`CancelCalendarEventCommand`
y el read de disponibilidad (horario comercial Lun–Sáb 9–18, franjas de 60 min).

Lo que falta es la INTELIGENCIA. Hoy el inbound effect es una regla FIJA. El
agente (este WU) interpreta el mensaje con un LLM y propone acciones —incluido
**agendar**: ver disponibilidad, crear evento y reagendar— como borrador que el
asesor confirma inline.

## Objetivo

1. Puerto `ModelProvider` con dos adaptadores: `OpenCodeGoProvider` (HTTP
   OpenAI-compatible a `https://opencode.ai/zen/go/v1/chat/completions`, modelo
   `deepseek-v4-pro`, auth `Bearer <OPENCODE_GO_API_KEY>`) y `StubModelProvider`
   (determinista, para tests y demo sin red). La key se agrega al final.
2. Persona de primera clase (`agent_persona.py`): identidad "Sofía", rol
   asesora inmobiliaria, tono cordial/profesional/empático, español neutro,
   guardrails (no confirma disponibilidad ni agenda por su cuenta; propone y
   deriva al asesor).
3. `ConversationAgent` que, dado un mensaje inbound + contexto + catálogo +
   **disponibilidad (WU13)**, produce un **draft**:
   - `intent` (search_properties, schedule_visit, **reschedule**, ask_question,
     update_preferences, general).
   - `criteria` (datos extraídos + preferencia de fecha/hora).
   - `matched_property_ids` (búsqueda determinista).
   - `proposed_actions` (mapeo determinista: register_interest, create_follow_up,
     transition_lead, **create_event, reschedule_event**).
   - `reply_text` (borrador, moldeado por la persona).
4. Draft persistido (`agent_drafts`, **migración 007**), status `proposed`, auditado.
5. Confirmación inline: aprobar ejecuta vía comandos existentes (WU5/WU11/WU13);
   rechazar no ejecuta nada.
6. Reads: `GET /v1/demo/agent/drafts` y `GET /v1/demo/agent/drafts/{id}`.

## Notas de diseño

- **El LLM NO toca la base.** Interpreta y extrae; el matching, la elección de
  franja libre y la ejecución son deterministas (usa el read de disponibilidad y
  los comandos de WU13).
- Para agendar: el agente consulta disponibilidad (WU13), elige la mejor franja
  (preferencia del lead o la primera libre) y propone `create_event` /
  `reschedule_event`. La creación real del evento usa `CreateCalendarEventCommand`
  con `source=agent`, y el reagendado usa `UpdateCalendarEventCommand`.
- La ejecución reusa comandos aceptados; no se duplica SQL.
- El `reply_text` es borrador; NO se envía automáticamente.

## Fuera de alcance

- No WhatsApp/Kapso real; no auto-envío del reply.
- No memoria multi-turno más allá del mensaje + contexto del contacto/lead.
- No roles/permisos nuevos.
- No se modifica el inbound effect determinista de WU5 ni los comandos/reads de
  WU13 (el agente los consume).

## Regresión

- `typecheck`, `npm test`, `compileall`, harness WU3–WU13 sin regresión.
- Base demo nueva `production_runtime_mvp_wu14` (runner 001–007 + seeds).
- No declarar aceptación; crear `work-unit-14-evidence.md`.
