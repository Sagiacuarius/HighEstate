# MVP Commercial Demo — Work Unit 7 Evidence

## Status

Selector de cuenta demo server-owned, composición de contexto en FastAPI, scoped cache keys y controles seed/reset implementados y verificados contra PostgreSQL 18.6 real sobre base dedicada `production_runtime_mvp_wu7`. **Implementado, pendiente de revisión de Leo.**

## Alcance (tasks.md WU7)

> Add server-owned dev identity, membership selector, context composition, scoped cache keys, seed/reset controls. Depends 4–6; `api/identity`, `web/runtime-client.ts`. Verification: `pytest api/test_demo_context.py`; A↔B switch/reload.

Cubierto: identidad demo server-owned (`DemoIdentityAdapter`, usuario fijo de configuración, claims del request ignorados), selector de membership (`DemoContextSelector`: hint validado en forma pura + resolución vía el `TenantContextResolver` aceptado), composición FastAPI con endpoints de select/current/seed/reset más lecturas dashboard/inbox cacheadas por scope, cache keys con `agency_id + client_account_id` embebidos y denegación cross-scope sin fill, y controles seed/reset que ejecutan los scripts aceptados (sólo filas `demo-*`).

## Archivos creados

```text
api/identity/__init__.py              — paquete identidad demo (dev-only)
api/identity/demo_identity.py         — DemoIdentityAdapter, AbsentDemoIdentityAdapter, validate_requested_account_id, DemoContextSelector
api/queries/demo_cache.py             — build_scoped_key/key_scope/ScopedQueryCache (miss/hit, cross-scope deny, invalidate_scope, clear)
api/composition_wu7_mvp.py            — app FastAPI: POST /v1/demo/context/select, GET /v1/demo/context/current,
                                        GET /v1/demo/dashboard|inbox (cacheadas), POST /v1/demo/seed|reset
api/test_demo_context.py              — harness escenario-tupla 14 casos + wrapper pytest
openspec/changes/mvp-commercial-demo/work-unit-7-evidence.md
```

## Archivos modificados (aditivos, sin cambiar comportamiento existente)

```text
contracts/runtime-v1.ts   — (agregado) DemoSelectContextBody, DemoContextResponse, DemoControlsResponse,
                            parseDemoContextResponse, demoQueryCacheKey espejo de demo_cache.py
web/runtime-client.ts     — (agregado) RuntimeHttpClient.post genérico + DemoSelectorClient
                            (selectContext con envelope v1 + chequeo de correlación, cacheKey desde contexto resuelto);
                            firma del fetcher ampliada con method?/body? opcionales (compat con stubs existentes)
web/runtime-client.test.ts — (agregado) 6 tests: post del hint + parse, denial 403 como RuntimeHttpError,
                            rechazo de environment/version/permissions, formato de cache key,
                            partición por cuenta, rechazo de segmentos inseguros
```

No se modificaron:

```text
PDR, proposal.md, design.md, specs, tasks.md
WU1–WU6 del MVP: contratos TS previos, migraciones 001–004, runner, seed/reset SQL,
entidades, repositories (solo se IMPORTAN _require y repositorios), composición WU3,
inbound boundary WU4, comandos WU5, queries/composición WU6
(src/application/commercial-demo.ts solo se espeja el contrato DemoContextResolver, no se toca)
composiciones técnicas, api/app.py, api/http_contracts.py, api/tenant_boundary.py
WACRM, demo/agencia-inmobiliaria.*, web/app (la UI dashboard es WU8)
```

## Diseño

### Selector server-owned (espejo del contrato TS DemoContextResolver)

- `DemoIdentityAdapter.get_authenticated_user` devuelve siempre el demo-user configurado en servidor; ignora el request. `AbsentDemoIdentityAdapter` devuelve None (escenario 401).
- `DemoContextSelector.select(request, requested_account_id)`: primero `validate_requested_account_id` pura (no-string / vacío / whitespace envolvente / >256 / charset fuera de `[A-Za-z0-9:_-]` → `MalformedAccountIntent`, 400 antes de cualquier SQL); luego `identity.get()` (ausente → 401); luego delega al `TenantContextResolver.resolve` aceptado (membership server-side; missing/foreign/sin-membership → un solo 403 no-enumerante); verifica que el contexto resuelto pertenezca al usuario autenticado.
- No se usa `TenantContextResolver.switch()`: exige permiso `client_account:switch` ausente en el seed demo; cada select es un `resolve()` stateless y la preservación del contexto previo se logra mutando `app.state.target_client_account_id` SÓLO en el path de éxito (probado en D4/D5).
- La cuenta pedida nunca se copia a ningún campo de autoridad: el scope efectivo sale del `TenantContext` resuelto.

