# Work Unit: Embudo comercial (gestión de pipelines y etapas)

Estado: planificado, no aceptado aún. Unidad sucesora de `configuration` (WU17) y `agent-custom-field-writes` (WU18); ocupa un lugar WU19 aproximado. Decisión de owner 2026-10-16.

## Contexto

El Embudo de hoy es solo lectura: `CrmReadService.pipeline` (`api/queries/crm_reads.py:107`) arma la vista desde `pipeline_views` (`api/adapters/postgres_crm_reads.py:163`, pipelines con etapas + conteo de prospectos) y la única ruta es `GET /v1/demo/pipeline` (`api/composition_wu8_mvp.py:320`); no existe ningún endpoint de alta, edición o baja de pipelines. La vista "Embudo" (`web/app/page.tsx:1473`, `ViewKey` `"pipeline"`) dibuja columnas kanban sin controles de creación o edición. Los pipelines y etapas semilla viven fijos en SQL: `mvp_seed.sql` crea `Ventas Aurea` con `Nueva consulta` (posición 0) y `Alquileres Litoral` con `Visita agendada` (posición 0), y `mvp_seed_008.sql` agrega `Calificado` (posición 1) por pipeline. El agente resuelve etapas por nombre dentro del pipeline del prospecto (`QUALIFY_STAGE_NAMES` / `VISIT_STAGE_NAME` en `api/application/conversation_agent.py:56-58`, resuelto por `_stage_id_for_name` en `api/routes/agent_wu14.py:500`): renombrar o borrar una etapa hoy rompería silenciosamente sus transiciones. El owner definió que el asesor debe gestionar sus propios embudos desde la UI, sin depender de seeds SQL.

## Objetivo

La vista "Embudo" gana gestión completa de pipelines y etapas por cuenta, toda por comandos atómicos (idempotencia + auditoría + outbox, alcance `client_account_id`):

1. **Crear pipeline**: alta de un embudo con nombre (validación de no vacío, como el `CHECK (btrim(name) <> '')` del DDL); nace con sus etapas iniciales definidas por el asesor.
2. **Renombrar pipeline**: edición del nombre; no mueve prospectos ni etapas.
3. **Agregar etapa**: alta al final (siguiente `position`) o en una posición dada, con reordenamiento de las demás.
4. **Renombrar etapa**: edición del nombre con resguardo del agente (ver Notas de diseño): no debe romper la resolución por nombre de `transition_lead`.
5. **Reordenar etapas**: cambio de `position` (botones subir/bajar primero; arrastrar y soltar después — ver Decisiones abiertas).
6. **Borrar etapa**: solo si ningún prospecto la referencia (cierre fallido, como el precedente de denegación por solape); con prospectos encima se deniega y la UI ofrece moverlos primero a mano.

## Notas de diseño

- Sin tablas nuevas: se reutilizan `pipelines` y `pipeline_stages` (`infra/migrations/004_commercial_crm.up.sql:43-72`). Los comandos viven en un módulo nuevo (p. ej. `api/application/pipeline_commands_wu19.py`) sobre el patrón `CrmCommandBase` (`api/application/crm_commands_wu5.py:87`: reserva de idempotencia → escritura con alcance → auditoría + outbox → commit). Las rutas nuevas cuelgan del montaje existente (mismo precedente que el simulador y la consola); la lectura `pipeline` aceptada no cambia.
- Borrado con prospectos: BLOQUEAR (fallo cerrado). La FK `leads_stage_pipeline_account_fk` ya es `ON DELETE RESTRICT`; el comando verifica el conteo antes y deniega con auditoría en vez de dejar que la FK hable. Mover prospectos automáticamente queda fuera: exige una UX de etapa destino que esta unidad no construye.
- Renombres frente al agente: el agente resuelve por nombre (`conversation_agent.py:56-58` + `agent_wu14.py:500-508`), así que renombrar `Calificado` o `Visita agendada` lo dejaría sin destino. Enfoque elegido: (a) el comando valida el renombre contra los nombres conocidos por el agente y advierte, y (b) cuando el destino del agente desaparece, el agente no propone `transition_lead` (escala, nunca adivina un id) — el comportamiento ya existe para nombres irresolubles (ver pruebas `U14` en `api/test_conversation_agent.py:261`).
- Orden: `position` (`integer >= 0`, `UNIQUE (pipeline_id, position)`). Crear anexa al final; reordenar renumera en una sola transacción. La lectura ya ordena por `position, id`, así que el kanban refleja el orden sin cambios.
- Convivencia con seeds: las filas demo llevan `demo_seed_key` (`demo-a-pipeline-001`, `demo-a-stage-001`, `demo-a-stage-calificado`, …); las filas creadas por el asesor llevan `demo_seed_key` NULL. El reset (`infra/migrations/validation/mvp_reset.sql:28-32`) borra solo por marcador + cuenta verificada, así que nunca toca filas de usuario. La etapa del simulador (`Simulador: visita calificada`, `api/routes/simulator_wu9.py:99-120`) también es NULL a propósito: el preclean del harness debe identificarla por nombre/posición, nunca por un borrado genérico de filas sin marcador.
- Sin etapas especiales esta unidad: sin etapa de entrada obligatoria ni semántica ganado/perdido. El estado ganado/perdido ya vive en `leads.status` (`won`/`lost`) y `ProcessInboundMessageCommand._first_stage` toma la primera etapa por orden (`crm_commands_wu5.py:428-442`); no hay supuesto de código que exija etapas reservadas.

## Fuera de alcance

Mover prospectos entre etapas desde el Embudo (sigue por `lead.transition` existente), arrastrar y soltar (botones primero), etapas de entrada/cierre con semántica especial, canal real (Kapso/WhatsApp), RLS/auth productiva, cambios en seeds demo existentes.

## Regresión

Instalación sin cambio de comportamiento: sin cambios a seeds existentes ni a superficies aceptadas WU5–WU18; el harness nuevo corre en base propia desechable (`production_runtime_mvp_wu19`, siguiendo el patrón `production_runtime_mvp_wu17`); los harnesses aceptados siguen verdes; el Embudo se ve igual hasta que el asesor crea o edita algo.
