# Work Unit: Configuración (timezone, campos personalizados y etiquetas)

Estado: planificado, no aceptado aún. Unidad sucesora inmediata de `agent-console` (consola). Es la fila "Configuración" del roadmap (`README.md`); misma metodología SDD.

## Contexto

La consola del agente (`agent-console`) dejó tres puntos explícitamente diferidos a esta unidad:

1. La zona horaria de negocio sigue hardcodeada en `BUSINESS_TIMEZONE = "America/Argentina/Buenos_Aires"` (`api/domain/calendar_event.py:21`). `agent_calendar_config` (migración 009) ya guarda ventana/día/turno pero NO timezone, y `load_calendar_config` lo fuerza a la constante (`api/adapters/postgres_agent_console.py:152-154`).
2. La allowlist de campos del prospecto (`agent_field_allowlist`) tiene un `CHECK` de enumeración cerrada `('stage_id','status','selected_property_id')` (migración `009_agent_console.up.sql:71`), así que no puede referenciar campos personalizados que todavía no existen sobre `leads` (que hoy solo tiene columnas fijas, sin notas ni custom — `004_commercial_crm.up.sql`).
3. No hay etiquetas (tags) asociables a leads/propiedades: `contacts` ya tiene `tags text[]` (`004_commercial_crm.up.sql:14`), pero `leads` y `properties` no.

## Objetivo

Nueva unidad "Configuración" que convierte tres superficies fijas en configuración por cuenta, toda por comandos atómicos (idempotencia + auditoría + outbox, alcance `client_account_id`):

1. **Zona horaria por cuenta**: agregar timezone a `agent_calendar_config` y hacer que `business_slot_starts`/`free_slots` la consuman, con las constantes `BUSINESS_*` como fallback.
2. **Campos personalizados de leads**: definiciones por cuenta (`custom_field_definitions`) + almacén de valores (`lead_custom_field_values`) y extensión de la allowlist para que `agent_field_allowlist` pueda nombrar campos custom (hoy el CHECK es un enum cerrado).
3. **Etiquetas (tags)**: tabla `tags` + joins `lead_tags`/`property_tags` (o equivalente) y CRUD desde la consola.

## Notas de diseño

- Cero cambio de comportamiento al instalar: timezone por defecto = `BUSINESS_TIMEZONE`, allowlist por defecto = `{stage_id, status}`, sin campos custom ni tags sembrados.
- Todo extiende superficies aceptadas de forma aditiva: la unidad consume el patrón settings-row de la 009 (`agent_settings`/`agent_calendar_config`/`agent_policy_matrix`) y el patrón de comandos `CrmCommandBase` (reserve → write → audit + outbox → commit).
- El patrón de seeds replica `mvp_seed_008.sql`/`mvp_seed_009.sql` (advisory lock, ownership gate, DELETE-then-INSERT de marcadores demo-a/b).
- Los campos custom alimentan la allowlist de la consola; si el agente los escribe o no es decisión abierta (ver proposal).

## Fuera de alcance

Canal real (Kapso/WhatsApp), RLS/auth productiva, Docker/CI/CD, Google Calendar sync, versionado de schema de campos custom con migración de datos históricos, etiquetas en conversaciones/mensajes.

## Regresión

No debe tocar superficies aceptadas WU5–WU16; los harnesses del motor (`test_agent_autonomy_wu15`) y de la consola (`test_agent_console_wu16`) siguen verdes; la demo funciona igual sin abrir Configuración.