### Composición y errores

- `build_mvp_wu7_application(conn_kwargs, target_client_account_id, identity)` — mismo patrón fail-fast + pool + repos Postgres + read service WU6. Rutas fijas bajo `/v1/demo/*`. 400 intent malformado / 401 sin identidad / 403 no-enumerante / 404 ruta / 405 método, todos con envelope v1 y `X-Correlation-Id` consistente header/body.
- `GET /v1/demo/context/current` proyecta el contexto de sesión vigente: es la prueba de preservación tras cada denegación.
- Estado de sesión = `app.state` del proceso (dev-only, single-session; documentado como no-productivo; WU8 consumirá estos endpoints por HTTP).

### Scoped cache keys

- Formato `mvp-demo:v1:{agency_id}:{account_id}:{surface}:{qualifier}`, scope siempre derivado del contexto server-resuelto. `key_scope()` extrae el scope o falla cerrado. `get_or_fill` verifica `key.scope == context.scope` ANTES de fill/store: una key de A bajo contexto B → `TenantAccessDenied` y el filler nunca corre (el test usa un filler que lanzaría `AssertionError` si se invocara).
- Las lecturas dashboard/inbox van por el cache con header `X-Cache-Status: hit|miss`. En hit, el `correlation_id` del body se refresca al del request vigente (el payload cacheado es snapshot de datos; la correlación es contractual por-request).
- Invalidación: `invalidate_scope(agency, account)` y `clear()` total tras seed/reset (tocan ambas cuentas). TS espeja el builder (`demoQueryCacheKey`) para keys de objeto/cache del lado cliente en WU8.

### Controles seed/reset

- Ejecutan verbatim `infra/migrations/validation/mvp_seed.sql` y `mvp_reset.sql` (se saltean sólo las meta-líneas psql `\set`, que psycopg no interpreta; el servidor aborta el script al primer error SQL = semántica ON_ERROR_STOP). Requieren contexto de sesión válido (401/403 si no).
- `POST /v1/demo/seed` = re-apply convergente. `POST /v1/demo/reset` = reset + re-seed (restaura el estado seed determinístico, según el spec de account-switching).
- Limpian el cache al terminar. Sólo tocan filas `demo-*` por los ownership gates aceptados de los scripts.

## Base usada

```text
production_runtime_mvp_wu7 (nueva, creada en esta unidad)
```

```text
CREATE DATABASE production_runtime_mvp_wu7
runner --up → applied 001/002/003/004, RUNNER_EXIT=0
mvp_seed.sql → SEED_EXIT=0
```

Bases anteriores verificadas intactas tras todo el ciclo:

```text
production_runtime_mvp_wu2 rows=16
production_runtime_mvp_wu3 rows=16
production_runtime_mvp_wu4 rows=16
production_runtime_mvp_wu5 rows=35   (estado propio de su harness)
production_runtime_mvp_wu6 rows=18   (estado propio de su harness)
```

## Matriz de escenarios (14/14 PASS, exit 0, dos ciclos)

Comando:

```bash
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_mvp_wu7 RUNTIME_DB_USER=postgres python -m api.test_demo_context
```

| # | Escenario | Resultado |
|---|---|---|
| D1 | select A: 200 proyección server-resuelta + correlación | PASS |
| D2 | switch A→B: sesión B sólo seed de B (leads=1, inbox=1 conv B, cero ids de A) | PASS |
| D3 | comando inbound WU5 real en A + switch de vuelta: efecto durable visible (unread 2, pending 2, body del evento) | PASS |
| D4 | select forjado/fantasma → un solo 403; contexto previo A preservado (current==A); cero mutación (messages/success-audit iguales) | PASS |
| D5 | intents malformados ({}, extra key, blank, `../escape`) → 400 pre-SQL; contexto sigue A | PASS |
| D6 | sin identidad: select/current/seed → 401 envelope + correlación | PASS |
| D7 | idempotency key de A reusada en B con otro payload → `IdempotencyConflict`, B intacto, cero success audit de B | PASS |
| D8a | cache unit: miss→hit mismo scope; key de A bajo contexto B denegada con cero fill (filler que explotaría si corriera) | PASS |
| D8b | HTTP: invalidate→miss→hit, correlación per-request refrescada en el hit | PASS |
| D9a | reset: scratch sin marca sobrevive, seed restaurado (leads=1, unread=1), cache flusheado (miss) | PASS |
| D9b | seed re-apply converge 200 idempotente | PASS |
| D9c | fail-closed: hijo sin marca bloquea reset con 500 `demo_script_failed`, filas seed intactas; tras limpiarlo, reset 200 de nuevo | PASS |
| D10 | ruta desconocida 404 / GET-a-POST 405 con envelope (nunca default) | PASS |

