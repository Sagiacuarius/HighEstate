# MVP Commercial Demo — Work Unit 2 Evidence

## Status

Se corrigió `004_commercial_crm.up.sql` y se validó realmente contra PostgreSQL. **No se declara aceptación.**

## Proyecto verificado

```text
C:\Users\Windows\Documents\AI Agency Platform
```

No se modificaron las migraciones 001–003, PDR, proposal, design, specs, Work Units anteriores ni WACRM.

## Corrección aplicada

`pipeline_stages` tenía una FK referenciada por:

```text
(id, pipeline_id, client_account_id)
```

Se agregó la constraint requerida:

```sql
UNIQUE (id, pipeline_id, client_account_id)
```

La FK existente ahora queda respaldada por una constraint `UNIQUE` válida:

```sql
FOREIGN KEY (id, pipeline_id, client_account_id)
REFERENCES pipeline_stages (id, pipeline_id, client_account_id)
```

La migración 004 fue el único archivo de esquema modificado.

## Base limpia

Se creó y utilizó una base separada:

```text
production_runtime_mvp_wu2
```

Servidor:

```text
PostgreSQL 18.6
Host: 127.0.0.1
Port: 5432
User: postgres
```

No se utilizó ni destruyó `production_runtime_local`.

## Runner de migraciones

Comando exacto:

```bash
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_mvp_wu2 RUNTIME_DB_USER=postgres python infra/migrations/runner.py --up
```

Resultado:

```text
applied 001 (001_initial_tenancy.up.sql)
applied 002 (002_command_idempotency.up.sql)
applied 003 (003_audit_outbox.up.sql)
applied 004 (004_commercial_crm.up.sql)
RUNNER_UP_EXIT=0
```

Confirmación: migrations 001, 002, 003 y 004 aplicaron sin errores contra PostgreSQL real.

## Seed, reset y reapply

Seed inicial:

```text
SEED_EXIT=0
```

Seed repetido:

```text
SEED_REPEAT_EXIT=0
```

Reset:

```text
RESET_EXIT=0
crm_after_reset=0
```

Reapply del seed:

```text
REAPPLY_SEED_EXIT=0
crm_after_reapply=16
```

Reaplicación del runner:

```bash
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_mvp_wu2 RUNTIME_DB_USER=postgres python infra/migrations/runner.py --up
```

Resultado:

```text
RUNNER_REAPPLY_EXIT=0
```

El runner no volvió a ejecutar migraciones ya registradas en `migration_ledger`.

## Escenarios de integridad CRM

Script ejecutado:

```bash
psql -f infra/migrations/validation/mvp_integrity.sql
```

Cada intento inválido se ejecutó dentro de una transacción y terminó con `ROLLBACK`.

### Relaciones cross-account

```text
Lead/Contact → FK leads_contact_account_fk rechazó
Lead/Property → FK leads_property_account_fk rechazó
Lead/Stage/Pipeline → FK leads_stage_pipeline_account_fk rechazó
Conversation/Contact → FK conversations_contact_account_fk rechazó
Message/Conversation → FK messages_conversation_account_fk rechazó
FollowUp/Lead → FK follow_ups_lead_account_fk rechazó
FollowUp/Contact → FK follow_ups_contact_account_fk rechazó
```

Mensajes reales de PostgreSQL incluyeron:

```text
viola la llave foránea «leads_contact_account_fk»
viola la llave foránea «leads_property_account_fk»
viola la llave foránea «leads_stage_pipeline_account_fk»
viola la llave foránea «conversations_contact_account_fk»
viola la llave foránea «messages_conversation_account_fk»
viola la llave foránea «follow_ups_lead_account_fk»
viola la llave foránea «follow_ups_contact_account_fk»
```

### Unicidades

```text
contacts_demo_seed_key_unique → duplicate rejected
pipeline_stages_pipeline_position_unique → duplicate rejected
```

Mensajes reales:

```text
llave duplicada viola restricción de unicidad «contacts_demo_seed_key_unique»
llave duplicada viola restricción de unicidad «pipeline_stages_pipeline_position_unique»
```

### NOT NULL

```text
properties.client_account_id = NULL → rejected
```

Mensaje real:

```text
el valor nulo en la columna «client_account_id» ... viola la restricción “not-null”
```

### Caso válido entre cuentas

La misma etiqueta/nombre puede existir en Account A y Account B dentro de una transacción aislada:

```text
same_name_rows=2
```

La transacción fue rollbackeada después de verificar el resultado.

### Contaminación final

```text
invalid_leads=0
invalid_contacts=0
INTEGRITY_EXIT=0
```

## Verificaciones obligatorias

### TypeScript typecheck

```text
npm.cmd run typecheck
exit code: 0
```

### Tests TypeScript

```text
npm.cmd test
exit code: 0
Test Files  6 passed (6)
Tests       39 passed (39)
Start at 23:01:26
Duration 3.48s
```

La suite incluye los contratos previos del MVP y no crea endpoints ni UI.

### Python compileall

```text
python -m compileall -q api
COMPILE_EXIT=0
```

## Archivos modificados

```text
infra/migrations/004_commercial_crm.up.sql
infra/migrations/004_commercial_crm.down.sql
infra/migrations/runner.py
infra/migrations/validation/mvp_seed.sql
infra/migrations/validation/mvp_reset.sql
infra/migrations/validation/mvp_integrity.sql
openspec/changes/mvp-commercial-demo/work-unit-2-evidence.md
```

