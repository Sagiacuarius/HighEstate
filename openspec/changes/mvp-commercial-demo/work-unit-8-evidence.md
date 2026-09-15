# MVP Commercial Demo — Work Unit 8 Evidence

## Status

Dashboard Next.js de datos reales (reemplazo de la health page), fetch layer tipado con parsers de contrato, adaptador CSS de `demo/agencia-inmobiliaria.css` y backend de sesión demo WU8 sobre PostgreSQL real (`production_runtime_mvp_wu8`). Verificado con `npm test` + `npm run build` + navegación en navegador real (Edge headless vía CDP): vistas, recarga y switch A↔B. **Implementado, pendiente de revisión de Leo.**

## Retoma: estado encontrado y correcciones de esta sesión

La sesión previa (08/09) dejó los archivos creados pero el servidor de demostración caía en cada request con `psycopg.InterfaceError: ProactorEventLoop`. Causa raíz verificada empíricamente: uvicorn 0.41 (`loop="auto"`) fuerza `ProactorEventLoop` en win32 mediante `loop_factory` propio, que **pisa** el `WindowsSelectorEventLoopPolicy` de `api/adapters/postgres_core.py`. Fix en WU8-only: `api/demo_run.py` pasa `loop="api.demo_run:_selector_loop"` (factory explícito que devuelve `SelectorEventLoop`).

Segundo defecto encontrado al verificar en navegador real: el preflight CORS `OPTIONS` devolvía 405 (el handler 405 de la app capturaba OPTIONS), y el dashboard renderizaba "Request to /v1/demo/context/current failed". Fix en WU8-only: `CORSMiddleware` en `create_mvp_dashboard_app` restringido a `http://127.0.0.1:3000` / `http://localhost:3000`, dev-only, documentado inline. No toca ninguna composición aceptada.

Además, el build de `web/` se repitió con `NEXT_PUBLIC_DEMO_API_BASE_URL=http://127.0.0.1:8001` (variable NEXT_PUBLIC: se inlinea en el bundle; `next start` sin la env la pierde — reproducibilidad del paso navegador requiere build+start con la misma env).

## Alcance (tasks.md WU8)

> Replace health page with real-data dashboard; adapt `demo/agencia-inmobiliaria.css`, never fixture state. Depends 6–7; `web/app`, CSS adapter. Verification: `npm test && npm run build`; browser views/reload/switch.

Cubierto:

- **Dashboard de datos reales** (`web/app/page.tsx`, client component, `force-dynamic`): 7 vistas (Resumen, Inbox, Leads, Propiedades, Pipeline, Agenda, Actividad), selector visible de cuenta demo server-owned (los ids pedidos son hints; el servidor resuelve membership), drill-in de hilo de conversación por conversación, controles Re-seed/Reset, estados de error que muestran el código del envelope sin datos fake, skeletons mientras carga. Ningún fixture, ningún count hard-coded: cada número proviene de las read APIs WU6 vía la composición WU8.
- **Fetch layer tipado** (`web/app/demo-api.ts`): todas las respuestas pasan por los parsers estructurales de `contracts/runtime-v1.ts` (nunca casts); base URL fail-fast vía `resolveDemoApiBaseUrl` (`NEXT_PUBLIC_DEMO_API_BASE_URL`, reutiliza `requireRuntimeApiBaseUrl`).
- **CSS adaptado** (`web/app/demo.css`): copia adaptada de `demo/agencia-inmobiliaria.css` + sección "WU8 views" (selector de cuenta, hilo de mensajes, tablas, estados de error). `demo/*` original intacto.
- **Backend de serving WU8** (`api/composition_wu8_mvp.py` + `api/demo_run.py`): composición delgada sobre seams aceptadas (resolver W1, selector+identidad W7, cache W7, reads W6, repos W3); expone selector, las 8 superficies de lectura, mensajes por conversación y seed/reset. No modifica ninguna app aceptada.
- **Parsers de contrato TS** (`contracts/runtime-v1.ts`, agregado WU8): `parseCrm*Response`, `parseDemoContextResponse`, `parseDemoControlsResponse` como validación de red del cliente.
- **`web/runtime-client.ts`**: `post` expuesto con opciones (usado por el selector de cuenta desde la UI).

