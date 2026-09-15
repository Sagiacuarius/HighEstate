# MVP Commercial Demo — Work Unit 9 Evidence

## Status

Endpoint dev-only del simulador (`POST /v1/demo/simulate`) montado sobre la composición WU8 con el comando atómico WU5, botón etiquetado en el Inbox de la dashboard, y resolución verificada del bloqueo conocido del reset fail-closed. Harness Python 12/12 (dos corridas + una tercera tras el ciclo navegador) y tests vitest (capa fetch + botón renderizado con jsdom). **Implementado, pendiente de revisión de Leo.**

## Alcance (tasks.md WU9)

> Add dev-only simulation endpoint and labeled Inbox button using the approved canonical payload. Depends 5,7–8; `api/routes`, `web`. Verification: `pytest api/test_simulator.py`; click twice, one effect/result. Revert endpoint/button.

Cubierto:

- **Endpoint del simulador**: `api/routes/simulator_wu9.py` — `build_simulator_router()` monta `POST /v1/demo/simulate` en la app WU8 (sesión/selector/cache compartidos). Cuerpo opcional (`body`, `receivedAt`, `idempotencyKey`); el server es dueño de scope, evento, conversación y clave de fallback. Envuelve el `ProcessInboundMessageCommand` aceptado (WU5): mensaje inbound normalizado + lead create/update + transición de etapa + follow-up + audit + outbox en una transacción. Etiquetas explícitas: `environment: "dev-only-simulator"`, prefijo `/v1/demo`, leyenda en UI "dev-only (no Kapso, no WhatsApp)". Cero Kapso/WhatsApp/LLM.
- **Payload canónico**: el contrato de evento inbound aprobado en WU1/WU4 (`eventId/clientAccountId/conversationId/message{id,conversationId,clientAccountId,direction,body,receivedAt}`). `eventId = sha256(cuenta|body|receivedAt)` y `message.id = uuid5(eventId)`: mismo click → mismo evento determinístico; la protección de replay vive en la reserva de idempotencia WU5 (key explícita o `sim-event:{eventId}`).
- **Botón en Inbox** (`web/app/page.tsx` + CSS WU9): panel "Simulador · dev-only (no Kapso, no WhatsApp)" con botón "Simular inbound"; nota de resultado (aplicada vs replay), desabilitado en vuelo (doble submit no genera segundo POST), denegaciones mostradas como `status · code` del envelope sin romper la vista. La lógica de ramas vive en helpers puros (`describeSimulateResult`, `isResetBlockedError`) usados por la página y cubiertos por tests.

## Bloqueo conocido resuelto (reset fail-closed con efectos de simulador)

Hallazgo documentado en evidencia WU7: `mvp_reset.sql` acepta sólo borra filas `demo-*`, así que los efectos del simulador (sin marca, hijos de filas seed) abortan el script por FK RESTRICT.

Resolución WU9 (opción "advertir + limpiar explícitamente", sin tocar los scripts aceptados):

- `POST /v1/demo/reset` sin parámetro: si hay efectos pendientes, **409 `demo_effects_pending`** con envelope v1 + correlación consistente + `pending_effects: N` (conteo SQL de las mismas filas que el clear acepta borrar). El 500 `demo_script_failed` del camino aceptado sigue intacto para cualquier otra falla del script; el estado seed no se muta.
- `POST /v1/demo/reset?clear_effects=true`: ejecuta `clear_simulator_effects()` — DELETEs sólo de filas **unmarked** de los efectos del comando (messages/follow_ups/leads/conversations/contacts/stages sin `demo_seed_key`) + restauración del stage seed de ambos leads + flush de `command_idempotency`/`audit_records`/`outbox_events` (mismas reglas probadas del helper `clear_unmarked` del harness WU7, extendido con la restauración del lead B). Reporta `cleared_effects: N`. Después corre el reset+seed aceptado: marcadores seed intactos, baseline determinístico restaurado.
- UI: botón "Reset" muestra "Reset bloqueado por 409 · demo_effects_pending" y recién ahí aparece **"Forzar reset"** (borde de peligro) que dispara la variante con `clear_effects=true` y muestra "Reset ok · cuenta … · N efectos limpiados".
- **Fixture de etapa del simulador**: el seed acepta un solo stage por pipeline, así que el efecto "advance" del comando no era visible. `ensure_simulator_stage()` crea determinísticamente (uuid5, idempotente) un segundo stage por cuenta demo, **sin marca `demo_seed_key` a propósito**: contribuye al conteo de bloqueo y lo limpia exactamente el mismo camino probado de force-reset. La contract de ownership seed/reset aceptada queda intacta (S9/S10 del harness lo prueban: el stage sin marca bloquea el reset y desaparece con el force).

