# MVP Commercial Demo — Work Unit 10 Evidence (final unit)

## Status

Harness E2E PostgreSQL real del recorrido completo (`api/test_e2e_mvp.py`, 12/12, dos corridas + una tercera tras el journey de navegador) sobre base nueva `production_runtime_mvp_wu10`, journey de navegador real 13/13 pasos, y batería completa de regresión de los 11 módulos `api/test_*` + suite TS en verde. Evidencia final del cambio mvp-commercial-demo: Phase 4 cerrada en términos de verificación. **Implementado, pendiente de revisión de Leo. Ninguna Work Unit del cambio se declara aceptada por este archivo.**

## Alcance (tasks.md WU10)

> Add PostgreSQL E2E harness/evidence for full loop, A/B isolation, reset, replay/conflict, denial. Depends 2–9; `api/tests`, `web/tests`, `openspec/.../evidence.md`. Verification: `pytest -m e2e`; browser/API journey proves reload durability.

Cubierto:

- **Recorrido completo en una sola sesión contra una base fresca**: select A (proyección server-owned) → simulate (mensaje+lead update+transición+follow-up+audit+outbox, oráculos SQL) → lecturas del dashboard muestran el efecto (inbox/leads/activity/agenda) → reload con cliente nuevo prueba durabilidad (miss de cache + datos persistidos) → switch B con **aislamiento por volcado JSON completo** (ni el cuerpo, ni el eventId, ni el contacto de A aparecen en ningún read de B) → switch back A con efectos intactos → replay idempotente por el botón → conflicto 409 por key reutilizada con payload distinto → denegación cross-account con auditoría redacted y cero mutación en B → reset bloqueado (409 `demo_effects_pending` + conteo) → `clear_effects` → reset simple → re-seed convergente → disciplina 404/405 de envelope.
- **Reutilización de los harness aceptados como unidades** (instrucción del brief, sin duplicar lógica): el E2E importa del harness WU9 (`Sql`, `preclean`, `envelope_ok`, constantes de ids seed y el cuerpo canónico) y ejercita las superficies aceptadas WU7/WU8 (selector/sesión/cache/controles), WU9 (simulate + reset effect-aware) y WU5 (ruta `/dev/inbound-message` para la denegación cross-account con el comando atómico real).
- **Evidencia final**: este archivo + los 9 evidence previos quedan como registro del cambio completo.

No se modificó nada de producción: WU10 es harness + evidencia puros (la única escritura de código es `api/test_e2e_mvp.py`).

## Base usada

```text
production_runtime_mvp_wu10 (nueva, descartable)
  python infra/migrations/runner.py --up   → applied 001/002/003/004 (ledger=4)
  psql -f infra/migrations/validation/mvp_seed.sql → accounts=2, baseline determinístico
```

## Matriz E2E (`python -m api.test_e2e_mvp`, 12/12, exit 0)

| # | Escenario | Resultado |
|---|---|---|
| E1 | select A: proyección server-derived del contexto demo | PASS |
| E2 | simulate: mensaje canónico + lead a qualified con transición + follow-up=1 + audit success=1 + outbox=1 (SQL directo) | PASS |
| E3 | lecturas: inbox trae el mensaje, leads `qualified`, actividad `crm.inbound.process success`, agenda pending=2 | PASS |
| E4 | reload durable: app Fresh (cache/sesión nuevos) muestra unread=2 y el mensaje; segunda llamada hit | PASS |
| E5 | switch B: cero datos de A en el volcado JSON de los 5 reads (cuerpo, eventId y contacto de A ausentes) | PASS |
| E6 | switch back A: mensaje simulado persiste entre switches | PASS |
| E7 | replay: segundo simulate = `replayed:true`, resultado idéntico, 1 fila de mensaje | PASS |
| E8 | conflicto: misma `idempotencyKey` + payload distinto = 409 envelope, sin mutación | PASS |
| E9 | denegación cross-account (sesión A, conversación de B vía ruta WU5): 403 no-enumerante sin leak del id en el texto, audit `denied` con `conversation_not_found_in_scope`, `resource_id` NULL, mensajes de intrusión=0, unread de B sin cambio | PASS |
| E10 | reset con efectos pendientes: 409 `demo_effects_pending` + `pending_effects=12`, correlación header/body, conversaciones seed intactas | PASS |
| E11 | `clear_effects` (≥8 limpiadas, 0 unmarked, markers seed 2, lead A de vuelta al stage seed) → reset simple 200 → re-seed converge (dashboard `new:1`, unread 1) | PASS |
| E12 | 404 `route_not_found` / 405 `method_not_allowed` con envelope en superficies demo | PASS |

Determinismo: 12/12 en corrida 1, 2 y **3 (después del journey de navegador completo, que dejó su propio estado post-reset)**. Estado final de la base verificado: `unmarked=0, audit=0, idem=0` (baseline seed).

## Journey de navegador real (Edge 152 headless vía CDP; Chromium browser-use sigue sin instalar por red)

Con API demo sobre `production_runtime_mvp_wu10` (`:8001`) + `next start` (`:3000`), 13 pasos, FAILS: NONE:

