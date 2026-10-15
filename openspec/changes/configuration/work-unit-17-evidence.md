# Work Unit 17 Evidence: Configuration (change: configuration)

Status: **implemented, pending review** — verificado directamente (compileall, harness nuevo 35/35 ×2 con output idéntico, fix de fechas WU16 → 46/46, units de configuración 6/6 + 13/13 + 9/9, units de consola 21/21 + 25/25 + 8/8 + 17/17, units puras 17/17 + 15/15 + 10/10, kb_retrieval 21/21, regresión WU13 22/22 + WU12 13/13 + WU11 16/16 + WU6 13/13, vitest 109/109 en 10 suites, typecheck + build web) contra PostgreSQL 18 real. Además se corrigió la regresión D8 (seed 010 en bases pre-010, fix `c5b0ace`: seeds gated por ledger). Deuda restante (pre-existente, no de esta unidad): WU15 (27/30) y WU14 (17/24) siguen con fechas hardcodeadas vencidas (D7).

## Scope

Harness de configuración Phase 8 (`api/test_configuration_wu17.py`) sobre base descartable `production_runtime_mvp_wu17` (runner 001–010 + `mvp_seed.sql` + `_005` + `_008` + `_009` + `_010`) con `StubModelProvider`: cubre T1–T7 del Testing Strategy del diseño contra las rutas reales (calendar-config con `timezone`, custom-fields CRUD, leads/{id}/custom-values, contacts + tags, lead-fields) más probes 401/404/405. Phase 9: fix de fechas en `test_agent_console_wu16.py` (K4/K6 relativas a `now`) y barrido de regresión completo. El harness usa fechas RELATIVAS a `datetime.now(timezone.utc)` (lección del envejecimiento de fechas de WU16) y genera efectos exclusivamente por el router real — nunca inserta filas a mano.

## Archivos

- `api/test_configuration_wu17.py` — harness nuevo T1–T7 + envelope (35 escenarios, base propia `production_runtime_mvp_wu17`, preclean espejo de WU16 + limpieza de custom-fields + reconvergencia al final).
- `api/test_agent_console_wu16.py` — **solo el fix de fechas** (K4/K6: `2026-09-19/21/22` → sábado/entre-semana relativos a `now`; 46/46).
- `openspec/changes/configuration/work-unit-17-evidence.md` — este archivo (draft).

## Decisiones implementadas

- **Fechas relativas, nunca absolutas**: el bug documentado de WU16 (`free_slots` ventana `[now, now+7d]` ya no contiene las fechas 2026-09-*) se replicó en el harness nuevo y en el fix de WU16. En el harness, `now + N días` calculados in-script; en WU16, un sábado (`weekday()==5`) + dos días de entre-semana distintos dentro de la ventana (ver D3).
- **T8 como barrido, no como escenario**: los harnesses hermanos (WU14/15/16) asertan su propio `RUNTIME_DB_NAME` y se niegan a correr sobre `wu17`; T8 es el barrido de regresión Phase 9 (comandos separados sobre sus bases), no un escenario interno.
- **Bodies distintos por escenario**: ninguna escritura de consola reutiliza payload entre escenarios; las escrituras de valores usan la key por contenido `console:{account}:custom-value:{hash}` sin `idempotencyKey` de cliente, así cada `(lead, field, value)` distinto es una escritura idempotente propia.
- **Comparación de seam por lectura directa**: el check crítico "UI availability == agent free_slots" (T1) lee `GET /calendar/availability` por el router y calcula `free_slots` con `load_calendar_config` + un `TenantContext` manual (mismo patrón que `test_configuration_timezone.py` Z4).
- **DELETE con body**: la ruta `DELETE /agent/contacts/{id}/tags` lee `{tag}` del body; el `TestClient` de starlette no acepta `json=` en `.delete()`, así que el harness usa `client.request("DELETE", …, json=…)` (ver D2).

