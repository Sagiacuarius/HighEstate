# MVP Commercial Demo — Work Unit 3 Evidence

## Status

Entidades de dominio CRM y repositories PostgreSQL tenant-scoped implementados y verificados contra PostgreSQL 18.6 real. **Aceptado para entidades y repositories tenant-scoped (25/25 escenarios).**

## Proyecto

```text
C:\Users\Windows\Documents\AI Agency Platform
```

## Archivos creados

```text
api/domain/__init__.py
api/domain/commercial_crm.py
api/adapters/postgres_commercial_crm.py
api/composition_wu3_mvp.py
api/test_crm_repositories.py
openspec/changes/mvp-commercial-demo/work-unit-3-evidence.md
```

No se modificaron:

```text
PDR, proposal.md, design.md, specs
WU1/WU2 del MVP (commercial-demo.ts, contratos, doubles, migración 004, seed/reset, runner)
migraciones 001–004
composición técnica WU4/WU5/WU6 (composition.py, composition_wu4.py, composition_wu5.py)
WACRM
demo/agencia-inmobiliaria.*
```

## 1. Entidades de dominio (módulo vertical propio)

`api/domain/commercial_crm.py` — no reutiliza `tenant_resource`:

```text
Contact, Lead, Property, Pipeline, Stage, Conversation, Message, FollowUp
```

Espejo de los contratos WU1 (`src/application/commercial-demo.ts`):

- IDs validados con la misma regla del constructor WU1: string no vacío, sin espacios laterales, ≤256, `^[A-Za-z0-9][A-Za-z0-9:_-]*$` (UUIDs válidos pasan).
- Enums coherentes con la migración 004: `CONTACT_STATUSES`, `LEAD_STATUSES`, `OPERATION_TYPES`, `PROPERTY_AVAILABILITIES`, `FOLLOWUP_STATUSES`, `MESSAGE_DIRECTIONS`, `CONVERSATION_STATUSES`.
- Invariantes puras: `Pipeline.__post_init__` rechaza Stages de otro Pipeline o de otro ClientAccount; `FollowUp` exige `lead_id` o `contact_id`; `reschedule_follow_up` rechaza reprogramar terminados/cancelados (invariante design); `price` es `Decimal` no negativo; inmutables (`frozen=True`).

## 2. Repositories PostgreSQL (psycopg 3, sin ORM)

`api/adapters/postgres_commercial_crm.py`:

| Port WU1 | Adapter |
|---|---|
| ContactLeadRepository | PostgresContactLeadRepository (list/get/save/delete de Contact y Lead) |
| PropertyRepository | PostgresPropertyRepository (list/get/save/delete) |
| PipelineRepository | PostgresPipelineRepository (list/get con stages ordenados, transition_lead) |
| ConversationRepository | PostgresConversationRepository (list/get/get_messages/mark_read) |
| FollowUpRepository | PostgresFollowUpRepository (list_due/list_all/get/save/complete) |

## 3. Reglas de aislamiento

- `_require(context, write=...)` en cada operación: contexto ausente → `CrossTenantAccessDenied` antes de cualquier SQL; cuenta vacía, agency/membership inconsistentes → rechazo; escrituras exigen `client_account:write` derivado de la membership server-side.
- Todo SQL lleva el predicado obligatorio `client_account_id = %s` derivado del `TenantContext` (list/get/save/update/delete).
- Ningún `client_account_id`/`lead_id`/`contact_id`/`property_id`/`stage_id`/`conversation_id` del payload es autoridad: la cuenta siempre sale del contexto; las referencias entre entidades se validan vía FKs compuestas de 004, y una referencia cross-account falla en la base y se traduce a error seguro.
- No-enumeración: recurso ajeno es indistinguible de inexistente (`None` / un solo `CrmNotFoundError`); violaciones FK → `CrmRelationshipViolation` con mensaje genérico; duplicados → `CrmConflictError`.
- El upsert `ON CONFLICT ... WHERE <table>.client_account_id = EXCLUDED.client_account_id` impide que un save con id ajeno mute la fila de otra cuenta.
- Los repositorios nunca tocan `demo_seed_key`: el seed sólo se muta a través del flujo seed/reset o de saves explícitos con el contexto dueño.

## 4. Composición local

`api/composition_wu3_mvp.py`:

```text
CrmRepositoryComposition  (contacts, properties, pipelines, conversations, follow_ups)
build_crm_composition()   (fail-fast env vars, mismo PostgresRunner compartido)
```

No reemplaza ni importa la composición técnica WU4/WU5/WU6; psycopg queda confinado a la capa adapter.

## 5. Base usada

```text
production_runtime_mvp_wu3   (nueva, descartable)
```

