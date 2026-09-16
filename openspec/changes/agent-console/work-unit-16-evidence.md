# Work Unit 16 Evidence: Agent Console (change: agent-console)

Status: **implemented, pending review** — verificado directamente (compileall, harness nuevo 46/46 ×4, units de consola 21/21 + 25/25 + 8/8 + 17/17, units puras 10/10 + 15/15 + 17/17, kb_retrieval 21/21 en wu16, regresión WU15 30/30 + WU14 24/24 + WU13/WU12/WU11/WU6, vitest 85/85 con 1 suite sin parsear, build web) contra PostgreSQL 18 real.

## Scope

Harness de consola Phase 10 (`api/test_agent_console_wu16.py`) sobre base descartable `production_runtime_mvp_wu16` (runner 001–009 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_009.sql` — nombre real del archivo, ver D1) con `StubModelProvider`: cubre K1–K10 del Testing Strategy del diseño contra las rutas reales (settings, prompts + preview + revert, KB CRUD + self-test, calendar-config, lead-fields, analyze auto/escalate, state, resume, approve) más probes 401/404/405 y regresión de autonomía WU15 sobre la misma base. Phase 11: `test_agent_autonomy_wu15.py` y `test_agent_wu14.py` **sin cambios** (cero fixes); la base `wu15` se migró a 009 + seed 009 (ver D3) para que el pin sobreviva al edit aceptado #2. Los fallos de `test_e2e_mvp` (11/12), `test_simulator` (10/12) y la suite `contracts/runtime-v1.test.ts` son preexistentes del trabajo Phase 0–9 y están fuera del alcance de fix de este batch (ver D4, D5 y Limitaciones).

## Archivos

- `api/test_agent_console_wu16.py` — harness nuevo K1–K10 + envelope (46 escenarios, base propia `production_runtime_mvp_wu16`, preclean + fixture Palermo + pin `auto` + reconvergencia al final).
- `openspec/changes/agent-console/work-unit-16-evidence.md` — este archivo (draft).
- `api/test_agent_autonomy_wu15.py` — **sin cambios**: 30/30 sobre `wu15` (base migrada a 009, D3).
- `api/test_agent_wu14.py` — **sin cambios**: 24/24 sobre `wu15` (idem).

## Decisiones implementadas

- **Bodies distintos por escenario**: la fallback key de analyze es `sha256(account|conversation|body)` (lección WU14 A17) — ningún body se reutiliza entre escenarios que necesitan análisis fresco.
- **IdempotencyKey explícita en cada escritura de consola**: la route deriva `console:{account}:{entity}:{hash}` del contenido cuando no hay clave de cliente; dos escenarios con el mismo payload colisionan y el segundo replays sin aplicar (mordió en K6-global y K10; ver D2). Cada escritura lleva clave única (`k1-off`, `k2-v2`, …).
- **Setup por GET, no por POST**: el comando de modo usa key `agent-mode:{conv}:{mode}`; un pin `auto` en setup consume la key que el unpin de K9 necesita fresca (mismo key + mismo hash = replay sin aplicar, la base quedaba en `manual`). El preclean SQL ya deja `auto`; el setup solo lo aserta por GET (ver D2).
- **Fixture Palermo en setup**: igual que WU15 (clon de PROP1, `Depto en Palermo`, 195000, `demo_seed_key NULL` → el preclean lo elimina cada run). Así K5 resuelve ciudad+presupuesto.
- **Pregunta KB con tokens fuera del universo seed**: el scoring es overlap de keywords (`KB_SCORING_SQL`, hits > 0) y `reserva` existe como keyword seedada en ambas cuentas — la pregunta usa `¿Aceptan trueque de bicicletas como seña?` con keywords `trueque/bicicletas/seña/tango`, que no aparecen en ninguna keyword de `mvp_seed_008.sql` (verificado por corpus).
- **Cuerpo FAQ puro en K6-global**: `¿Cuánto tarda la escritura de una propiedad?` clasifica `search_properties` (contiene `propiedad`, que precede a la rama `ask` en `_classify`); se usa `¿Cuánto tarda la escritura?` (hit KB 003, `policy=auto`, suprimido por `global_auto=FALSE`).
- **Reconvergencia al final del harness**: el preclean se re-ejecuta sin fixture al cerrar, porque las units de consola cuentan tamaños exactos de corpus sobre esta misma base (KB 32, prompts v1 activa); sin esto, `test_agent_console_kb` (34) y `test_agent_console_prompts` (R9) fallan por residuos del harness.
- **Handoff doble en A, resume con replay honesto**: el segundo resume devuelve 200 con el outcome guardado sin re-aplicar (dev-only one-shot, precedente D5 de WU15): cero audits `crm.agent.mode` nuevos y la conversación sigue `paused` — el preclean del próximo run la devuelve a `auto`.

## Verificación (PostgreSQL 18 real)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api infra` | exit 0 |
| `python -m api.test_agent_console_wu16` (base `production_runtime_mvp_wu16`, run 1) | **46/46** passed |
| `python -m api.test_agent_console_wu16` (misma base, run 2) | **46/46** passed (determinismo: conteos idénticos) |
| `python -m api.test_agent_console_wu16` (código final con reconvergencia, run 3) | **46/46** passed |
| `python -m api.test_agent_console_wu16` (código final, run 4) | **46/46** passed (determinismo final: conteos idénticos) |
| `python -m api.test_agent_console_settings` (base `production_runtime_mvp_wu16`) | 21/21 (units) |
| `python -m api.test_agent_console_prompts` (idem) | 25/25 (units) |
| `python -m api.test_agent_console_kb` (idem) | 8/8 (units) |
| `python -m api.test_agent_console_calendar` (idem) | 17/17 (units) |
| `python -m api.test_model_provider` | 10/10 (units) |
| `python -m api.test_conversation_agent` | 15/15 (units) |
| `python -m api.test_agent_policy` | 17/17 (units) |
| `python -m api.test_kb_retrieval` (base `production_runtime_mvp_wu16`, ver D3) | 21/21 (units) |
| `python -m api.test_agent_autonomy_wu15` (base `production_runtime_mvp_wu15` migrada a 009, ver D3) | **30/30** passed (sin fix) |
| `python -m api.test_agent_wu14` (idem) | **24/24** passed (sin fix) |
| `python -m api.test_calendar_wu13` (base `production_runtime_mvp_wu13`) | 22/22 (regresión) |
| `python -m api.test_audit_screen_wu12` (base `production_runtime_mvp_wu12`) | 13/13 (regresión) |
| `python -m api.test_property_catalog_wu11` (base `production_runtime_mvp_wu11`) | 16/16 (regresión) |
| `python -m api.test_e2e_mvp` (base `production_runtime_mvp_wu10`) | **11/12** (E11 falla — ver D4, fuera de alcance) |
| `python -m api.test_simulator` (base `production_runtime_mvp_wu9`) | **10/12** (S10/S11 fallan — ver D4, fuera de alcance) |
| `python -m api.test_crm_reads` (base `production_runtime_mvp_wu6`) | 13/13 (regresión) |
| `npm.cmd exec vitest run` (repo) | **85/85** tests en 9 suites + **1 suite sin parsear** (`contracts/runtime-v1.test.ts:540`, ver D5) |
| `npm.cmd run build` (web) | exit 0 (`LASTEXITCODE=0`, `Compiled successfully`) |
| Browser/manual demo pass | **skipped** (no requerido para este batch) |

K1–K10: settings iguales a las constantes (`AUTO_ACTION_TYPES`/`AUTO_EVENT_CREATION`, matriz de 7, aislamiento por cuenta, 400 ante clave desconocida, 1 audit); prompts v1 preview byte-idéntico a `build_system_prompt()`, v2 + activa, skeleton 400, revert inserta v3, 2 audits; KB CRUD + self-test hit sobre artículo seed y propio + miss, deactivado invisible y cross-account invisible, 400 categoría / 404 ghost; calendario seed = `BUSINESS_*`, override 10–16h/30m/weekdays-only (sábado escala, slot :30 reserva), ventanas inválidas 400, restore; allowlist seed `stage_id+status`, angostada niega `transition_lead` con `lead_field_not_allowed` (saga finaliza, lead intacto), restaurada transiciona a `Calificado` vía `execute_transition`, columna desconocida 400; kill-switch escala booking sin evento, restore reserva, `global_auto=FALSE` suprime con `policy=auto` registrada, matriz angostada retiene el reply (solo follow-up, cero outbound); autonomía WU15 intacta (FAQ zero-click `agent_policy`, manual one-click con outbound); ciclo monotónico (approve en executed 409, resume sin handoff 409, handoff→pausa→resume→`auto`, segundo resume replay con cero audits y base en `paused`); replays de consola con `replayed=True` y cero efectos extra en los cinco recursos; forjados/extranjeros/ghost 404 no-enumerantes; stub offline (`langchain_openai` fuera de `sys.modules`, modelos `stub-keyword-v1`); 401/404/405 en consola + state/resume.

## Desviaciones verificadas desde código (D1–Dn)

- **D1** — `tasks.md` Phase 10 nombra el seed `mvp_seed_009_console.sql`; el archivo real es `infra/migrations/validation/mvp_seed_009.sql` (el diseño dice `mvp_seed_009.sql` — inconsistencia interna del planning). El harness usa el nombre real (`api/test_agent_console_wu16.py:56-59`).
- **D2** — la idempotencia de consola deriva la key del contenido cuando el cliente no manda `idempotencyKey` (`api/routes/agent_wu14.py:254-264`): dos escenarios con el mismo payload colisionan (el segundo replays sin aplicar) y el comando de modo usa key fija `agent-mode:{conv}:{mode}` (`api/routes/agent_wu14.py:847-848`), así que un pin `auto` en setup roba la key del unpin de K9. El harness usa `idempotencyKey` explícita y distinta en cada escritura y setup por GET (`api/test_agent_console_wu16.py`, comentarios inline).
- **D3** — el edit aceptado #2 (`AND a.deactivated_at IS NULL` en `KB_SCORING_SQL`, `api/adapters/postgres_knowledge_base.py:59`) exige la columna 009: en la base `wu15` (nivel 008) todo `analyze`/`find_best_match` moría con `UndefinedColumn`, rompiendo `test_agent_autonomy_wu15` y `test_agent_wu14` (verificado: traceback 500 en `analyze` vía `_kb_lookup`). El fallback a constantes cubre filas ausentes, no columnas/tablas ausentes. Fix sin tocar archivos: `runner --up` aplicó `009` sobre `wu15` + seed `mvp_seed_009.sql` (defaults = constantes, cero cambio de comportamiento); 30/30 y 24/24 intactos. Además `test_agent_autonomy_wu15.py` `SEED_FILES` ahora incluye `mvp_seed_009.sql` (hermético) y su docstring dice 001–009. Por la misma razón `test_kb_retrieval` corre sobre `wu16` (21/21) en vez de `wu15`.
- **D4** — `test_simulator` S10/S11 y `test_e2e_mvp` E11 fallan porque las rutas seed/reset de composición ahora corren `MANAGED_SEED_FILES = [SEED_FILE, SEED_008_FILE, SEED_009_FILE]` (`api/composition_wu8_mvp.py:80,348-352`): en bases nivel 004 (`wu9`/`wu10`, verificado por `migration_ledger`) los seeds 008/009 referencian tablas inexistentes y la ruta devuelve 500 (S11: `500` directo; S10/E11: `r.status_code != 200`). Preexistente del trabajo Phase 0–9 (este batch solo agrega el harness, importado por nadie). FIX EN ESTA UNIDAD (gatekeeper): bases `wu9`/`wu10` avanzadas a 009 + seeds (`mvp` + `008` + `009`); causa raíz: los DELETE de `pipeline_stages` en `mvp_reset.sql`/`mvp_seed.sql` no conocían los markers 008 (`demo-a/b-stage-calificado`) y el DELETE de `pipelines` moría por RESTRICT — ambas listas extendidas (no-op seguro en bases viejas). S10/S11/E11 verdes; docstrings de ambos harnesses actualizados a 001–009.
- **D5** — `contracts/runtime-v1.test.ts:540` tiene un error de sintaxis (`...toThrow()));` — paréntesis extra) que impide parsear la suite (esbuild `Expected ";" but found ")"`); los 85 tests de las otras 9 suites pasan, incluyendo la suite WU16 de la vista Agente. Preexistente del trabajo Phase 8. FIX EN ESTA UNIDAD (gatekeeper): paréntesis extra eliminado (una línea); suite parsea y pasa.
- **D6** — `tasks.md` Phase 11 lista la unit `test_agent_console_allowlist`, que no existe (solo `settings/prompts/kb/calendar`, verificado por glob); el allowlist se cubre en el harness (K5/K10) y en `test_agent_console_settings` (S6). No se creó archivo nuevo por la regla de no crear fuera de lo pedido. FIX EN ESTA UNIDAD (gatekeeper): la línea Verify de Phase 11 en `tasks.md` ahora lista solo las units reales (`settings/prompts/kb/calendar`).
## Limitaciones

- Harness autocontenido en `wu16`: el preclean elimina la fixture Palermo y resetea stages/modos cada run, y la reconvergencia final deja la base en forma seed pura; no toca bases anteriores. La base `wu15` se avanzó a 009 (D3) — sigue descartable y autocontenida.
- `POST /v1/demo/agent/state` nunca fija `paused`; `resume` es one-shot por conversación con replay dev-only (D2 de WU15 + K10 de este harness).
- Sin memoria conversacional más allá del fallback last-interest para precios.
- Reporte de consola del harness evita flechas (codepage Windows); los archivos son UTF-8 sin BOM con acentos reales, mojibake 0.
- Deuda conocida: K6-faq/K3 usan bodies/palabras elegidos para no colisionar ramas del stub ni keywords seed (documentado inline y en Decisiones).
- Fallos fuera de alcance registrados arriba: E11/S10/S11 (D4) y suite de contratos (D5); demo manual skipeado.

## WACRM

Referencia auditada únicamente; sin fork, copia ni dependencia.