## Verificación (PostgreSQL 18 real)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api infra` | exit 0 |
| `python -m api.test_configuration_wu17` (base `production_runtime_mvp_wu17`, run 1) | **35/35** passed |
| `python -m api.test_configuration_wu17` (misma base, run 2) | **35/35** passed (determinismo: output byte-idéntico) |
| `python -m api.test_agent_console_wu16` (base `production_runtime_mvp_wu16`) | **46/46** passed (fix de fechas, ver D3) |
| `python -m api.test_agent_autonomy_wu15` (base `production_runtime_mvp_wu15`) | **27/30** (B4/B6 ×3 — fechas vencidas, ver D7) |
| `python -m api.test_agent_wu14` (base `production_runtime_mvp_wu15`) | **17/24** (A10/A13–A17 — fechas vencidas, ver D7) |
| `python -m api.test_calendar_wu13` (base `production_runtime_mvp_wu13`) | 22/22 (regresión) |
| `python -m api.test_audit_screen_wu12` (base `production_runtime_mvp_wu12`) | 13/13 (regresión) |
| `python -m api.test_property_catalog_wu11` (base `production_runtime_mvp_wu11`) | 16/16 (regresión) |
| `python -m api.test_e2e_mvp` (base `production_runtime_mvp_wu10`) | **12/12** (E11 arreglado por el fix D8, ver D8) |
| `python -m api.test_simulator` (base `production_runtime_mvp_wu9`) | **12/12** (S10/S11 arreglados por el fix D8, ver D8) |
| `python -m api.test_crm_reads` (base `production_runtime_mvp_wu6`) | 13/13 (regresión) |
| `python -m api.test_configuration_timezone` (base `production_runtime_mvp_wu17`) | 6/6 (units) |
| `python -m api.test_configuration_custom_fields` (idem) | 13/13 (units) |
| `python -m api.test_configuration_tags` (idem) | 9/9 (units) |
| `python -m api.test_agent_console_settings` (base `production_runtime_mvp_wu16`) | 21/21 (units) |
| `python -m api.test_agent_console_prompts` (idem) | 25/25 (units) |
| `python -m api.test_agent_console_kb` (idem) | 8/8 (units) |
| `python -m api.test_agent_console_calendar` (idem) | 17/17 (units) |
| `python -m api.test_agent_policy` | 17/17 (units) |
| `python -m api.test_kb_retrieval` (base `production_runtime_mvp_wu16`) | 21/21 (units) |
| `python -m api.test_conversation_agent` | 15/15 (units) |
| `python -m api.test_model_provider` | 10/10 (units) |
| `npm.cmd run typecheck` | exit 0 |
| `npm.cmd exec vitest run` (repo) | **109/109** tests en 10 suites |
| `npm.cmd --workspace web run build` (web) | exit 0 (`Compiled successfully`) |
| Browser/manual demo pass | **skipped** (no requerido para este batch) |

T1–T7: timezone seed = `BUSINESS_TIMEZONE`, IANA inválido 400, PUT NY round-trips y el mismo instante cae en hora local distinta (NY 08:00 vs 09:00), UI availability == agent free_slots (seam, 19 == 19), restore BA; custom-field create/edit/delete + round-trip de valor; allowlist seed `{stage_id,status}` + round-trip fijo + nombre custom rechazado 400 (D4) + indefinido 400; matriz de coerción (number 195000, no-número 400, date ISO, fecha inválida 400, select ∈ choices, select fuera 400, campo indefinido 400); tags add lowercases + dedupe + append sin duplicados + `?tag=` filter + remove case-normaliza + filter vacío; ids fantasma/extranjeros 404 no-enumerantes en las siete rutas; stub offline (`langchain_openai` fuera de `sys.modules`); 401 sin identidad, 404/405, clave desconocida 400.

## Desviaciones verificadas desde código (D1–Dn)

