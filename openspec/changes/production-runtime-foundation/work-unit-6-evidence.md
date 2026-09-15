# Production Runtime Foundation — Work Unit 6 Evidence (final correction)

## Status

Se corrigieron los dos hallazgos finales de Work Unit 6 y se verificaron contra PostgreSQL real 18.6. **No se declara aceptación ni producción lista.**

## Scope

Sólo se corrigió dentro de Work Unit 6:

- auditoría durable de denegaciones producidas por el command boundary;
- retry real de publisher fallido;
- preservación de atomicidad success/replay/conflict/rollback;
- FKs compuestas Agency/ClientAccount;
- redaction profunda;
- contrato hexagonal de auditoría.

No se agregó RLS, auth productiva, Docker, CI/CD, WACRM, proveedores externos reales ni funcionalidad de producto.

## Archivos WU6 presentes

```text
infra/migrations/003_audit_outbox.up.sql
infra/migrations/003_audit_outbox.down.sql
api/adapters/postgres_audit_outbox.py
api/application/audit_outbox_ports.py
api/application/outbox_dispatcher.py
api/commands/audited_update_tenant_resource.py
api/test_audit_outbox.py
openspec/changes/production-runtime-foundation/work-unit-6-evidence.md
```

La comprobación física final confirmó:

```text
ARTIFACT_COUNT=8/8
```

El command/idempotency path previo utilizado por WU5 es:

```text
api/adapters/postgres_idempotency.py
```

No se mencionan rutas inexistentes.

## 1. Auditoría de denegaciones reales

`AuditedUpdateTenantResource.execute()` ahora registra una denegación real después de hacer rollback del intento rechazado. El registro se persiste en una transacción separada y no crea outbox.

El escenario ejecutado intentó modificar un recurso de Account B utilizando el contexto de Account A:

```text
resource B data after denial: {"value": "B"}
outcome: denied
correlation_id: corr-wu6
client_account_id: Account A
metadata: {"reason": "tenant_scope_denied"}
success_audit=0
outbox=0
```

Resultado:

```text
PASS cross-account command records redacted denial without success audit/outbox/mutation
```

Se verificó que:

- existe un `audit_record` durable;
- `outcome = denied`;
- conserva `correlation_id`;
- no existe audit de éxito;
- no existe outbox de éxito;
- Account B no muta;
- no se incluye payload crudo ni datos protegidos de B;
- `resource_id` protegido no se copia al registro de denegación.

## 2. Publisher failure y retry real

Se agregó `FailOnceConsumer`, que lanza una excepción durante el primer `consume()`.

El flujo real ejecutado fue:

```text
pending
→ dispatcher claim
→ publisher exception
→ dispatcher captura excepción
→ status=failed
→ attempts=1
→ available_at establecido
→ retry claim
→ publisher exitoso
→ status=published
→ attempts=2
```

Resultado exacto:

```text
PASS publisher failure is marked failed then retried and published
first=1
failed status=failed attempts=1
retry=1
published status=published attempts=2
```

La segunda entrega fue aceptada por el fake consumer y quedó registrada una sola entrega exitosa por event ID.

No se usó `mark_failed` manual como único camino: el primer `failed` fue generado por la excepción real del publisher dentro del dispatcher.

## 3. Retry y dispatchers concurrentes

Se mantienen y vuelven a verificar:

```text
PASS failed event is reclaimed when due and published on retry
PASS concurrent dispatchers do not double-claim events
```

Resultado de dispatchers concurrentes:

```text
claimed=[0, 2]
deliveries=2
cada evento: status=published, attempts=1
```

El resultado `claimed=[0,2]` o `[2,0]` es equivalente: el orden depende de scheduling. No hubo doble claim.

La query mantiene:

```sql
FOR UPDATE SKIP LOCKED
```

y reclama eventos:

```sql
pending AND available_at <= now()
failed AND available_at <= now()
```

## 4. FKs compuestas

La migración `003_audit_outbox.up.sql` incluye:

```text
(audit_records.client_account_id, audit_records.agency_id)
→ client_accounts(id, agency_id)

(outbox_events.client_account_id, outbox_events.agency_id)
→ client_accounts(id, agency_id)
```

`audit_records.client_account_id` permanece nullable para eventos no tenant-scoped. `outbox_events.client_account_id` es obligatorio.

Test real:

```text
PASS audit inconsistent Agency/ClientAccount is rejected by composite FK
rejected=True
```

## 5. Redaction profunda

El adapter filtra recursivamente claves sensibles dentro de:

- objetos;
- arrays;
- objetos dentro de arrays;
- arrays dentro de objetos.

Claves filtradas:

```text
password
token
secret
authorization
cookie
raw_body
request_body
```

Resultado persistido del test:

```text
{'safe': 'kept', 'items': [{'safe_item': 1}, {'deep': {'safe_deep': True}}], 'nested': {'safe_nested': 'kept'}}
```

Resultado:

```text
PASS nested audit secrets are deeply redacted
```

## 6. Contrato hexagonal

Auditoría usa consistentemente:

```text
AuditRepository.append(record, cursor)
```

Ese método coincide entre:

```text
puerto
adapter
comando
tests
```

Outbox conserva su método independiente:

```text
OutboxRepository.append_outbox(event, cursor)
```

No hay dependencia de FastAPI en los puertos.

## Base de prueba

Se utilizó una base PostgreSQL separada y reconstruida desde cero:

```text
production_runtime_wu6_final3
```

```text
Host:     127.0.0.1
Port:     5432
Server:   PostgreSQL 18.6
User:     postgres
```

No se alteraron:

```text
production_runtime_local
production_runtime_wu4
```

La autenticación utilizó el `pgpass.conf` local. No se registraron contraseñas.

## Tests reales WU6

```bash
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_wu6_final3 RUNTIME_DB_USER=postgres python -m api.test_audit_outbox
```

Resultado:

```text
exit code: 0
AUDIT OUTBOX SCENARIOS: 15/15 passed
```

Escenarios cubiertos:

```text
success → mutation + idempotency + audit + outbox
replay sin duplicados
hash conflict sin mutación
concurrencia de comandos
rollback completo
publisher failure real
retry de failed
coordinación de dos dispatchers
redaction profunda
FK compuesta inconsistente
denegación cross-account auditada
Account B intacta
```

## Verificaciones obligatorias

### TypeScript

```text
npm.cmd run typecheck
exit code: 0
```

### Tests TypeScript

```text
npm.cmd test
exit code: 0
Test Files  5 passed (5)
Tests       27 passed (27)
Start at 17:17:22
Duration 1.41s
```

### Python compileall

```text
python -m compileall -q api
COMPILE_EXIT=0
```

### Runtime FastAPI

```text
RUNTIME_API_HOST=127.0.0.1 RUNTIME_API_PORT=8000 python -m api.test_runtime
exit code: 0
FASTAPI SCENARIOS: 13/13 passed
```

### Repositories WU4

```text
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_wu4 RUNTIME_DB_USER=postgres python -m api.test_postgres_repositories
exit code: 0
POSTGRES WU4 SCENARIOS: 19/19 passed
```

### Idempotencia WU5

```text
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_wu4 RUNTIME_DB_USER=postgres python -m api.test_command_idempotency
exit code: 0
COMMAND IDEMPOTENCY SCENARIOS: 7/7 passed
```

### Auditoría/outbox WU6

```text
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_wu6_final3 RUNTIME_DB_USER=postgres python -m api.test_audit_outbox
exit code: 0
AUDIT OUTBOX SCENARIOS: 15/15 passed
```

El entorno emite un warning deprecado de Starlette relacionado con `httpx/httpx2`; no afecta los resultados ni los códigos de salida.

## Limitaciones

No existe todavía:

```text
worker productivo
proveedor externo real
RLS
auth productiva
OAuth/MFA
pool productivo definitivo
Docker Compose
CI/CD
deploy VPS
```

El dispatcher es un seam local con leasing. El fake consumer prueba captura de fallo, retry y deduplicación por event ID, pero no reemplaza una integración externa real.

No se implementó:

```text
backoff operativo completo
dead-letter queue
retención/archivado
audit/outbox para comandos adicionales
política de recuperación distribuida productiva
```

No se declara producción lista.

## Acceptance status

Los dos hallazgos finales fueron corregidos y verificados con PostgreSQL real:

```text
Denegación real auditada             ✅
Audit redacted                       ✅
Sin audit/outbox de éxito en denial  ✅
Account B intacta                    ✅
Publisher failure real               ✅
mark_failed automático               ✅
Retry failed → published             ✅
Dispatchers concurrentes             ✅
FK compuesta                         ✅
Redaction profunda                   ✅
WU1-WU5 sin regresiones              ✅
Artefactos físicos                   8/8 ✅
```

**Work Unit 6 queda aceptado para el slice durable de auditoría y outbox.** Esta aceptación no incluye worker productivo, entrega externa real, RLS, autenticación productiva ni readiness de producción.