## Archivos

Creados:

```text
api/composition_wu8_mvp.py         — app FastAPI demo (selector + reads sesion + cache + seed/reset + CORS dev-only)
api/demo_run.py                    — entrada de servidor: python -m api.demo_run (config fail-fast, loop Selector explícito)
web/app/page.tsx                   — dashboard (reemplaza health page)
web/app/layout.tsx                 — shell Next.js (importa demo.css)
web/app/error.tsx, not-found.tsx   — fallbacks con envelope visible
web/app/demo-api.ts                — fetch layer tipado sobre parsers + RuntimeHttpClient/DemoSelectorClient
web/app/demo.css                   — adaptador CSS desde demo/agencia-inmobiliaria.css
web/app/demo-api.test.ts           — 10 tests: parsers + helpers de vista (stub fetcher, mismos parsers de red)
openspec/changes/mvp-commercial-demo/work-unit-8-evidence.md
```

Modificados (aditivos, sin cambiar comportamiento aceptado):

```text
contracts/runtime-v1.ts — agregado bloque WU8: tipos de respuesta v1 + parsers estructurales + parseDemoControlsResponse
api/composition_wu8_mvp.py, api/demo_run.py — correcciones WU8 de esta sesión (loop factory + CORS), ambos son archivos WU8
```

Intactos: migraciones 001–004, runner, seed/reset SQL, repos WU3, inbound WU4, comandos WU5, queries/composición WU6, identidad/cache/composición WU7, `api/app.py`, `api/tenant_boundary.py`, `demo/agencia-inmobiliaria.*` (originales), PDR/proposal/design/specs/tasks. WACRM: sólo referencia auditada, sin fork/copia/dependencia.

## Verificación ejecutada (comandos exactos y salidas)