- Creada desde cero; `production_runtime_mvp_wu2` intacta (verificado: `wu2_rows=16` después de toda la corrida WU3).
- Migraciones aplicadas por el runner existente:

```text
applied 001/002/003/004 → RUNNER_EXIT=0
```

- Seed determinista `mvp_seed.sql` aplicado: `SEED_EXIT=0`.
- Credenciales por env vars + pgpass libpq; ninguna contraseña en código ni salida.

## 6. Tests reales contra PostgreSQL

```bash
python -m api.test_crm_repositories
```

Resultado:

```text
MVP-CRM WU3 REPOSITORY SCENARIOS: 25/25 passed
WU3_TEST_EXIT=0
```

Corrida repetida (repetibilidad con pre-clean de scratch y re-seed idempotente):

```text
25/25 passed, WU3_REPEAT_EXIT=0
```

Escenarios cubiertos (todos con verificación del estado final commiteado en DB):

```text
S1  Account A lista sólo sus registros (5 repos incl. stages anidados)
S1  Account B lista sólo los suyos
S2  Lectura de recurso B con contexto A → None / no-enumerating
S2b get_messages de conversación B con contexto A → CrmNotFoundError
S3  Seed determinista A/B visible vía repositorios (lead→stage, pipeline→stages, mensajes)
S4  Creación con scope correcto persiste bajo la cuenta del contexto
S5  Actualización con scope correcto muta sólo la fila propia
S6  save con client_account_id de la entidad ≠ contexto → rechazado, sin fila
S7  upsert bajo B contra id dueño de A no puede mutar la fila de A (nombre intacto)
S8  Lead cross-account ×4 variantes (contact B, property B, stage de pipeline B,
    pipeline B + stage A) → FK rechaza, cero persistencia
S9  delete de recurso ajeno → no-op seguro; delete propio elimina exactamente 1 fila
S10 membership read-only no puede escribir
S11 contexto ausente y contexto inconsistente rechazados antes de SQL
S12 transition_lead: mismo stage idempotente; stage ajeno → safe not-found;
    stage nuevo propio del pipeline → transición válida con previous_stage
S13 dominio puro: FollowUp sin target rechazado; follow-up con contact de B → FK;
    complete() es one-shot (segundo llamado → None)
S14 save same-account sobre id propio = guarded upsert (sin crear duplicados)
S15 Account B íntegra (set exacto de 8 filas semilla) tras todos los intentos desde A
```

## 7. Verificaciones obligatorias

| Comando | Resultado |
|---|---|
| `npm.cmd run typecheck` | exit 0 |
| `npm.cmd test` | 6 files, 39/39 passed, exit 0 |
| `python -m compileall -q api` | COMPILE_EXIT=0 |
| `python -m api.test_crm_repositories` | 25/25 passed, exit 0 |
| segunda corrida del harness | 25/25 passed, exit 0 |
| `wu2_rows` tras WU3 | 16 (base WU2 intacta) |

## No implementado (fuera de alcance WU3)

```text
endpoints HTTP CRM
UI / dashboard / simulador
commands CRM atómicos con hash/idempotencia/audit/outbox (WU5 MVP)
read APIs (WU6 MVP)
inbound boundary (WU4 MVP)
Kapso / WhatsApp / LLM / agentes / automatizaciones
RLS / row-level security
auth productiva
Docker / VPS / CI/CD
```

## Limitaciones

- Los repositorios no envuelven aún multi-write transaccional: `transition_lead` hace read-then-write en autocommit; la atomicidad con comando/idempotencia/auditoría corresponde al WU5 del MVP (patrón WU5/WU6 técnico reutilizado allí, no aquí).
- No hay auditoría ni outbox en estas escrituras (también diferido a WU5 por diseño).
- `mark_read` fija `unread_count = 0` sin recalcular desde mensajes; el conteo derivado de DB llega con los read APIs (WU6).
- Sin RLS: el aislamiento es por predicado en la capa adapter + FKs compuestas; RLS productivo sigue fuera de alcance.
- El harness usa `psycopg` sincronia para el runner de migraciones/seed y `asyncio.run` por escenario, igual que el patrón WU4 técnico aceptado.
- Las entidades Python espejan los contratos TS pero no hay un generador compartido de tipos; la coherencia está cubierta por los tests contractuales TS (39/39) y este harness SQL.

## Acceptance status

Implementado y verificado contra PostgreSQL 18.6 real con base descartable dedicada.

**Work Unit 3 del MVP Commercial Demo queda aceptado para entidades y repositories tenant-scoped.** La atomicidad transaccional de escrituras, idempotencia, auditoría/outbox, read APIs y UI quedan para WU5/WU6 del MVP.
