# MVP Commercial Demo — Work Unit 6 Evidence

## Status

Read APIs versionadas y tenant-scoped implementadas y verificadas contra PostgreSQL 18.6 real sobre base dedicada `production_runtime_mvp_wu6`. **Implementado, pendiente de revisión de Leo.**

## Alcance (tasks.md WU6)

> Add versioned read APIs for dashboard, Inbox, leads, properties, pipeline, agenda, activity; derive counts from DB. Depends 3–5; `api/queries`, `api/routes`, `contracts/`. Verification: `pytest api/test_crm_reads.py`; scoped GET-after-command.

Cubierto: siete superficies GET bajo prefijo versionado `/v1/crm/*` (dashboard, inbox, inbox/{id}/messages, leads, properties, properties/{id}, pipeline, agenda, activity), todos los conteos derivados de SQL (COUNT/GROUP BY/FILTER/LATERAL), GET-after-command sobre el comando atómico WU5 real, aislamiento A/B, errores no-enumerantes con envelope v1 y correlación consistente.

## Archivos creados

```text
api/adapters/postgres_crm_reads.py   — SQL read adapter (dashboard/inbox/leads/pipeline/agenda/activity) con _require de WU3
api/queries/__init__.py              — paquete query-side
api/queries/crm_reads.py             — CrmReadService provider-neutral (sin FastAPI), validación pura + cross-checks con repos WU3
api/composition_wu6_mvp.py           — composición FastAPI dev-only: rutas /v1/crm, resolver server-owned, middleware correlación, handlers 404/405 envelope
contracts/runtime-v1.ts              — (agregado) tipos Crm*Response v1 espejo de las respuestas Python
api/test_crm_reads.py                — harness escenario-tupla 13 casos + wrapper pytest
openspec/changes/mvp-commercial-demo/work-unit-6-evidence.md
```

No se modificaron:

```text
PDR, proposal.md, design.md, specs, tasks.md
WU1–WU5 del MVP: contratos TS previos, migraciones 001–004, runner, seed/reset,
entidades, repositories (api/adapters/postgres_commercial_crm.py intacto — solo se IMPORTA _require),
composición WU3, inbound boundary WU4, comandos WU5 (api/application/crm_commands_wu5.py,
api/commands/crm_commands.py, api/composition_wu5_mvp.py)
composiciones técnicas (composition.py, composition_wu4.py, composition_wu5.py), api/app.py,
api/http_contracts.py, api/tenant_boundary.py
WACRM, demo/agencia-inmobiliaria.*, web/ (la UI dashboard es WU8)
```

## Diseño

### Composición y contexto (reutiliza WU4/WU5, no los toca)

- `build_mvp_wu6_application(conn_kwargs, target_client_account_id, identity)` — mismo patrón fail-fast + pool + `PostgresTenantRepository`/`PostgresMembershipRepository`/`TenantContextResolver` server-owned de la composición WU4 MVP. La cuenta objetivo es estado de sesión/composición; **ninguna ruta read acepta un account id del cliente**; un `?clientAccountId=…` forjado se ignora (probado en R9).
- Identidad: `InMemoryIdentityAdapter` dev-only; sin auth productiva.
- Rutas fijas registradas estáticamente bajo `/v1/crm` (prefijo versionado, threat matrix: "fixed route registration"). 401/403 comparten la shape no-enumerante aceptada; 404 de recurso ausente/extranjero es una sola shape; ruta desconocida y método incorrecto devuelven envelope v1 (handlers 404/405 al estilo de `api/app.py` aceptado), nunca el default de FastAPI.
- Toda respuesta (200 y errores) lleva `X-Correlation-Id` de header y `correlation_id`/`error.correlation_id` en body consistentes.

### Conteos derivados de la DB

- Dashboard: `GROUP BY status` en leads, `GROUP BY availability` en properties, `sum(unread_count)` en conversaciones abiertas, `count(*) FILTER (WHERE …)` para pending/overdue de follow-ups, y `leads_by_stage` con LEFT JOIN por etapa ordenado por position — todo SQL.
- Inbox: JOIN conversations→contacts + `LEFT JOIN LATERAL` para el último mensaje por conversación. Sin fixtures, sin acumulación en memoria.
- Pipeline: etapas con `lead_count` por GROUP BY; cross-check de que el read model coincide con `PostgresPipelineRepository.list` (WU3) — divergencia = fallo.
- Agenda: overdue/due_today calculados con `now()` del servidor (misma zona SQL que las escrituras WU5), no con reloj del cliente.
- Activity: `audit_records` filtrado por `client_account_id = contexto` (las filas con account NULL, cross-context, quedan invisibles); NUNCA devuelve `metadata`: expone action, outcome, resource type/id, actor, correlation, idempotency key, y `reason` sólo en outcomes no-success. Body plano está fuera del view por diseño.
- El `properties.price` numérico se serializa como string (`"185000.00"`) para no perder exactitud decimal en JSON.

### Espejo TS

`contracts/runtime-v1.ts` añade los tipos de respuesta v1 (`CrmReadMeta`, `CrmDashboardResponse`, `CrmInboxResponse`, etc.) como contrato documentado para WU8 (UI). Sin code-gen compartido, igual que WU4/WU5: la coherencia la cubren los tests de este harness comparando cada count contra SQL directo.

## Base usada

```text
production_runtime_mvp_wu6 (nueva, creada en esta unidad)
```