- **D1** — *pre-010 tolerant write fix* (ya implementado, Phase 2): `UpdateCalendarConfigCommand._apply` detecta `information_schema.columns` para `agent_calendar_config.timezone` y escribe sobre las columnas 009 cuando la base no tiene 010 (`api/application/agent_commands_wu16.py:597-655`). El diseño no preveía la escritura tolerante; sin esto un PUT de calendario sobre una base pre-010 reventaba.
- **D2** — *runtime-client.ts DELETE-body extension* (ya implementado, Phase 5): la ruta de eliminación de tag es `DELETE /agent/contacts/{id}/tags` y LEE el body `{tag}` (`api/routes/agent_wu14.py:1699-1730`), en contra del diseño que proponía `PUT /agent/contacts/{id}/tags {add: string[], remove: string[]}`. La implementación real usa POST (agregar un tag) + DELETE (quitar un tag) con `{tag}` único. El `TestClient` de starlette no acepta `json=` en `.delete()`, así que el harness usa `client.request("DELETE", …, json=…)`.
- **D3** — *WU16 date-aging fix* (este batch): `test_agent_console_wu16.py` tenía fechas hardcodeadas `2026-09-19/21/22`; con el reloj en 2026-10-14 la ventana `[now, now+7d]` ya no las contiene y K4/K6 fallaban con `event=False` (booking → escalate). Se reemplazaron por un sábado y dos días de entre-semana calculados relativos a `datetime.now(timezone.utc)` (`api/test_agent_console_wu16.py:269-297`), manteniendo la intención original (escalamiento solo-días-hábiles, slot :30, restore del kill-switch). 46/46.
- **D4** — *allowlist write path no admite nombres de custom fields*: el diseño (Phase 3/5) decía que `UpdateFieldAllowlistCommand` se ampliaba a columnas fijas ∪ nombres definidos y que `PUT /agent/lead-fields` aceptaría un nombre custom. La implementación real **no** lo hace: el comando sigue validando solo `ALLOWLIST_COLUMNS = {stage_id, status, selected_property_id}` y rechaza cualquier nombre custom con 400 (`api/application/agent_commands_wu16.py:699-711`). Solo el READ path (`load_field_allowlist` con el JOIN a `field_definition_id`) y el DELETE de definición limpian refs custom. El harness T3 lo registra (nombre custom → 400) y `test_configuration_custom_fields.py` CF6 cubre el read path insertando a mano una fila con `field_definition_id`.
- **D5** — *`api/domain/custom_field.py` y `api/test_configuration_allowlist.py` no existen*: la lógica de coerción vive en `api/application/configuration_commands_wu17.py` (`CUSTOM_FIELD_TYPES` + `_coerce_value`), y el widening del allowlist se cubre en `test_configuration_custom_fields.py` (CF6), no en una unit separada como nombraba `tasks.md` Phase 3.
- **D6** — *envelope de los valores*: `PUT /agent/leads/{id}/custom-values` responde con `_console_ok("custom_values", {lead_id, values})` sin campo `replayed`, a diferencia de las otras escrituras de consola; el harness lo aserta con el helper `console_ok` estándar (sin `replayed`).
- **D7** — *WU15/WU14 siguen con fechas vencidas (fuera de alcance)*: `test_agent_autonomy_wu15.py` (27/30) y `test_agent_wu14.py` (12/24) sufren el MISMO envejecimiento de fechas que WU16 (A13/A14 y B6 muestran `starts=None` / booking sin evento con fechas 2026-09-*). Por la regla de este batch («Touch NOTHING else»; solo `test_agent_console_wu16.py` es editable) no se corrigieron. Pre-existente, no introducido por este cambio.
- **D8** — *E11/S10/S11 (wu10/wu9): seed 010 en bases pre-010 — CORREGIDO*: `MANAGED_SEED_FILES` incluía `SEED_010_FILE` y la ruta seed/reset ejecutaba `mvp_seed_010.sql` (cuyo `UPDATE agent_calendar_config SET timezone = …` referencia la columna 010) contra bases wu9/wu10 en migración 009 → `UndefinedColumn` 500. Fix `c5b0ace`: `_managed_seed_for_ledger` lee `max(version)` de `migration_ledger` y filtra los seeds managed que exceden el ledger de la base (`_MANAGED_SEED_MIN_VERSION`: base 1, 008→8, 009→9, 010→10); las bases pre-010 ya no corren el seed 010. Verificado: wu9 (009) reset+seed 200; E11/S10/S11 verdes (12/12 y 12/12).

## Limitaciones

- Harness autocontenido en `wu17`: el preclean elimina custom-fields/valores y resetea tags/timezone cada run, y la reconvergencia final deja la base en forma seed pura; no toca bases anteriores.
- El allowlist de custom fields solo es escribible por SQL directo (read path + delete limpian refs), no por el router (D4); el agente sigue escribiendo solo `stage_id`/`status` a través de `TransitionLeadStageCommand._apply`.
- Sin memoria conversacional más allá del fallback last-interest para precios.
- Reporte del harness evita flechas (codepage Windows); los archivos son UTF-8 sin BOM con acentos reales, mojibake 0.
- Fallos fuera de alcance registrados arriba: WU15 27/30 y WU14 17/24 (D7, fechas vencidas — pre-existente, no de esta unidad); demo manual skipeado.

## WACRM

Referencia auditada únicamente; sin fork, copia ni dependencia.
