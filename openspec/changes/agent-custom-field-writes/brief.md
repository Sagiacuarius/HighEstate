# Work Unit: Escritura de campos personalizados por el agente (autocompletado por señales)

Estado: planificado, no aceptado aún. Unidad sucesora inmediata de `configuration` (WU17). Misma metodología SDD.

## Contexto

La unidad `configuration` (WU17) entregó tres superficies por cuenta y dejó dos cosas diferidas a esta unidad:

1. Campos personalizados pasivos sobre leads: `custom_field_definitions` (nombre + tipo `CHECK IN ('text','number','select','date')` + `choices text[]` para `select`, `UNIQUE (client_account_id, name)`) y el almacén EAV `lead_custom_field_values` (una fila por `lead_id × field_id`), migración `010_configuration`. El comando `SetLeadCustomFieldValueCommand` (`configuration_commands_wu17.py`, operación `configuration.custom_field.value.set`) ya implementa la coerción/validación completa (`_coerce_value`: `number`→Decimal, `date`→ISO-8601, `select`∈`choices`, `text` acotado).
2. Ampliación de allowlist a nivel de schema: migración `010` agrega `agent_field_allowlist.field_definition_id` + FK compuesta `(field_definition_id, client_account_id) → custom_field_definitions (id, client_account_id)`, de modo que la lectura `load_field_allowlist` (`postgres_agent_console.py:114`) ya devuelve columnas fijas ∪ nombres custom.

Quedan dos deudas:

1. **D4 — la ruta de ESCRITURA de allowlist sigue cerrada**: `UpdateFieldAllowlistCommand` (`agent_commands_wu16.py:691`, operación `agent.allowlist.update`) aún valida contra `ALLOWLIST_COLUMNS = frozenset({"stage_id","status","selected_property_id"})` (línea 56) y su `_apply` inserta sólo `column_name` (sin `field_definition_id`). El asesor no puede autorizar qué campos custom puede tocar el agente.
2. **El agente no escribe campos custom**: `PROPOSED_ACTION_TYPES` (`agent_draft.py:23`) no tiene ningún tipo de acción para escribir valores custom, así que esos valores sólo los escribe el asesor (o futuras automatizaciones).

## Objetivo

Nueva unidad que habilita el autocompletado OPCIONAL de campos custom desde señales deterministas, respetando el carácter pasivo del campo y cerrando D4:

1. **Campo custom pasivo**: la definición (nombre/tipo/choices) nunca conoce de dónde sale su valor (ya es cierto en WU17 — confirmar y honrar). El autocompletado es un binding separado y opcional.
2. **Binding señal → campo, opcional y separado**: una señal (de un catálogo cerrado) → un campo custom, configurado en una NUEVA sección de consola "Fuentes del agente" (fuera de la definición del campo). Sin binding, el campo sólo lo llena el asesor o (futuras) automatizaciones.
3. **Productor-scoped, no field-scoped**: un valor de campo admite múltiples productores (asesor manual, agente por binding, futuras automatizaciones), así que el binding se alcance por productor.
4. **Catálogo cerrado de señales** (aprobado por el dueño): `phone`, `email`, `presupuesto_max`, `barrio`, `tipo_propiedad`, `fecha_objetivo`, `medio_de_contacto`. Extracción determinista; el modelo nunca escribe un valor libre.
5. **Nueva acción propuesta** `set_custom_field_value` que REUSA `SetLeadCustomFieldValueCommand` (coerción + validación de tipo + select∈choices ya existentes), sin bifurcarlo.
6. **Cerrar D4**: la ruta `PUT /agent/lead-fields` / `UpdateFieldAllowlistCommand` debe aceptar nombres custom (independiente del binding de señal).

## Notas de diseño

- Todo extiende superficies aceptadas de forma aditiva: patrón `CrmCommandBase`, saga de dispatch `_dispatch_actions` (`agent_wu14.py:336`), helpers de consola `_console_ok`/`_console_body`/`_console_key`, y el patrón de migración `010` → `011` (siguiente número libre).
- La extracción de señales es determinista y cerrada; análoga a la regla de grounding de `send_reply` (sólo texto derivado de KB/catálogo), el valor nunca sale de texto libre del modelo.
- La nueva acción entra en la tabla de veredicto de `agent_policy.evaluate(...)` y en el CHECK de `agent_policy_matrix.action_type` (hoy cerrado a los 7 tipos reales) — migración `011` amplía ese CHECK.
- Cero cambio de comportamiento al instalar: sin bindings sembrados, el agente no escribe ningún campo custom.

## Fuera de alcance

Automatizaciones en sí (roadmap 4), free-writes del modelo, edición de superficies aceptadas más allá de los dos CHECK declarados (allowlist + matrix de política), canales reales (Kapso/WhatsApp), RLS/auth productiva.

## Regresión

No debe tocar superficies aceptadas WU5–WU17; los harnesses del motor (`test_agent_autonomy_wu15`), de la consola (`test_agent_console_wu16`) y de configuración (`test_configuration_wu17`) siguen verdes; sin bindings, el comportamiento del agente es idéntico.