```text
CREATE DATABASE production_runtime_mvp_wu6
runner --up → applied 001/002/003/004, RUNNER_EXIT=0
mvp_seed.sql → SEED_EXIT=0
```

El harness SIEMPRE corre sobre wu6; el comando WU5 del escenario R2 se ejecuta contra wu6 vía su propia composición con `conn_kwargs` apuntando a wu6. Bases anteriores verificadas intactas después de todo el ciclo:

```text
production_runtime_mvp_wu2 rows=16
production_runtime_mvp_wu3 rows=16
production_runtime_mvp_wu4 rows=16
production_runtime_mvp_wu5 rows=35   (estado propio de su harness; sin cambios desde WU5)
```

## Matriz de escenarios (13/13 PASS, exit 0)

Comando:

```bash
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_mvp_wu6 RUNTIME_DB_USER=postgres python -m api.test_crm_reads
```

| # | Escenario | Resultado |
|---|---|---|
| R1 | dashboard baseline: counts == SQL directo sobre seed | PASS |
| R2 | comando inbound WU5 real → GET dashboard/inbox/leads muestran el efecto durable (unread 2, follow_ups_pending 2, lead en la respuesta de comando presente, último mensaje = body del evento) | PASS |
| R3 | activity: success audit con action/outcome/correlation visibles; cero body crudo ni metadata en el JSON | PASS |
| R4 | pipeline lead_count por etapa == SQL; agenda pending==2; due_at ISO-8601 Z | PASS |
| R5 | properties scoped; propiedad de B y UUID fantasma comparten UN solo 404 no-enumerante (messages idénticos) | PASS |
| R6 | mensajes de la conversación A DB-derived; conversación de B → 404 bajo sesión A | PASS |
| R7 | sesión B: sus propios counts, cero ids/registros de A visibles en ningún surface, activity vacía | PASS |
| R8 | identidad ausente → 401 envelope con correlación en las 7 superficies | PASS |
| R9 | `?clientAccountId=B` forjado es ignorado: scope sigue server-derived (cuenta A en body, counts de A) | PASS |
| R10 | ruta desconocida → 404 envelope; POST a GET → 405 envelope; ambos con correlación | PASS |
| R11 | `limit=abc/9999/0` → 400 `invalid_query` antes de SQL; `limit=1` → página de 1 | PASS |
| R12 | reload determinista: dos GETs de inbox devuelven estado persistido idéntico (excluido clock/correlación) | PASS |
| R13 | comando denegado cross-scope (conversación de B con contexto A) aparece en activity redacted: `resource_id` NULL, reason class presente, sin id de B ni body en el JSON | PASS |

Re-run completo dos veces con preclean convergente: 13/13 ambos ciclos, exit 0.

## Verificaciones obligatorias

| Comando | Resultado |
|---|---|
| `python -m compileall -q api` | exit 0 |
| `python -m api.test_crm_reads` (2x) | 13/13, exit 0 |
| `python -m api.test_crm_commands` (re-run WU5, sin regresión) | 21/21, exit 0 |
| `npm.cmd run typecheck` | exit 0 |
| `npm.cmd test` | 6 files, 39/39, exit 0 |

## Fuera de alcance WU6 (no implementado a propósito)

```text
UI/dashboard Next.js con datos reales (WU8)
selector demo de cuenta server-owned + cache keys (WU7; aquí target_account es estado de composición)
endpoint simulador con botón Inbox (WU9)
paginación/cursor completa (limit sólo en activity; listados demo son acotados por diseño del seed)
export CSV, realtime, cache de aplicación (no introducido: cada GET lee la DB)
Kapso/WhatsApp real, LLM/LangGraph, RLS, auth productiva, Docker/CI/CD
```

## Limitaciones

- `psycopg` queda confinado a `api/adapters/postgres_crm_reads.py`; `api/queries/crm_reads.py` es provider-neutral pero importa `CrmRepositoryComposition` de la composición WU3 para los cross-checks — seam aceptado en WU4/WU5, documentado.
- `_require` se reutiliza por import privado desde el módulo de repositorios WU3 (mismo paquete adapters, mismo layer); si revisión prefiere duplicación explícita del guard, es un cambio de una línea.
- Las rutas read están detrás de la misma identidad dev-only que WU4/WU5; en producción la resolución de contexto cambiaría de adaptador, no de contrato.
- `as_of` usa el reloj de la DB (`now()`), expuesto para que WU8 no calcule "hoy" en el navegador.
- Sin pytest como runner instalado: el módulo corre vivo (`python -m api.test_crm_reads`, shape `run_all()` → tuplas) con wrapper `test_crm_reads()` para cuando el runner exista.
- `unread_messages` del dashboard es suma de `unread_count` de conversaciones abiertas (semántica demo elegida; Inbox muestra por conversación).

## WACRM

Audited reference only; sin fork, copia ni dependencia.

## Acceptance status

Implementado y verificado con FastAPI TestClient + PostgreSQL 18.6 real sobre base descartable dedicada `production_runtime_mvp_wu6`, sin regresión en WU5 ni cambios en bases anteriores.

**Aceptado para read APIs tenant-scoped del MVP (13/13, dos ciclos deterministas).** Follow-ups cosméticos (no bloqueantes): caso dedicado 403 en el harness (hoy sólo se ejercita 401) y limpieza de `decimal_to_str` sin uso.