Rev-2 agregó los gates fail-closed en `mvp_seed.sql`/`mvp_reset.sql` y el chequeo de prerequisitos y docstring de checksums en `runner.py`. El script `mvp_integrity.sql` fue corregido únicamente en su chequeo final de contaminación: el filtro inicial incluía IDs válidos del seed. La validación final ahora comprueba sólo los IDs de prueba y devuelve cero contaminación.

## No implementado

No se implementó:

```text
repositories CRM
endpoints de producto
UI
auth productiva
OAuth
MFA
RLS definitivo
Docker
CI/CD
VPS
WACRM
WhatsApp
CRM runtime
agentes
automatizaciones
reuniones
formularios
```

No se declara aislamiento de aplicación ni producción lista. Esta ejecución prueba integridad estructural PostgreSQL de la migración 004 y el comportamiento de seed/reset, no autorización de aplicación.

## Revisión WU2 rev-2: ownership de seed/reset y runner

### `mvp_seed.sql` (fail-closed ownership)

Antes de crear o modificar cualquier fila:

- Gate de `ClientAccount`: si el ID designado existe con `demo_seed_key` NULL o distinto del esperado (`demo-account-a`/`demo-account-b`), el seed aborta con excepción explícita, sin reclamar ni sobrescribir la cuenta y sin borrar datos.
- Gate de entidades CRM: cada fila designada (id + marker exactos para las 16 filas demo) debe, si ya existe, pertenecer al `client_account_id` esperado y llevar el `demo_seed_key` exacto; si un marker está en poder de otra fila, se aborta antes de cualquier DELETE/INSERT.
- Las cuentas designadas sólo se crean cuando no existen (`ON CONFLICT DO NOTHING`); nunca se reescribe un marker ajeno.
- La convergencia del re-apply borra únicamente pares exactos `(client_account_id, demo_seed_key)` de las 16 filas demo, en la misma transacción con advisory lock `mvp-commercial-demo-seed-reset`.

Escenarios ejecutados contra PostgreSQL real:

```text
E1 cuenta existe con marker NULL
  -> ERROR: designated client account ... exists with an incompatible or missing ownership marker; refusing to claim it
  -> marker_still_null=true (cuenta intacta, sin cambios)

E2 cuenta existe con marker 'foreign-marker'
  -> seed rechazado (mismo mensaje explícito)
  -> reset rechazado: target demo accounts are absent or their ownership markers do not match demo-account-a/b; refusing to reset
  -> crm_rows_untouched=16 (ningún borrado)

E3 marker restaurado a 'demo-account-a'
  -> reset válido: EXIT=0
  -> crm_after_reset=0 (sólo filas demo eliminadas)
  -> accounts_kept=2 (las ClientAccounts no se tocan)

Seed repetido tras reset
  -> SEED_EXIT=0, SEED_REPEAT_EXIT=0
  -> crm_rows_after_repeat=16 (sin duplicados: 8 tablas x 2 filas)
```

### `mvp_reset.sql`

- Verifica primero que ambas cuentas objetivo existan con el marker exacto `demo-account-a`/`demo-account-b`; si falta o no coincide, aborta sin borrar nada.
- Elimina sólo filas demo marcadas pertenecientes a esas `ClientAccounts` (par `(client_account_id, demo_seed_key)`).
- Todo dentro de una transacción con el mismo advisory lock, concurrente y account-safe.

### `runner.py`

- Antes de aplicar 004 verifica en `migration_ledger` que 001, 002 y 003 estén registradas; si falta alguna falla sin aplicar 004: `refusing to apply 004: migration_ledger is missing 002`.
- Mantiene orden numérico (`sorted(ROOT.glob("*.up.sql"))`).
- Verificación gate real (transacción rollbackeada, sin tocar el ledger persistente):

```text
GATE_WITH_ALL_PREREQS=PASSED_OK
GATE_MISSING_002_REJECTED_OK: refusing to apply 004: migration_ledger is missing 002
```

- Checksums de archivos ya aplicados quedan explícitamente fuera del runner MVP y están documentados en el docstring de `require_prerequisites`.

### Runner en base fresca tras la revisión

```text
applied 001 / applied 002 / applied 003 / applied 004
RUNNER_UP_EXIT=0
ledger=001,002,003,004
```

### Integridad CRM re-verificada tras los cambios

```text
7 FK cross-account rechazadas
2 unicidades rechazadas
1 NOT NULL rechazado
same_name_residual=0
invalid_test_rows=0
crm_total=16
```

### Typecheck / tests / compileall tras la revisión

```text
npm.cmd run typecheck → exit 0
npm.cmd test → 6 files, 39/39 tests passed
python -m compileall -q api → COMPILE_EXIT=0
```

## Limitaciones

- La validación se realizó contra PostgreSQL real 18.6 local.
- No prueba repositories ni aislamiento de aplicación.
- La migración down (`--down 004`) continúa sin ejecutarse en este ciclo; permanece operator-reviewed y destructivo, bloqueado para esta revisión hasta que el reviewer lo autorice explícitamente.
- No se modificaron migraciones 001–003.
- El runner no verifica checksums de archivos ya aplicados (decisión MVP documentada).

## Acceptance status

La FK compuesta de `pipeline_stages` fue corregida y `001–004` aplicaron realmente con `RUNNER_UP_EXIT=0`.

La integridad CRM, seed repetido, reset y reapply fueron verificados en PostgreSQL real.

**Work Unit 2 del MVP Commercial Demo queda aceptado para la migración 004 y seed/reset deterministas.** El down destructivo (`--down 004`) continúa operator-reviewed y no fue ejecutado; repositories, endpoints, UI y aislamiento de aplicación quedan para work units posteriores.