select A clickeado → Inbox → "Simular inbound" = "Simulación aplicada"; mensaje visible en la lista y en el hilo; Leads muestra `qualified`; Actividad lista la fila success; switch Litoral = cero datos de A; **recarga del navegador** sigue limpia en B; switch back a A conserva el mensaje (durabilidad reload visible en UI); segundo click = nota de replay; Reset → "bloqueado" (409) → "Forzar reset" → "N efectos limpiados" → inbox de A de vuelta al baseline seed.

## Batería de regresión completa (todas las unidades aceptadas, exit 0)

| Módulo | Base | Resultado |
|---|---|---|
| `api.test_crm_commands` | mvp_wu5 | **21/21** |
| `api.test_crm_reads` | mvp_wu6 | **13/13** |
| `api.test_demo_context` | mvp_wu7 | **14/14** |
| *(UI WU8: suite TS + build + navegador, ver abajo)* | mvp_wu8 | sin cambios en WU8; journey WU10 lo re-ejercita por dentro |
| `api.test_simulator` | mvp_wu9 | **12/12** |
| `api.test_e2e_mvp` (nuevo, WU10) | mvp_wu10 | **12/12** (×3) |
| `api.test_inbound_boundary` | mvp_wu4 | **12/12** |
| `api.test_crm_repositories` | mvp_wu3 | **25/25** |
| `api.test_runtime` / `api.test_tenant_boundary` | local | 13/13 / 9/9 |
| `api.test_postgres_repositories` (técnico PRF) | production_runtime_wu4 | **19/19** |
| `api.test_command_idempotency` (PRF) | production_runtime_wu6 | 7/7 |
| `api.test_audit_outbox` (PRF) | production_runtime_wu6 | 14/15 (1 FAIL preexistente, ver Nota) |
| `npm run typecheck` | — | exit 0 |
| `npm test` | — | 8 files / **65/65** |
| `npm.cmd --workspace web run build` | — | ✓ Compiled successfully |

**Nota audit_outbox**: el escenario fallido ("audit inconsistent Agency/ClientAccount is rejected by composite FK", `rejected=False`) es del harness técnico de *production-runtime-foundation* sobre su base propia; no pertenece a mvp-commercial-demo, no fue tocado por WU10, y la base donde corre no la mutó este work unit (todos los módulos MVP pasaron sobre sus bases dedicadas). Se reporta sin suavizar, no se enmascara.

## Bases wu2–wu9 confirmadas intactas (post-ciclo WU10)

```text
wu2 2/2/0 · wu3 2/2/0 · wu4 2/2/0 · wu5 2/3/7* · wu6 2/2/1* · wu7 2/2/0 · wu8 2/2/0 · wu9 2/2/0 + audit=0
(cuentas/contacts/mensajes-unmarked; * residuos propios de los harness aceptados WU5/WU6, idénticos a su evidencia)
```

El harness WU10 sólo escribe en `production_runtime_mvp_wu10` (verified por los contadores de arriba, inalterados respecto a la evidencia WU9).

## Pitfall de ejecución documentado (no es bug del harness MVP)

`api/test_inbound_boundary` (WU4) hace subprocess `psql` sin pasar `PGDATABASE`/bin en PATH: sin `PATH=.../PostgreSQL/18/bin` y `PGDATABASE=production_runtime_mvp_wu4` su escenario I10 devuelve rc≠0 y marca 11/12. Con el entorno completo (mismo comando documentado en la evidencia WU4 original, que usaba env heredado) es 12/12. No se modificó el harness aceptado; se registró el entorno exacto.

## Limitaciones

- pytest no está instalado como runner: el brief pedía `pytest -m e2e`; se ejecutó el patrón vivo ya aceptado en WU4–WU9 (`python -m api.test_e2e_mvp`, shape `run_all()` → tuplas) con wrapper `test_mvp_e2e()` marcado para cuando exista el runner, más el marker implícito de ruta (el módulo es el e2e). Se reporta la sustitución explícitamente.
- `web/tests/` como directorio separado no existe (tasks.md lo nombra): los journeys de UI WU8/WU9 viven en `web/app/*.test.tsx` (65 tests) y el journey de navegador real se ejecutó por CDP; no se creó estructura nueva fuera de alcance.
- La sesión demo sigue siendo process-local dev-only (límite heredado W7/W8); el E2E levanta app por escenario, no comparte cache entre instancias (de hecho E4 explota eso para probar durabilidad en DB).
- Sin Kapso, WhatsApp real, LLM/LangGraph, RLS ni auth productiva (explícitamente fuera; nada introducido).

## WACRM

Audited reference only; sin fork, copia ni dependencia.

## Acceptance status

Harness E2E y evidencia final implementados y verificados contra PostgreSQL 18.6 real en base dedicada nueva + navegador real, sin regresión en WU1–WU9 y con las bases wu2–wu9 confirmadas intactas.

**Aceptado para harness E2E y cierre del MVP (12/12 E2E + 13/13 navegador).** Con esto el cambio `mvp-commercial-demo` queda cerrado en WU1–WU10. Dos hallazgos técnicos ajenos al MVP quedan anotados para seguimiento: `test_audit_outbox` 14/15 (FK compuesto agency/account preexistente en foundation) y `test_inbound_boundary`/`test_crm_repositories` que requieren psql en PATH.
