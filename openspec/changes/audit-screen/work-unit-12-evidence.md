# Work Unit 12 Evidence: Audit Screen (change: audit-screen)

Status: **accepted** — Leo verified independently (compileall, typecheck, vitest 76/76, harness 13/13, regresión WU11/WU10/WU6) contra PostgreSQL 18.6 real.

## Scope

Read-only audit screen over the accepted `audit_records` table (migración 003): una read API `GET /v1/demo/audit` con filtros (contacto, propiedad, usuario, acción, rango de fechas) y keyset pagination, más una vista "Auditoría" en el dashboard. Sin migración nueva, sin writes, sin tocar `/v1/demo/activity`.

## Archivos

- `api/adapters/postgres_audit_screen.py` — SQL read-only con predicado `client_account_id` y filtros parametrizados.
- `api/queries/audit_screen_wu12.py` — validación, cursor opaco, proyección redacted.
- `api/routes/audit_screen_wu12.py` — router APIRouter montado en la app WU8 (aditivo).
- `contracts/runtime-v1.ts` + `.test.ts` — `CrmAuditResponse`/`AuditEntry` + parser estricto.
- `web/app/demo-api.ts` + `.test.ts` — cliente `audit(filters)`.
- `web/app/page.tsx` + `demo.css` + `audit-screen.test.tsx` — vista "Auditoría" + barra de filtros.
- `api/test_audit_screen_wu12.py` — harness real.

## Decisiones implementadas

- **Acción traducida** (no cruda) vía `statusLabel`, incluidos los mapeos de WU11:
  `crm.property.create` → "Propiedad creada", `crm.property.update` → "Propiedad actualizada",
  `crm.property.status_change` → "Cambio de estado de propiedad",
  `crm.property.link_contact` → "Contacto vinculado a propiedad",
  `crm.property.register_interest` → "Interés registrado".
- Filtros foreign/ghost → lista vacía 200 (no-enumeración); malformed → 400.
- Keyset `(created_at, id)` con cursor opaco + `limit+1` para `has_more`.
- Redaction: solo acción/outcome/resource/actor/correlación/idempotency/reason-clase; nunca `metadata` completo ni bodies.
- Sin cache (un audit view stale es peor que lento).

## Verificación (PostgreSQL 18.6 real)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api infra` | exit 0 |
| `npm.cmd run typecheck` | exit 0 |
| `npm.cmd test` | 9 files / **76 tests** passed |
| `python -m api.test_audit_screen_wu12` | **13/13** passed (base `production_runtime_mvp_wu12`) |
| `python -m api.test_property_catalog_wu11` | 16/16 (regresión) |
| `python -m api.test_e2e_mvp` | 12/12 (regresión) |
| `python -m api.test_crm_reads` | 13/13 (regresión) |

## Limitaciones

- Sin roles/permisos (vista operadora; el permiso se agrega cuando exista el Bloque 5).
- Sin export CSV ni reportes agregados.
- El mapeo contact/property se define vía `EXISTS(leads)` o `metadata->>'contact_id'` y `resource_type IN (property, property_interest)` o `lead.selected_property_id`; los audits de conversación/mensaje no son mapeables a propiedad (documentado, no inventado).

## WACRM

Referencia auditada únicamente; sin fork, copia ni dependencia.