## Archivos

Creados:

```text
api/routes/__init__.py                 — paquete de rutas versioned del demo
api/routes/simulator_wu9.py            — router simulate + payload canónico + ensure_simulator_stage
                                          + count_pending_effects + clear_simulator_effects
api/test_simulator.py                  — harness escenario-tupla S1–S12 + wrapper pytest
web/app/simulator-button.test.tsx      — 5 tests vitest (jsdom + RTL) del botón real
openspec/changes/mvp-commercial-demo/work-unit-9-evidence.md
```

Modificados (acotados a WU8/WU9, nada aceptado de WU1–WU7 alterado):

```text
api/composition_wu8_mvp.py   — include_router(simulate) + reset effect-aware (409/force) +
                               expose_headers CORS (bug real: sin él el fetch cross-origin no lee
                               X-Correlation-Id y el cliente descartaba envelopes de error)
web/app/demo-api.ts          — simulate() + reset(clearEffects) + seam fetcher para tests + helpers puros WU9
web/app/page.tsx             — panel/botón del simulador, notas, Forzar reset tras 409
web/app/demo.css             — sección WU9 (simulator-panel, ghost-btn.danger)
contracts/runtime-v1.ts      — DemoSimulateResult/Response + parseDemoSimulateResponse + cleared_effects? en DemoControls
package.json / package-lock  — devDeps jsdom + @testing-library/react (lockfile único raíz)
```

Intactos: migraciones 001–004, runner, `mvp_seed.sql`/`mvp_reset.sql`, comandos/queries/repos WU3–WU6, identidad/cache/selector WU7, `demo/agencia-inmobiliaria.*`, PDR/proposal/design/specs/tasks. WACRM: sólo referencia auditada, sin fork/copia/dependencia.

## Matriz de escenarios (`python -m api.test_simulator`, 12/12, exit 0)

Base dedicada nueva: `production_runtime_mvp_wu9` (runner --up 001–004 + mvp_seed; `production_runtime_mvp_wu8` y anteriores intactas — ver "Bases" abajo).

```bash
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_mvp_wu9 \
RUNTIME_DB_USER=postgres python -m api.test_simulator
```

| # | Escenario | Resultado |
|---|---|---|
| S1 | Un click: message+lead update+transición al stage del simulador+follow-up+audit success+outbox=1, todo verificado con oráculos SQL directos | PASS |
| S2 | Doble click mismo payload: `replayed:true`, **mismo result exacto**, 1 fila de mensaje, 1 success audit | PASS |
| S3 | `idempotencyKey` explícita: dos clicks = replay idéntico; misma key + payload distinto = 409 envelope, sin mutación | PASS |
| S4 | Reentrega del mismo evento con key nueva: 403 no-enumerante `tenant_access_denied`, conteo de mensajes sin cambio | PASS |
| S5 | Body en blanco cae al canónico; `receivedAt` inválido → 400 `invalid_payload` antes de cualquier efecto | PASS |
| S6 | Tras simulate el scope cache del account cae: dashboard siguiente = miss y `unread_messages` coincide con SQL | PASS |
| S7 | Sesión en B (select server-owned): el simulate toca la conversación de B; A intacta (conteos exactos A=4/B=2) | PASS |
| S8 | Identidad ausente: 401 envelope con correlación header/body | PASS |
| S9 | Reset con efectos pendientes: **409 `demo_effects_pending`** + `pending_effects≥10` + correlación; conversaciones seed intactas | PASS |
| S10 | `?clear_effects=true`: `cleared_effects≥10`, cero filas unmarked, markers seed 2/2/2, lead A de vuelta al stage seed, dashboard miss con baseline | PASS |
| S11 | Reset simple sobre baseline limpio: 200 (sin 409) | PASS |
| S12 | GET sobre /v1/demo/simulate: 405 envelope (método estricto, registro fijo) | PASS |

Determinismo: corridas 1, 2 y 3 (ésta última después del ciclo completo de navegador que dejó efectos del click S1 del journey) → 12/12, exit 0. El bug encontrado y arreglado en el test mismo: comparación `UUID == str` en S1 (psycopg devuelve objetos `UUID`); fix en assertions del harness, cero cambios de producción.

## Tests UI (vitest)

- `web/app/simulator-button.test.tsx` (5): botón presente en Inbox con leyenda dev-only/no-Kapso/no-WhatsApp; click → `POST {}` a `/v1/demo/simulate` + reload de superficies; nota de replay distinta; disabled en vuelo con doble click = 1 POST; denial 403 muestra `403 · tenant_access_denied` y la vista sigue viva; "Forzar reset" **sólo aparece tras el 409** y llama `reset?clear_effects=true` mostrando `12 efectos limpiados`.
- `web/app/demo-api.test.ts` (+5 = 15): simulate parsea el contrato y rechaza `lead_created:"no"`; reset(true) arma la query correcta y parsea `cleared_effects`; helpers `describeSimulateResult`/`isResetBlockedError`.

