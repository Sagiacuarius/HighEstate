# Work Unit 15 Evidence: Agent Autonomy (change: agent-autonomy)

Status: **implemented, pending review** — verificado directamente (compileall, harness nuevo 30/30 ×2, WU14 24/24 re-apuntado, units 10/10 + 15/15 + 17/17 + 21/21, regresión WU13/WU12/WU11/WU10/WU9/WU6, vitest 59/59, build web) contra PostgreSQL 18 real.

## Scope

Harness de autonomía Phase 10 (`api/test_agent_autonomy_wu15.py`) sobre base descartable `production_runtime_mvp_wu15` (runner 001–008 + `mvp_seed.sql` + `mvp_seed_005.sql` + `mvp_seed_008.sql`) con `StubModelProvider`: cubre B1–B10 del Testing Strategy del diseño contra las rutas reales (analyze auto/escalate, state, resume, approve) más probes 401/404/405/409 y regresión WU9. Phase 11: el re-apunte de `test_agent_wu14.py` a `wu15` (hecho en un batch anterior, con pin `manual` vía state endpoint) se verificó intacto — **no requirió ningún fix**, no se tocó ningún otro archivo.

## Archivos

- `api/test_agent_autonomy_wu15.py` — harness nuevo B1–B10 + envelope (30 escenarios, base propia `production_runtime_mvp_wu15`, preclean + fixture Palermo + pin `auto` en setup).
- `openspec/changes/agent-autonomy/work-unit-15-evidence.md` — este archivo (draft).
- `api/test_agent_wu14.py` — **sin cambios**: su re-apunte a `wu15` + pin `manual` + aserciones de `policy_decision`/`execution_mode` sigue en 24/24.

## Decisiones implementadas

- **Bodies distintos por escenario**: la fallback key de analyze es `sha256(account|conversation|body)` (lección WU14 A17) — ningún body se reutiliza entre escenarios que necesitan análisis fresco.
- **Fixture Palermo en setup**: ninguna propiedad `available` seedada tiene una ciudad extraíble por `_CITY_HINTS` (el filtro es `city ILIKE` estricto y el catálogo A es `Buenos Aires`); el harness clona PROP1 como `Depto en Palermo` (apartment/sale/available, 195000, `demo_seed_key NULL` → el preclean lo elimina cada run). Así B3 resuelve ciudad+presupuesto y las búsquedas solo-operación siguen top-matcheando PROP1.
- **Handoff explícito en A, complaint en B**: el resume dev-only usa key fija `agent-resume:{conversation_id}` (ver D5) — el ciclo complaint corre en la conversación canónica de B, que tiene key fresca.
- **B2a honesto**: el seed 005 deja un interés `ai_agent` sobre PROP3 (`capturing`); el escalate se prueba vía live-read miss, no vía fallback vacío (ver D6).
- **Slot-race auto no reproducible por HTTP**: la ruta auto es una sola saga sin gap analyze→dispatch; se documenta como cubierto por el dispatcher compartido + WU14 A10 (ver D8).
- **B7 como oráculo real**: reconstruye `PolicySignals` desde el draft persistido (intent + acciones + evidencia match/slot/stage) y compara `evaluate()` contra `policy_decision` en 13 drafts del corpus.
- **Invariante grounded**: el cuerpo outbound persiste solo `send_reply.reply_text` con fuente (`kb_article`/`price_catalog`) y se aserta distinto del `reply_text` libre del modelo (B1); B5 aserta cero outbound.

## Verificación (PostgreSQL 18 real)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api infra` | exit 0 |
| `python -m api.test_agent_autonomy_wu15` (base `production_runtime_mvp_wu15`, run 1) | **30/30** passed |
| `python -m api.test_agent_autonomy_wu15` (misma base, run 2) | **30/30** passed (determinismo: conteos idénticos) |
| `python -m api.test_agent_wu14` (base `production_runtime_mvp_wu15`) | **24/24** passed (re-apunte intacto, sin fix) |
| `python -m api.test_model_provider` | 10/10 (units) |
| `python -m api.test_conversation_agent` | 15/15 (units) |
| `python -m api.test_agent_policy` | 17/17 (units) |
| `python -m api.test_kb_retrieval` | 21/21 (units, corpus `mvp_seed_008`) |
| `python -m api.test_calendar_wu13` (base `production_runtime_mvp_wu13`) | 22/22 (regresión) |
| `python -m api.test_audit_screen_wu12` (base `production_runtime_mvp_wu12`) | 13/13 (regresión) |
| `python -m api.test_property_catalog_wu11` (base `production_runtime_mvp_wu11`) | 16/16 (regresión) |
| `python -m api.test_e2e_mvp` (base `production_runtime_mvp_wu10`) | 12/12 (regresión) |
| `python -m api.test_simulator` (base `production_runtime_mvp_wu9`) | 12/12 (regresión) |
| `python -m api.test_crm_reads` (base `production_runtime_mvp_wu6`) | 13/13 (regresión) |
| `npm.cmd exec vitest run` (web) | **59 tests** passed, exit 0 (incluye suite WU15 agent autonomy) |
| `npm.cmd run build` (web) | exit 0 |
| Browser/manual demo pass | **skipped** (no requerido para este batch) |

