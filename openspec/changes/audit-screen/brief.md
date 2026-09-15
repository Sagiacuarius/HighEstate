# Work Unit: Pantalla de Auditoría (exponer audit log existente)

## Contexto

El audit ya se captura (WU5 comandos atómicos + WU6 audit/outbox, tabla
`audit_records` de la migración 003). Lo que falta es EXPONERLO, no capturarlo:
una read API con filtros + una vista en el dashboard. NO crear tablas nuevas,
NO escribir datos nuevos.

`audit_records` ya tiene: `actor_id`, `agency_id`, `client_account_id`,
`action`, `resource_type`, `resource_id`, `outcome`, `correlation_id`,
`idempotency_key`, `metadata`, `created_at`.

## Objetivo

1. Read API sobre el audit log, tenant-scoped (predicado `client_account_id`),
   con filtros:
   - `contact_id`  (mapeo: audit → leads → contacts, o resource directo si existe)
   - `property_id` (mapeo: resource_type='property' + resource_id, o JOIN)
   - `user_id`     (actor_id)
   - `from` / `to` (rango sobre created_at)
   - `action`      (string de acción, ej. crm.inbound.process)
   - `limit` + cursor/pagination (el log crece)
   Las rutas NO aceptan account id del cliente; scope server-resolved.

2. Vista "Auditoría" en el dashboard (o enriquecer la vista "Actividad" actual):
   - tabla con action, outcome, resource, actor, fecha, correlation;
   - filtros por contacto, propiedad, usuario, rango de fechas, tipo de acción;
   - valores de estado traducidos al español con el `statusLabel` existente;
   - estados vacío/error con envelope, correlación consistente.

## Notas de diseño

- El mapeo "por contacto" y "por propiedad" depende de qué guarda el audit
  como `resource_type`/`resource_id` (hoy: leads, properties, conversations,
  tenant_resource). Definir el JOIN exacto en design; no inventar columnas.
- `metadata` es jsonb con redaction; NO exponer bodies/secretos. Reusar la
  redaction existente.
- El filtro "por usuario" es trivial en la demo (un solo demo-user), pero el
  contrato debe soportarlo (actor_id).

## Rol / permisos

Esta primera versión NO depende de roles: es una vista para el operador.
Cuando exista el Bloque 5 (roles), se agrega el permiso por encima, sin
rehacer la vista.

## Valor

- Interno: revisar el audit log semanalmente durante el piloto.
- Ventas: mostrar el historial completo de una conversación a un interesado
  (señal de seriedad frente a un bot de WhatsApp genérico).

## Fuera de alcance

- No roles/permisos todavía.
- No escritura ni nueva captura de audit.
- No export CSV ni reportes agregados (solo listado filtrado).
- No Kapso/LLM/RLS/auth productiva.

## Regresión

- typecheck, npm test, compileall, harness Python WU3–WU11 sin regresión.
- Base demo existente (no crear migración nueva si no hace falta; esto es
  solo read + UI).

No declarar aceptación; crear work-unit-12-evidence.md (o el cambio/archivo
que corresponda según se arme como cambio nuevo o extensión del MVP).