(14 escenarios: D8 y D9 desdoblados en a/b/c.)

## Verificaciones obligatorias

| Comando | Resultado |
|---|---|
| `python -m compileall -q api` | exit 0 |
| `python -m api.test_demo_context` (2x) | 14/14, exit 0 |
| `python -m api.test_crm_commands` (re-run WU5, sin regresión) | 21/21, exit 0 |
| `python -m api.test_crm_reads` (re-run WU6, sin regresión) | 13/13, exit 0 |
| `npm.cmd run typecheck` | exit 0 |
| `npm.cmd test` | 6 files, 45/45 (39 previos + 6 WU7), exit 0 |

## Bugs encontrados durante la unidad (producción, no test)

1. `get_or_fill` invocaba `filler()` sin contexto mientras la composición pasaba `lambda ctx: …` → `TypeError: 'coroutine' object is not callable`. Fix: firma `filler(context)`.
2. `_run_demo_script` usaba `asyncio.run()` dentro del loop del endpoint → hubiera fallado siempre. Fix: función async con await.
3. Hit de cache devolvía el `correlation_id` del primer request en el body, rompiendo la consistencia header/body contractual. Fix: refresh de `correlation_id` al request vigente en hits (datos = snapshot, correlación = per-request).
4. `create_mvp_demo_app` pasaba `loader(context)` (coroutine ya evaluada) en vez del callable. Fix: pasar `loader`.

## Hallazgo de diseño documentado (no es bug, es contrato)

Los scripts seed/reset aceptados sólo tocan filas `demo-*`: con filas de comando sin marca referenciando filas seed, la FK RESTRICT aborta el script (fail-closed correcto). El endpoint lo expone como 500 `demo_script_failed` con envelope + correlación, sin mutación parcial (el script es una transacción). El harness limpia filas de comando antes del reset (helper `clear_unmarked`, misma regla que los preclean WU5/WU6) y prueba el bloqueo explícitamente (D9c). Implicancia para WU8/WU9: el botón de reset demo debe limpiar o advertir sobre efectos de simulador pendientes, o aceptar el 500 como "reset bloqueado".

## Fuera de alcance WU7 (no implementado a propósito)

```text
UI/dashboard Next.js con datos reales y selector visible (WU8; aquí sólo el cliente TS + endpoints)
endpoint simulador con botón Inbox (WU9)
sesiones multi-usuario / persistencia de sesión (estado = app.state del proceso, dev-only)
paginación completa, realtime, auth productiva, RLS
Kapso/WhatsApp real, LLM/LangGraph, Docker/CI/CD
```

## Limitaciones

- `api/identity` es dev-only: `DemoIdentityAdapter` fija el usuario en configuración del servidor; el adaptador de auth productiva futura reemplaza el puerto `CurrentIdentity`, no el selector.
- `ScopedQueryCache` es process-local sin TTL: válido para demo; invalidación explícita por scope + flush en seed/reset. Sin stampede protection (fuera de alcance demo).
- Sin pytest como runner instalado: el módulo corre vivo (`python -m api.test_demo_context`, shape `run_all()` → tuplas) con wrapper `test_demo_context()` para cuando el runner exista.
- El `post` genérico de `RuntimeHttpClient` serializa el body como JSON sin header `Content-Type` explícito (igual que el fetcher existente, que sólo tipa `headers`); suficiente para el stub de tests y `fetch` real.

## WACRM

Audited reference only; sin fork, copia ni dependencia.

## Acceptance status

Implementado y verificado con FastAPI TestClient + PostgreSQL 18.6 real sobre base descartable dedicada `production_runtime_mvp_wu7`, sin regresión en WU5/WU6 ni cambios en bases anteriores.

**Aceptado para selector demo server-owned (14/14, dos ciclos).** Sesión single/dev-only y cache process-local documentados como límites de demo; UI real con selector visible queda para WU8.
