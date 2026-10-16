# Work Unit 19 Evidence: Commercial Pipelines (change: commercial-pipelines)

Status: **implemented, pending review** — verificado directamente (compileall, harness nuevo 30/30 ×2, units de comandos 31/31, regresión WU18 29/29 + WU17 36/36 + WU16 46/46 + WU15 30/30 + WU14 24/24 + WU13/WU12/WU11/WU10/WU9/WU6, units Python 15/15 + 10/10 ×2 + 17/17 + 21/21 + 15/15 + 10/10 + 6/6 + 13/13 + 9/9 + 21/21 + 25/25 + 8/8 + 17/17, vitest 126/126 en 10 suites, typecheck, build web) contra PostgreSQL 18 real.

## Scope

Harness Phase 5 (`api/test_commercial_pipelines_wu19.py`) sobre base descartable nueva `production_runtime_mvp_wu19` (runner 001–011 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_008.sql` + `mvp_seed_009.sql` + `mvp_seed_010.sql` + `mvp_seed_011.sql`), sin modelo (esta unidad no llama a analyze en ningún escenario; stub-offline es N/A por construcción): cubre P1–P7 del Testing Strategy del diseño contra las rutas reales (create/rename de pipelines, create/rename/move/delete de stages, GET `/v1/demo/pipeline`, POST reset) más probes 401/404/405. Phase 6: sweep completo + este archivo (draft). Las fases 1–4 ya estaban commiteadas (`pipeline_commands_wu19.py`, `pipeline_wu19.py` + mount, contratos/cliente tipado/Embudo); este batch solo agrega harness + evidencia, sin commits ni push. El reset completo falla por el gap preexistente de junctions (D2); la supervivencia de filas asesoras se prueba con el slice verbatim de pares marcador (D2) y el preclean reconverge la base al final de cada run.

## Archivos

- `api/test_commercial_pipelines_wu19.py` — harness nuevo P1–P7 (30 escenarios, base propia `production_runtime_mvp_wu19`, preclean + reconvergencia al final; sin fixture de catálogo porque no hay búsqueda del agente).
- `openspec/changes/commercial-pipelines/work-unit-19-evidence.md` — este archivo (draft).
- Todo lo demás — **sin cambios** (`git status` limpio salvo los dos archivos nuevos).

## Decisiones implementadas

- **IdempotencyKey explícita y distinta por escritura** (`k19-*`): la route deriva `console:{account}:{entity}:{hash}` del contenido cuando no hay clave de cliente; cada escritura lleva clave única para no colisionar entre escenarios.
- **Sin fixture Palermo**: ningún escenario resuelve ciudad+presupuesto ni llama a analyze; el único uso directo de comando es `execute_transition` por id en P4 (superficie aceptada WU5, prueba que el rename conserva la resolución por id).
- **DELETE con body vía `client.request("DELETE", ..., json=...)`**: las rutas de delete solo aceptan `idempotencyKey`; el replay del delete usa la misma clave (`k19-p3-del`).
- **Preclean con alcance por nombre para la stage del simulador**: `DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL AND name <> 'Simulador: visita calificada'` (`api/test_commercial_pipelines_wu19.py`, preclean) — nunca borrado blanket por marcador NULL; más `DELETE FROM pipelines WHERE demo_seed_key IS NULL` después de stages (RESTRICT FKs). En `wu19` la stage del simulador nunca llega a existir (D6), así que la guarda es un no-op de seguridad.
- **P5 en tres pasos honestos**: plain reset 409 (fail-closed vigente) → force reset 500 por el gap preexistente (D2, probado con cero filas asesoras) → slice verbatim de `mvp_reset.sql:20-32` (messages, conversations, follow_ups, leads, stages, pipelines; properties/contacts omitidos por el mismo gap) que prueba que las pipelines asesoras NULL sobreviven y los marcadores se van.
- **Parser tolerante de kanban**: la vista usa `pipeline_id` (no `id`, D4) dentro de `items` (`GET /v1/demo/pipeline` devuelve `{"items": [...]}`); el orden kanban se compara contra `ORDER BY position, id` por SQL.
- **Reconvergencia al final del harness**: preclean completo al cerrar, dejando la base en forma seed pura (2 pipelines, 4 stages, 2 leads, cero filas NULL, idempotencia/auditoría/outbox vacíos — verificado por conteo).
- **405, no 404, para método erróneo en path existente**: `GET /stages/{id}` y `DELETE /pipelines/{id}` dan 405; el 404 se prueba sobre `/v1/demo/no-such-route` (D5).

## Verificación (PostgreSQL 18 real)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api infra` | exit 0 |
| base nueva: `dropdb --if-exists` + `createdb` + `RUNTIME_DB_NAME=production_runtime_mvp_wu19 ... python infra/migrations/runner.py --up` | exit 0 (001–011 aplicadas) |
| `psql ... -f mvp_seed.sql -f mvp_seed_005.sql -f mvp_seed_008.sql -f mvp_seed_009.sql -f mvp_seed_010.sql -f mvp_seed_011.sql` sobre `wu19` | exit 0 (COMMIT ×6) |
| `python -m api.test_commercial_pipelines_wu19` (base `production_runtime_mvp_wu19`, run 1) | **30/30** passed |
| `python -m api.test_commercial_pipelines_wu19` (misma base, run 2) | **30/30** passed (determinismo: conteos idénticos) |
| `python -m api.test_pipeline_commands_wu19` (base `production_runtime_mvp_wu18`) | **31/31** passed |
| `python -m api.test_agent_custom_field_writes` (base `production_runtime_mvp_wu18`) | **29/29** passed |
| `python -m api.test_configuration_wu17` (base `production_runtime_mvp_wu17`) | **36/36** passed (ver D1: el pin 35/35 de tasks está desactualizado) |
| `python -m api.test_agent_console_wu16` (base `production_runtime_mvp_wu16`) | **46/46** passed (sin fix) |
| `python -m api.test_agent_autonomy_wu15` (base `production_runtime_mvp_wu15`) | **30/30** passed (sin fix) |
| `python -m api.test_agent_wu14` (idem) | **24/24** passed (sin fix) |
| `python -m api.test_calendar_wu13` (base `production_runtime_mvp_wu13`) | 22/22 (regresión) |
| `python -m api.test_audit_screen_wu12` (base `production_runtime_mvp_wu12`) | 13/13 (regresión) |
| `python -m api.test_property_catalog_wu11` (base `production_runtime_mvp_wu11`) | 16/16 (regresión) |
| `python -m api.test_e2e_mvp` (base `production_runtime_mvp_wu10`) | 12/12 (regresión) |
| `python -m api.test_simulator` (base `production_runtime_mvp_wu9`) | 12/12 (regresión) |
| `python -m api.test_crm_reads` (base `production_runtime_mvp_wu6`) | 13/13 (regresión) |
| `python -m api.test_agent_signals` | 15/15 (units) |
| `python -m api.test_agent_signal_bindings` (base `production_runtime_mvp_wu18`, ×2) | 10/10 + 10/10 (units) |
| `python -m api.test_agent_policy` | 17/17 (units) |
| `python -m api.test_kb_retrieval` (base `production_runtime_mvp_wu16`) | 21/21 (units) |
| `python -m api.test_conversation_agent` | 15/15 (units) |
| `python -m api.test_model_provider` | 10/10 (units) |
| `python -m api.test_configuration_timezone` / `custom_fields` / `tags` (base `production_runtime_mvp_wu17`) | 6/6 + 13/13 + 9/9 (units) |
| `python -m api.test_agent_console_settings` / `prompts` / `kb` / `calendar` (base `production_runtime_mvp_wu18`) | 21/21 + 25/25 + 8/8 + 17/17 (units; settings exige `wu18`, ver D1) |
| `psql ... -f mvp_reset.sql` sobre `wu19` poblada (prueba manual del gap D2) | exit 3 (`ERROR: ... property_amenity_rel ... RESTRICT`, con cero filas asesoras también) |
| `npm.cmd exec vitest run` (repo) | **126/126** tests en **10/10** suites (incluye suites WU19 de contratos/cliente) |
| `npm.cmd run typecheck` | exit 0 (cero errores) |
| `npm.cmd --workspace web run build` | exit 0 |
| Browser/manual demo pass | **skipped** (no requerido para este batch) |

P1–P7: create con stages iniciales `0..2` + replay canónico sin stages extra + duplicados permitidos con ids distintos; rename con `agent_name_warning: false` y kanban idéntico antes/después (leads intactos); cross-account 404 + 6× forged/foreign/ghost 404 no-enumerante (solo short stage paths); blank/unknown keys 400; append en `max+1`; rename silencioso `unknown→unknown`, warning en `unknown→Calificado` y `Calificado→Posventa` (regla old-or-new), duplicado mismo-pipeline 409 `stage_name_already_exists`; swap down/up reflejado en kanban (`position, id` intacto en lectura), bordes no-op `{moved: false}`, replay de move con cero efectos extra; delete de stage con leads 409 `stage_has_leads` con `lead_count: 1` + auditoría de denegación + cero efectos; delete de vacía + replay canónico con una sola auditoría; rename de stage seed + `execute_transition` por id exitoso + rename-back; plain reset 409 `demo_effects_pending` (pending=65), force reset 500 `demo_script_failed` (D2), slice de pares: 2 pipelines asesoras sobreviven, 0 marcadores; 401 sin identidad, 404 ruta desconocida, 405 en stage/pipeline; sin imports de `langchain_openai` ni llamadas a analyze en toda la unidad.

## Desviaciones verificadas desde código (D1–Dn)

- **D1** — `tasks.md` Phase 6 fija `test_configuration_wu17` en 35/35 con T3 actualizado, pero el archivo tiene 36 `record()` y el observado es **36/36**: el T3 actualizado acepta nombres custom (cierre D4 de WU18) y hay un record extra de validación. Pin desactualizado del planning; se reporta el observado. Igual con `test_agent_console_settings`: el pin decía base `wu16`, pero el archivo commiteado (`798aea7`, batch WU18) exige `len(matrix_rows) == 8` para B (`api/test_agent_console_settings.py:84,126`) y `wu16` solo tiene 7 filas seed en B — en `wu18` (cadena de seeds 011 con `set_custom_field_value`) hay 8 y da **21/21**. Sobre `wu16` falla con `AssertionError: seed rows missing for B` (preexistente, fuera de alcance: solo el harness nuevo y esta evidencia eran editables). No se tocó ningún archivo existente.
- **D2** — `POST /v1/demo/reset?clear_effects=true` devuelve 500 `demo_script_failed` en `wu19`: el aceptado `mvp_reset.sql` borra `properties` sin limpiar antes `property_amenity_rel`/`property_contacts`/`property_interests` (esos deletes viven en `mvp_seed_005.sql:75-86`, no en el reset), así que el FK `property_amenity_rel_property_account_fk ... ON DELETE RESTRICT` dispara. Probado manualmente con cero filas asesoras (`psql -f mvp_reset.sql`, exit 3) — el gap es independiente de este batch (misma clase que D5 de WU18) y no se puede corregir aquí (límite de alcance: solo los dos archivos nuevos). La afirmación de supervivencia se prueba en su lugar con el slice verbatim de `mvp_reset.sql:20-32` (P5: 2 pipelines asesoras NULL sobreviven, 0 marcadores).
- **D3** — `clear_simulator_effects` (`api/routes/simulator_wu9.py:169`) borra `pipeline_stages WHERE demo_seed_key IS NULL`: por el path de force, las stages asesoras NO sobreviven (solo las pipelines, que ninguna limpieza toca). Superficie aceptada WU9, fuera de alcance — se documenta, no se cambia.
- **D4** — la vista kanban usa la clave `pipeline_id` (no `id`) dentro de `items` (`api/adapters/postgres_crm_reads.py:177-178`, `api/queries/crm_reads.py:114` devuelve `{"items": ...}`). El harness lo parsea así (`api/test_commercial_pipelines_wu19.py`, `_kanban_names`).
- **D5** — método erróneo sobre path existente da 405, no 404 (`api/composition_wu8_mvp.py:428-432`): `GET /stages/{id}` y `DELETE /pipelines/{id}` → 405; el 404 se prueba sobre `/v1/demo/no-such-route` (handler en `:423-426`, código `route_not_found`).
- **D6** — la stage NULL del simulador (`Simulador: visita calificada`, `api/routes/simulator_wu9.py:56,99-120`) nunca existe en `wu19`: solo se inserta vía la ruta simulate y únicamente si la posición 1 está libre, pero el seed `Calificado` la ocupa. La guarda por nombre del preclean es un no-op de seguridad, tal como pide el diseño.
- **D7** — bug propio del harness (sin impacto en producto): el `last_id` inicial usaba `ORDER BY position, id DESC` (el DESC solo aplicaba a `id` y devolvía la primera stage); corregido a `ORDER BY position DESC, id DESC`. El swap esperado también se calcula ahora por índice dinámico en vez de asumir la posición de `Negociación`.
- **D8** — el replay del move devuelve el payload almacenado (`moved: true`) con `replayed: true` y cero efectos nuevos: el harness lo aserta comparando posiciones SQL antes/después, no el flag `moved`.

## Limitaciones

- Harness autocontenido en `wu19`: el preclean elimina las filas asesoras NULL y resetea leads/stages cada run, y la reconvergencia final deja la base en forma seed pura (2 pipelines, 4 stages, 2 leads, evidencia vacía); no toca bases anteriores.
- Sin llamadas al agente en toda la unidad: P4 usa `execute_transition` directo (id-resolución), nunca analyze/approve; sin fechas, sin reservas, sin fixture de catálogo.
- El harness evita flechas en el reporte de consola (codepage Windows); los archivos son UTF-8 sin BOM con acentos reales, mojibake 0.
- Fallos fuera de alcance registrados arriba: reset completo 500 (D2) y settings-sobre-wu16 (D1); demo manual skipeado.
- Deuda conocida: `mvp_reset.sql` no limpia las junctions de catálogo antes de `properties` (D2) — le corresponde a otra unidad con permiso sobre `infra/`.

## WACRM

Referencia auditada únicamente; sin fork, copia ni dependencia.
