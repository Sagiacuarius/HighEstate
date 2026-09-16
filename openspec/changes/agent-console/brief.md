# Work Unit: Consola del Agente (panel de administración)

Estado: planificado, no aceptado aún. Unidad sucesora inmediata de `agent-autonomy` (motor). Decision de owner 2026-09-16.

## Contexto

El change `agent-autonomy` deja al agente funcionando de forma automática, pero con las palancas fijas en código o en seeds SQL: la política vive en constantes de `agent_policy.py`, el prompt en `api/application/agent_persona.py`, la base de conocimiento solo tiene articles semilla, el calendario es el hardcodeado de WU13 y la clasificación toca stages por nombre. El owner definió que administración de todo eso debe ser una consola propia, no superficies dev-only.

## Objetivo

Nuevo item de navegación "Agente" en la barra lateral izquierda (misma convención de vistas que Bandeja/Leads/Propiedades/Calendario/Auditoría), con estas secciones:

1. **Configuración del agente**: settings persistidos por cuenta (no por código): qué acciones ejecuta solo (matriz AUTO/ESCALATE por tipo de acción, hoy fija en `agent_policy`), interruptor auto global y por conversación (hoy `agent_mode` con endpoint dev-only), y kill-switch de calendario (hoy la constante `AUTO_EVENT_CREATION`).
2. **Redacción del prompt**: edición del prompt de sistema de la persona con versionado y auditoría. Skeleton de seguridad inmutable (nunca inventar datos, handoff ante reclamos, límites de schema) más una capa editable de tono/instrucciones; preview del prompt final compuesto.
3. **Base de conocimiento**: CRUD completo de `knowledge_base_articles` (alta/edición/baja, categorías, keywords, respuesta), reemplazando la dependencia del seed SQL para mantener la KB. Búsqueda y prueba de recuperación desde la UI (pregunta de prueba → article matcheada o "escaladoría").
4. **Vinculación a calendario**: configuración del calendario destino del agente (horario comercial, franja, día/s de atención; qué calendario usa al agendar). La sincronización externa (Google Calendar) queda explícitamente fuera hasta que exista `external_id`. La interpretación exacta de este punto se confirma en el proposal.
5. **Campos actualizables del Prospecto**: allowlist de campos de `leads` que el agente puede escribir (stage hoy; notas y demás campos propios de la ficha; campos custom dependen de la unidad Configuración — la consola debe mostrar el allowlist filtrado a lo que exista).

## Notas de diseño

- Depende del motor: consume las tablas y vocabulario de `agent-autonomy` (migración 008) y agrega sus propias tablas (p. ej. `agent_settings`, `agent_prompts` versionado, allowlist). Las superficies dev-only del motor (`POST /agent/state`) se promueven a la consola, sin romper endpoints aceptados.
- Todo escribe por comandos atómicos con idempotencia/auditoría/outbox como el resto del sistema; toda lectura scope-pinned por `client_account_id`.
- El prompt editable afecta solo al texto del modelo; las validaciones deterministas (schema fail-closed, grounded-reply, política) siguen siendo código inmutable.

## Fuera de alcance

Canal real (Kapso/WhatsApp), multi-idioma de persona, fine-tuning, embeddings/vector, edición de los guardrails del skeleton, Google Calendar sync, RLS/auth productiva.

## Regresión

No debe tocar superficies aceptadas WU5–WU14; los harnesses del motor (`test_agent_autonomy_wu15`) siguen verdes; la demo del inbox funciona igual sin abrir la consola.