### Ejecutable (no sólo sintaxis)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api` | exit 0 |
| `python -c "import api.composition_wu8_mvp, api.demo_run"` | `IMPORT_OK` |
| `npm run typecheck` (tsc --noEmit, include cubre web/**, contracts/**) | exit 0, sin errores |
| `npm test` (vitest run, raíz workspace) | **7 files / 55 tests passed** (incluye `web/app/demo-api.test.ts` 10 tests) |
| `NEXT_PUBLIC_DEMO_API_BASE_URL=http://127.0.0.1:8001 npm.cmd --workspace web run build` | ✓ Compiled successfully; rutas `/` (8.03 kB) y `/_not-found` prerendered; único warning: autoprefixer `flex-start/end` mixto heredado del CSS demo (preexistente en el adaptador, sin efecto) |
| `python -m api.demo_run` (env RUNTIME_API_HOST/PORT + RUNTIME_DB_* → `production_runtime_mvp_wu8`) | arriba; `GET /v1/demo/context/current` → 200 con proyección server-derived |
| API por curl: select A→B, dashboard (miss→hit con `X-Cache-Status`), switch a A (miss nuevo por scope A), inbox/leads/properties/pipeline/agenda/activity → 200, POST reset → `{"reset":true,...}` + dashboard miss | todos 200, correlación header/body coherente |
| CORS preflight `OPTIONS /v1/demo/context/current` con `Origin: http://127.0.0.1:3000` | 200 + `access-control-allow-origin` |

### Navegador real (Edge 152 headless vía CDP; Chromium de browser-use no instalable por fallo de red del mirror)

Con `next start` (build con la env inline) + API 8001 arriba:

- **Carga inicial (A)**: KPIs leídos de la base — conversaciones abiertas 1 (1 sin leer), leads 1 `new`, propiedades 1 `available`, seguimientos 1 (1 vencidos); conversación "Camila Torres · I would like details about the Palermo loft." — sin panel de error.
- **Recarga**: mismo contenido persistido (determinista), cero errores.
- **Switch A→B** (click en `.account-option` Litoral): cabecera y KPIs cambian a datos de B (lead `qualified`, 0 sin leer, hilo "Bruno Salvatierra"), botón `selected` se mueve — prueba el selector visible y el cache por scope.
- **6 vistas**: inbox (Inbox · 1), leads (tabla con 1 fila), properties (1), pipeline, agenda (1 pendientes), activity (0, estado vacío honesto) — todas con sección visible renderizada y sin `.error-panel`.
- **Drill-in hilo**: click en conversación del inbox → "Mensajes · The Fisherton house is available for a visit. outbound · 12:05".

## Diseño y decisiones

- **Composición WU8 propia en vez de reusar la app WU7**: la app WU6 fija la cuenta de sesión al build (sin selector) y la WU7 sólo expone dashboard+inbox; el dashboard necesita las 8 superficies bajo la cuenta de sesión gobernada por el selector. Mismo patrón aceptado: se muta `app.state` sólo tras `select` exitoso; rollback = borrar `composition_wu8_mvp.py` + `demo_run.py` + `web/app`.
- **CORS dev-only acotado a los dos orígenes locales del dashboard**, solo en la app de demo WU8. Alternativa descartada: proxy Next (más superficie de config sin valor para la demo).
- **`loop` factory explícito en `demo_run.py`** porque el default de uvicorn en win32 es Proactor por diseño de su factory `auto`; el import-string `api.demo_run:_selector_loop` es la vía soportada sin parchear librerías.
- **Sin estado de fixture en el cliente**: loaders por vista con el mismo `RuntimeHttpClient`/parsers de red que cubren los tests unitarios; el stub de `demo-api.test.ts` sólo reemplaza `fetch`, no los parsers.
- **`as_of` viene de la DB** (WU6), la UI lo muestra ("DATOS AL … UTC") en vez de calcular "hoy" en el navegador.

## Limitaciones (demo, documentadas)

- Sesión single-user dev-only en `app.state` del proceso; cache process-local (heredado de W7). No es infraestructura productiva.
- El selector lista dos cuentas hard-codeadas en la UI (`DEMO_ACCOUNTS`) porque el seed demo define exactamente esas dos; una cuenta no-seed → 403 no-enumerating del resolver (sin ruta de listado de membresías en MVP).
- `RUNTIME_DEMO_ACCOUNT` fija la cuenta inicial del servidor; tras cualquier `select`, la UI manda.
- Verificación navegador sobre build de producción local; sin Playwright/Cypress ni CI (fuera de alcance explícito).
- El warning de build "Request to … failed" que aparece si `next start` corre sin la env es el camino fail-fast esperado (`resolveDemoApiBaseUrl`), no un estado roto: la UI muestra el error del envelope sin inventar datos.

## Reproducibilidad

```text
# terminal 1 (repo root)
RUNTIME_API_HOST=127.0.0.1 RUNTIME_API_PORT=8001 RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 \
RUNTIME_DB_NAME=production_runtime_mvp_wu8 RUNTIME_DB_USER=postgres python -m api.demo_run
# terminal 2
NEXT_PUBLIC_DEMO_API_BASE_URL=http://127.0.0.1:8001 npm.cmd --workspace web run build && \
NEXT_PUBLIC_DEMO_API_BASE_URL=http://127.0.0.1:8001 npm.cmd --workspace web run start
# navegador: http://127.0.0.1:3000
```

Lockfile único en la raíz del workspace (sin lockfiles anidados nuevos). Sin cambios en package.json de esta sesión.

## Aceptación

**Aceptado para dashboard Next.js con datos reales y composición demo local.** Verificado en vivo: 8 superficies demo con datos PostgreSQL, aislamiento por scope, cache hit/miss, preflight CORS dev-only y build Next. Límites dev-only registrados (single-user, cache process-local, sin membership listing).