## Verificaciones obligatorias (comandos exactos)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api` | exit 0 |
| `python -m api.test_simulator` (3x, una post-navegador) | 12/12, exit 0 |
| `python -m api.test_crm_commands` sobre su base (wu5) | 21/21, exit 0 — sin regresión |
| `python -m api.test_crm_reads` sobre su base (wu6) | 13/13, exit 0 — sin regresión |
| `python -m api.test_demo_context` sobre su base (wu7) | 14/14, exit 0 — sin regresión |
| `npm run typecheck` | exit 0 |
| `npm test` | 8 files / **65 tests** passed (55 WU8 previos + 10 WU9) |
| `NEXT_PUBLIC_DEMO_API_BASE_URL=http://127.0.0.1:8001 npm.cmd --workspace web run build` | ✓ Compiled successfully (warning preexistente autoprefixer del CSS demo) |
| `python -m api.demo_run` (base wu9) + `next start` + Edge headless CDP | journey completo abajo |

### Navegador real (Edge headless vía CDP — Chromium browser-use sigue sin instalar por red)

Panel visible en Inbox etiquetado "SIMULADOR · DEV-ONLY (NO KAPSO, NO WHATSAPP)". Click 1 → "Simulación aplicada · lead 65000000… · follow-up creado"; Click 2 → "Simulación reproducida (replay): mismo resultado, sin efecto extra". Leads muestra el lead `qualified` en la etapa del simulador; Actividad lista `crm.inbound.process · success` con correlación. Reset → "Reset bloqueado por 409 · demo_effects_pending" + botón "Forzar reset" → "Reset ok … 12 efectos limpiados"; el inbox posterior ya no contiene el mensaje simulado (baseline restaurado). FAILS: NONE.

## Bases

```text
production_runtime_mvp_wu9  ← creada con runner --up (001–004) + mvp_seed; usada por todo el harness WU9
wu2 2/2/0 · wu3 2/2/0 · wu4 2/2/0 · wu5 2/3/7* · wu6 2/2/1* · wu7 2/2/0 · wu8 2/2/0
(accounts/contacts/unmarked-messages; * residuos propios de los harness aceptados wu5/wu6, sin cambios nuevos)
```

## Limitaciones

- `ensure_simulator_stage` escribe SQL INSERT directo desde la ruta WU9 (idempotente, scope de cuenta ya resuelto por el server): es una fixture de demo, no un comando de dominio; el camino productivo de edición de pipeline queda fuera del MVP.
- El 409 `demo_effects_pending` usa el envelope de error v1 + campo extra `pending_effects` (aditivo); el contrato ErrorEnvelope aceptado no se modificó.
- `clear_simulator_effects` borra TODA la evidencia (idempotency/audit/outbox) de las dos cuentas demo en el force-reset — aceptable para un baseline de demo; un reset por-cuenta granular no está en alcance.
- `simulate` usa el `X-Correlation-Id` del request como correlación de comando vía el contexto resuelto; no acepta keys anidadas extra (pydantic-less shape deliberado: overrides opcionales, todo lo demás server-owned).
- La sesión sigue siendo process-local dev-only (límite heredado documentado en W7/W8). pytest no está instalado como runner: el módulo se ejecuta vivo (`python -m api.test_simulator`) con wrapper `test_simulator()` para cuando exista.

## Reproducibilidad

```text
# base wu9 limpia para el harness
python infra/migrations/runner.py --up            # con RUNTIME_DB_* -> production_runtime_mvp_wu9
psql ... -f infra/migrations/validation/mvp_seed.sql
RUNTIME_DB_NAME=production_runtime_mvp_wu9 python -m api.test_simulator
# demo end-to-end (ver README de WU8) + botonear en http://127.0.0.1:3000 → Inbox
```

## WACRM

Audited reference only; sin fork, copia ni dependencia.

## Acceptance status

Implementado y verificado contra PostgreSQL 18.6 real en base dedicada descartable + navegador real, sin regresión en WU5/WU6/WU7 y con los scripts seed/reset aceptados intactos.

**Aceptado para simulador dev-only + botón Inbox (12/12 Python, vitest 65/65).** Payload canónico determinístico, replay idempotente, reset con `clear_effects` explícito y CORS expuesto (X-Correlation-Id) verificados. Límite conocido: `ensure_simulator_stage` escribe fuera de la transacción del comando (fixture demo).