B1–B10: FAQ zero-click ejecutado por la política (sentinel, `decided_by NULL`, outbound visible en el thread, 1 audit + 1 outbox por acción, cross-account con artículo propio) y precio por fallback + edición de catálogo en vivo; search con `transition_lead` vía `execute_transition` (lead en `Calificado`, replay sin duplicados); handoff explícito + complaint (pausa, 409 en toggle, inbound en pausa silencioso, resume a `auto`); no-match propuesto sin mutar KB (32 filas) e invisible cross-account; booking auto zero-click + manual como WU14; oráculo de política 13/13; forjados 404; stub offline (`langchain_openai` fuera de `sys.modules`); ciclo de vida monotónico 409; 401/404/405/409 en state/resume; simulador WU9 intacto.

## Desviaciones verificadas desde código (D1–Dn)

- **D1** — SQL del diseño `cardinality((SELECT … INTERSECT …))` no es PostgreSQL válido; el adapter usa `cardinality(ARRAY((SELECT … INTERSECT …)))` como equivalente exacto (`api/adapters/postgres_knowledge_base.py:52-58`, con comentario que lo documenta).
- **D2** — `generate()` devuelve interpretación validada: `OpenCodeGoProvider.generate` retorna `validate_interpretation(raw)` (`api/application/model_provider.py:199`); el stub pasa por `validate_interpretation` en el agente (`api/application/conversation_agent.py:136`).
- **D3** — la composición NO cablea 008: `api/composition_wu8_mvp.py:341,345` corre seed/reset solo con `mvp_seed.sql` (`SEED_FILE`), sin `mvp_seed_008.sql` — un reset desde el dashboard pierde los artículos KB y el flip `auto` (vuelven al default `manual`).
- **D4** — nombres de stage agregados por 008: filas `Calificado` por pipeline demo (markers `demo-a/b-stage-calificado`); `transition_lead` + `send_reply` viven en el vocabulario pero solo el dispatcher WU15 les da efecto (`api/domain/agent_draft.py:21-25`).
- **D5** — resume dev-only one-shot por conversación: `POST /v1/demo/agent/handoff/resume` usa key fija `agent-resume:{conversation_id}` (`api/routes/agent_wu14.py:694`); un segundo resume replays el outcome guardado (200 con payload viejo) sin re-aplicar. El harness corre el ciclo complaint en la conversación de B (key fresca) y lo deja como limitación dev-only.
- **D6** — B2a no es "sin interés": `mvp_seed_005.sql:176-178` siembra `demo-a-interest-001` (`ai_agent`) sobre PROP3, que está `capturing`; el escalate se pinnea vía live-read miss (`price_read` exige `status='available'`) y la aserción lo documenta (`seed_target=capturing`).
- **D7** — fixture Palermo clonado en setup del harness (ver Decisiones): sin él, B3 no tiene target resoluble ciudad+presupuesto porque el filtro `city ILIKE` (`api/adapters/postgres_agent_drafts.py:105-108`) es estricto y ningún hint de `_CITY_HINTS` matchea `Buenos Aires`.
- **D8** — `create_event=denied` en ruta auto no es reproducible por HTTP (decide→dispatch→finalize en una sola llamada, sin ventana de carrera); cubierto por el `_dispatch_actions` compartido (`api/routes/agent_wu14.py:168`) + prueba manual WU14 A10. El kill-switch `AUTO_EVENT_CREATION=False` sigue pinneado por units (`test_agent_policy` P16/P17).

## Limitaciones

- Harness autocontenido en `wu15`: el preclean elimina la fixture Palermo y resetea precios/stages cada run; no toca bases anteriores.
- `POST /v1/demo/agent/state` nunca fija `paused`; `resume` es one-shot por conversación (D5) — superficie dev-only.
- Sin memoria conversacional más allá del fallback last-interest para precios.
- Reporte de consola del harness evita flechas `→` (codepage Windows); el archivo es UTF-8 sin BOM con acentos reales.
- Deuda conocida: B5/B4-inbound dependen de bodies que no colisionen fallback keys ni keywords del stub (documentado inline, bodies únicos por escenario).

## WACRM

Referencia auditada únicamente; sin fork, copia ni dependencia.
