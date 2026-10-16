# Work Unit 18 Evidence: Agent Custom Field Writes (change: agent-custom-field-writes)

Status: **implemented, pending review** — verificado directamente (compileall, harness nuevo 29/29 ×3, units de señales 15/15 + bindings 10/10 ×2 + policy 17/17 + conversation 15/15 + provider 10/10, units de consola/config, kb_retrieval 21/21 en wu16, regresión WU17 34/35 + WU16 46/46 + WU15 30/30 + WU14 24/24 + WU13/WU12/WU11/WU10/WU9/WU6, vitest 117/117 en 10 suites, typecheck, build web) contra PostgreSQL 18 real.

## Scope

Harness Phase 7 (`api/test_agent_custom_field_writes.py`) sobre base descartable nueva `production_runtime_mvp_wu18` (runner 001–011 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_008.sql` + `mvp_seed_009.sql` + `mvp_seed_010.sql` + `mvp_seed_011.sql`) con el `StubModelProvider` por defecto (sin API key): cubre C1–C9 del Testing Strategy del diseño contra las rutas reales (signal-bindings CRUD, custom-fields CRUD, lead-fields allowlist, analyze auto/manual, state, approve, drafts, custom-values) más probes 401/404/405 y regresión no-binding sobre la cuenta B. Phase 8: sweep completo + este archivo (draft). `api/test_agent_console_wu16.py` **sin cambios** (sigue 46/46, ver D4). Los fallos de `test_configuration_wu17` (T3) y `test_agent_console_settings` (S1) son expectativas viejas rotas por el cambio aceptado WU18 (D4 + AUTO widening) y están fuera del alcance de fix de este batch (ver D2, D3 y Limitaciones).

## Archivos

- `api/test_agent_custom_field_writes.py` — harness nuevo T1–T8 (29 escenarios, base propia `production_runtime_mvp_wu18`, preclean + fixture Palermo + pin `auto` + reconvergencia al final).
- `openspec/changes/agent-custom-field-writes/work-unit-18-evidence.md` — este archivo (draft).
- `api/test_agent_console_wu16.py` — **sin cambios**: 46/46 sobre `wu16` (ver D4).
- `api/test_agent_autonomy_wu15.py`, `api/test_agent_wu14.py` — **sin cambios**: 30/30 y 24/24 sobre `wu15`.

## Decisiones implementadas

- **Bodies distintos por escenario**: la fallback key de analyze es `sha256(account|conversation|body)` (lección WU14 A17) — ningún body se reutiliza entre escenarios que necesitan análisis fresco (cada analyze lleva su propio cierre: "gracias", "por favor", "genial", …).
- **IdempotencyKey explícita en cada escritura de consola** (`k18-*`): la route deriva `console:{account}:{entity}:{hash}` del contenido cuando no hay clave de cliente; cada escritura lleva clave única para no colisionar.
- **Setup por GET, no por POST**: el comando de modo usa key fija `agent-mode:{conv}:{mode}`; el preclean SQL ya deja `auto` y el setup solo lo aserta por GET (precedente D2 de WU16).
- **Fixture Palermo en setup**: clon de PROP1 (`Depto en Palermo`, 195000, `demo_seed_key NULL` → el preclean lo elimina cada run), igual que WU15/WU16. Así los bodies de búsqueda resuelven ciudad+presupuesto.
- **T4 denial vía approve manual**: `_derive_actions` filtra en analyze lo no-allowlisted (nunca emite), así que el denial de dispatch (`lead_field_not_allowed` en `execution_result`) se ejercita con el flujo manual — analyze en `manual` (draft `proposed` con la acción), angostar allowlist, approve (dispatch con el allowlist vigente niega, la saga finaliza `executed`), restaurar allowlist + modo `auto` (`api/test_agent_custom_field_writes.py:676`).
- **DELETE de binding nunca-bound para el 404**: el DELETE usa key fija `console:{account}:signal-binding:{key}` (replay ante la misma key), así que el 404 se prueba con una clave válida jamás bindeada (`email`), no con doble DELETE.
- **Fechas relativas, sin reservas**: ningún escenario reserva visita; `fecha_objetivo` lleva el próximo martes relativo en ISO (`api/test_agent_custom_field_writes.py:309-318`), nunca una fecha absoluta.
- **Reconvergencia al final del harness**: preclean sin fixture al cerrar (`api/test_agent_custom_field_writes.py:760`), dejando la base en forma seed pura.
- **Teléfono canónico único** `+54 9 11 1234-5678` → `+5491112345678` (13 dígitos, dentro de [7,15]); el `200000` del presupuesto (6 dígitos) nunca califica como candidato telefónico.

## Verificación (PostgreSQL 18 real)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api infra` | exit 0 |
| base nueva: `dropdb --if-exists` + `createdb` + `RUNTIME_DB_NAME=production_runtime_mvp_wu18 ... python infra/migrations/runner.py --up` | exit 0 (001–011 aplicadas) |
| `psql ... -f mvp_seed_005/008/009/010/011.sql` sobre `wu18` | exit 0 (COMMIT ×5, convergen; ver D5 por `mvp_seed.sql`) |
| `python -m api.test_agent_custom_field_writes` (base `production_runtime_mvp_wu18`, run 1) | **29/29** passed |
| `python -m api.test_agent_custom_field_writes` (misma base, run 2) | **29/29** passed (determinismo: conteos idénticos) |
| `python -m api.test_agent_custom_field_writes` (tras re-seed psql, run 3) | **29/29** passed (base sana) |
| `python -m api.test_agent_signals` | 15/15 (units) |
| `python -m api.test_agent_signal_bindings` (base `production_runtime_mvp_wu18`, ×2) | 10/10 + 10/10 (units) |
| `python -m api.test_agent_policy` | 17/17 (units) |
| `python -m api.test_conversation_agent` | 15/15 (units) |
| `python -m api.test_model_provider` | 10/10 (units) |
| `python -m api.test_configuration_timezone` / `custom_fields` / `tags` (base `production_runtime_mvp_wu17`) | 6/6 + 13/13 + 9/9 (units) |
| `python -m api.test_agent_console_settings` (base `production_runtime_mvp_wu16`) | **20/21** (S1 falla — ver D3, fuera de alcance) |
| `python -m api.test_agent_console_prompts` / `kb` / `calendar` (idem) | 25/25 + 8/8 + 17/17 (units) |
| `python -m api.test_kb_retrieval` (base `production_runtime_mvp_wu16`) | 21/21 (units) |
| `python -m api.test_configuration_wu17` (base `production_runtime_mvp_wu17`) | **34/35** (T3 falla — ver D2, fuera de alcance) |
| `python -m api.test_agent_console_wu16` (base `production_runtime_mvp_wu16`) | **46/46** passed (sin fix) |
| `python -m api.test_agent_autonomy_wu15` (base `production_runtime_mvp_wu15`) | **30/30** passed (sin fix) |
| `python -m api.test_agent_wu14` (idem) | **24/24** passed (sin fix) |
| `python -m api.test_calendar_wu13` (base `production_runtime_mvp_wu13`) | 22/22 (regresión) |
| `python -m api.test_audit_screen_wu12` (base `production_runtime_mvp_wu12`) | 13/13 (regresión) |
| `python -m api.test_property_catalog_wu11` (base `production_runtime_mvp_wu11`) | 16/16 (regresión) |
| `python -m api.test_e2e_mvp` (base `production_runtime_mvp_wu10`) | 12/12 (regresión) |
| `python -m api.test_simulator` (base `production_runtime_mvp_wu9`) | 12/12 (regresión) |
| `python -m api.test_crm_reads` (base `production_runtime_mvp_wu6`) | 13/13 (regresión) |
| `npm.cmd exec vitest run` (repo) | **117/117** tests en **10/10** suites (incluye la suite WU18 "Fuentes del agente") |
| `npm.cmd run typecheck` | exit 0 (cero errores) |
| `npm.cmd --workspace web run build` | exit 0 |
| Browser/manual demo pass | **skipped** (no requerido para este batch) |

T1–T8: binding CRUD por cuenta (upsert una fila por clave, lista con `field_name`/`field_type` vía JOIN, 400 ante señal desconocida en POST+DELETE, 404 no-enumerante ante field fantasma/extranjero, B vacío, DELETE de clave jamás bindeada 404, `deleted:true`, cascada al borrar la definición); extracción determinista de las 7 señales (phone `+5491112345678`, email `sofiagarcia@example.com`, presupuesto `200000`, barrio `Palermo`, tipo `apartment`, fecha martes relativo ISO, medio `phone`/`email` por palabra explícita y por canal resuelto; re-run replaya idéntico; FAQ sin señal emite cero acciones); AUTO escribe vía `execute_set` (fila `+5491112345678`, `outcome=success`, re-run upsert sin duplicados; `select` fuera de choices → `invalid` con mención a choices; `number`/`date` coercionan por la autoridad única); allowlist angostada niega `lead_field_not_allowed` con la saga en `executed` y el valor previo intacto, transición seed `stage_id+status` intacta hacia `Calificado`, nombre custom indefinido 400 (D4 cerrado, `field_definition_id` persistido); forjados/extranjeros/ghost 404 en drafts + custom-fields + binding cross-account; B sin bindings emite cero acciones y escribe cero filas; stub offline (`langchain_openai` fuera de `sys.modules`, modelos `stub-keyword-v1`); 401 sin identidad en settings+resume+bindings, 404 ruta desconocida, 405 en settings/state.

## Desviaciones verificadas desde código (D1–Dn)

- **D1** — `tasks.md` Phase 8 fija `test_configuration_wu17` en 35/35, pero el archivo tiene 36 `record()` y el observado es **34/35** (el fallo es D2). Pin desactualizado del planning; se reporta el observado.
- **D2** — `api/test_configuration_wu17.py:407` ("T3 allowlist custom-field name rejected by write path") espera que `PUT /agent/lead-fields` con un nombre custom falle; el cambio aceptado WU18 (cierre D4: `UpdateFieldAllowlistCommand` acepta fijas ∪ nombres definidos y persiste `field_definition_id`, `api/application/agent_commands_wu16.py:699-779`) lo convierte en éxito legítimo (`status=200`). Expectativa vieja del harness WU17, no regresión del producto. Fuera del alcance de fix de este batch (solo `test_agent_console_wu16.py` era editable, y no lo necesitó) — no se tocó.
- **D3** — `api/test_agent_console_settings.py:82-84` (S1) exige `len(loaded.matrix) == 7`; el Phase 4 aceptado sumó `set_custom_field_value` a `AUTO_ACTION_TYPES` y `load_console_settings` devuelve 8 claves en cualquier base (verificado: `wu16` está en ledger 009 con 7 filas seed, el octavo viene de las constantes). Pin desactualizado, no regresión. Fuera de alcance — no se tocó. Resto de la unit 20/21 en verde.
- **D4** — `api/test_agent_console_wu16.py` **no requirió ningún fix**: 46/46 sobre `wu16` (el archivo date-fixed está intacto). El permiso de fix de Phase 8 no se ejerció.
- **D5** — re-seed standalone de `mvp_seed.sql` sobre base poblada no converge (falla por RESTRICT `property_amenity_rel → properties`; verificado en `wu18`): ese script presupone el orden del preclean del harness (wipes + seeds), igual que en bases anteriores. Los seeds 005/008/009/010/011 sí convergen standalone (COMMIT ×5). Preexistente, no de este batch.
- **D6** — el harness no reserva visitas (ningún lookup de eventos): el idioma de fechas relativas de WU16 (`_now`/`_tz`/`_today`/`_next_day`, lunes + martes estrictamente posterior) se aplica solo al valor de la señal `fecha_objetivo` (`api/test_agent_custom_field_writes.py:291-318`). Cero fechas absolutas en bodies y SQL del harness.
- **D7** — el preclean WU18 extiende el WU17 con `DELETE FROM agent_signal_bindings` antes de `lead_custom_field_values`/`custom_field_definitions` (`api/test_agent_custom_field_writes.py:150`); `SEED_FILES` suma `mvp_seed_011.sql` (`api/test_agent_custom_field_writes.py:66`).

## Limitaciones

- Harness autocontenido en `wu18`: el preclean elimina la fixture Palermo y resetea stages/modos cada run, y la reconvergencia final deja la base en forma seed pura; no toca bases anteriores.
- `POST /v1/demo/agent/state` nunca fija `paused`; el flujo manual de T4 usa `manual`/`auto` únicamente.
- Sin memoria conversacional más allá del fallback last-interest para precios.
- El harness evita flechas en el reporte de consola (codepage Windows); los archivos son UTF-8 sin BOM con acentos reales, mojibake 0.
- Deuda conocida: bodies/palabras elegidos para no colisionar ramas del stub (sin dígitos ≥7 salvo el teléfono canónico, sin `?` salvo el FAQ de ausencia) ni keywords seed.
- Fallos fuera de alcance registrados arriba: WU17-T3 (D2) y settings-S1 (D3); demo manual skipeado.

## WACRM

Referencia auditada únicamente; sin fork, copia ni dependencia.
