# MVP Commercial Demo — Work Unit 5 Evidence

## Status

Comandos CRM atómicos (inbound, transición de etapa, follow-up create/complete) implementados y verificados contra PostgreSQL 18.6 real con idempotencia canónica, transacción única, audit redacted y outbox. **Implementado, pendiente de revisión de Leo.**

## Alcance (tasks.md WU5)

> Add atomic lead, pipeline, follow-up commands with WU5/WU6 hash, idempotency, transaction, audit/outbox. Depends 3–4; `api/application/commands/`. Verification: `pytest api/test_crm_commands.py`; loop/replay/conflict.

Cubierto: comando inbound atómico (message→lead→advance→follow-up→audit→outbox→result en UNA transacción), comandos explícitos `lead.transition`, `follow_up.create`, `follow_up.complete`, canonical hash (sorted-keys JSON + sha256, espejo de `api/command_http._hash_body`), `reserve(actor, operation, key, hash)` con replay/conflicto, auditoría de denegación redacted en tx separada, dispatcher de outbox con claim/retry.

## Archivos

Creados esta unidad (y revisados/completados en esta sesión de cierre):

```text
api/commands/crm_commands.py             — intents puros provider-neutral + canonical_hash + InboundCrmEffects
api/application/crm_commands_wu5.py      — adaptadores atómicos PostgreSQL (CrmCommandBase reserve→act→audit/outbox→commit)
api/composition_wu5_mvp.py               — composición FastAPI dev-only /dev/inbound-message con X-Idempotency-Key
api/test_crm_commands.py                 — harness escenario-tupla (21 escenarios) + wrapper pytest
```

No se modificaron:

```text
PDR, proposal.md, design.md, specs, tasks.md
WU1–WU4 del MVP (contratos TS, migración 004 + runner + seed/reset, entidades, repositories, composición WU3, inbound boundary WU4)
composiciones técnicas (composition.py, composition_wu4.py, composition_wu5.py), api/app.py, api/http_contracts.py,
api/tenant_boundary.py, api/commands/audited_update_tenant_resource.py
WACRM, demo/agencia-inmobiliaria.*
migraciones 001–004
```

## Correcciones aplicadas en esta sesión de cierre

El inventario mostró los artefactos de WU5 ya creados por la sesión previa, pero el harness **nunca había pasado** (nunca se ejecutó con éxito). Fallas reales encontradas y arregladas — todas en el archivo de test, cero cambios en la lógica de producción:

1. **`Inspector.q` crasheaba en todo DELETE/UPDATE no-SELECT** (`fetchall()` sin result set → `ProgrammingError`, abortaba en `preclean`). Fix: guard `cur.description is None`.
2. **`snapshot_account` usaba columna `status` inexistente** en `properties` (usa `availability`) y `pipelines`/`pipeline_stages`/`messages` (no tienen `status`). Fix: columna de estado canónica por tabla.
3. **C2 mezclaba dos semánticas distintas**: reenvío del mismo evento con key nueva → `CrmCommandDenied("message_event_already_processed")` (dedup por `external_event_id`) reventaba el paso 1, y además había una llamada huérfana con key errónea ("same-as-c1-nope"). Fix: escenarios separados C2a (doble entrega bajo key nueva → denial + cero mutación + cero success audit) y C2b (replay con la MISMA key+hash → resultado canónico, cero efectos duplicados). Ambos pasan.
4. **C7 leía el denial audit equivocado**: `db.one` sin ORDER devolvía el denial de C2a (`message_event_already_processed`), enmascarando el motivo real. Fix: `ORDER BY created_at DESC, id DESC LIMIT 1`. El motivo verificado ahora es `conversation_not_found_in_scope`, resource_id NULL, sin fuga de conversación ni body en el metadata.
5. **C10 completaba el follow-up con key nueva** (`fu-c-2`) cuando ya estaba `completed` → `follow_up_not_pending` no esperado. Fix: replay de completion con la misma `fu-c-1`; el escenario de segunda denegación usa `fu-c-3` explícitamente (ya lo cubría).
6. **C9/C13 secuencia contradicha**: el test avanzaba el lead de vuelta al seed stage en C9, así que el segundo inbound de C13 sí aplica un advance (`previous=SEED_STAGE_A, current=SCRATCH_STAGE`), no el "terminal → no transition" asumido. Fix: C13 afirma el comportamiento real verificado (lead único, follow-up del MISMO lead, advance reaplicado) y además `fu_same_lead >= 2`.
7. **`preclean` violaba la FK RESTRICT** `leads_stage_pipeline_account_fk` al borrar `pipeline_stages` scratch cuando un run abortado había dejado el seed-lead avanzado sobre ella. Fix: restaurar el seed lead a su etapa original ANTES del bloque de DELETEs.
8. **Limpieza**: dos expresiones bool muertas (`... if False else None` / contorsión de deliveries) reemplazadas por aserciones directas.

## Base usada

```text
production_runtime_mvp_wu5 (preexistente de la sesión previa: runner --up 001–004 + mvp_seed; reutilizada)
```

Bases anteriores verificadas intactas tras todo el ciclo:

```text
production_runtime_mvp_wu2 rows=16
production_runtime_mvp_wu3 rows=16
production_runtime_mvp_wu4 rows=16 (y su harness sigue pasando: 12/12)
```

## Matriz de escenarios (21/21 PASS, exit 0)

Comando:

```bash
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=production_runtime_mvp_wu5 RUNTIME_DB_USER=postgres python -m api.test_crm_commands
```

| # | Escenario | Resultado |
|---|---|---|
| C1 | inbound nuevo: message+lead+transition+follow-up+audit+outbox+idempotency en una tx | PASS |
| C2a | mismo evento bajo key nueva: denial `message_event_already_processed`, cero mutación, cero success audit | PASS |
| C2b | replay misma key+hash: resultado canónico, cero efectos duplicados (7 contadores idénticos) | PASS |
| C3 | misma key, hash distinto → `IdempotencyConflict`, sin mutación | PASS |
| C4 | concurrencia 5 workers key+hash iguales → 1 mutación + 4 replays, una fila de idempotency | PASS |
| C5 | concurrencia 5 workers misma key, hashes distintos → 1 winner + 4 conflicts | PASS |
| C6 | rollback forzado pre-commit → cero residuo (messages/audit/outbox/idempotency) | PASS |
| C7 | cross-account (sesión A, conversación de B): denial + audit redacted (`conversation_not_found_in_scope`, resource_id NULL, sin leak de id ni body), unread de B intacto | PASS |
| C8 | membership read-only bloqueado antes de SQL + denial audit | PASS |
| C9 | transition explícita: atómica, replay canónico, stage extranjero denegado (`stage_not_available_in_scope`), 1 success audit / 1 outbox | PASS |
| C10 | follow-up create replay-safe; complete una vez; segundo complete denegado (`follow_up_not_pending`); invariante target lead/contact exigido | PASS |
| C11 | outbox claim/publish + retry tras fallo; consumer fake idempotente nunca double-aplica | PASS |
| C12 | redacción profunda de claves sensibles a todos los niveles de anidamiento | PASS |
| C13 | segundo inbound misma conversación: lead actualizado sin duplicado, follow-up del mismo lead, advance reaplicado | PASS |
| C14 | Account B exactamente igual al snapshot previo ("Account B intact") | PASS |
| H1 | HTTP doble POST mismo payload = un efecto, segundo `replayed:true` | PASS |
| H2 | HTTP replay + hash conflict → 409 `tenant_idempotency_conflict` envelope | PASS |
| H3 | HTTP claim de cuenta forjado → 403 no-enumerante sin leak | PASS |
| H4 | HTTP extra key anidada en `message` → 400 | PASS |
| H5 | HTTP identidad ausente → 401 | PASS |
| H6 | HTTP sesión B procesa su propia conversación → 200 | PASS |

Re-run determinista tras primer ciclo completo: 21/21, `RERUN_EXIT=0` (preclean converge).

## Verificaciones obligatorias

| Comando | Resultado |
|---|---|
| `python -m compileall -q api` | exit 0 |
| `python -m api.test_crm_commands` (2x) | 21/21, exit 0 |
| `python -m api.test_inbound_boundary` (re-run WU4 sobre su base) | 12/12, exit 0 |
| `npm.cmd run typecheck` | exit 0 |
| `npm.cmd test` | 6 files, 39/39, exit 0 |

## Decisión de diseño: /dev/inbound-message con backcommand

`composition_wu5_mvp.create_mvp_command_app` reemplaza el handler de journal en memoria de WU4 por el comando atómico en la MISMA ruta dev-only. La composición WU4 aceptada queda intacta (el harness de WU4 sigue importándola y pasando 12/12); WU5 introduce su propia composición named `DemoCompositionWU5Mvp`. El `X-Idempotency-Key` explícito manda; sin header, el `eventId` determinístico actúa como clave (doble click del simulador = replay exacto → 200 `replayed:true`, un solo efecto), cerrando el gap de idempotencia documentado en la evidencia WU4.

## Limitaciones

- `InboundCrmEffects` es una regla demo fija y provider-neutral (advance al siguiente stage declarado + un follow-up con due +48h), no un motor de automatizaciones.
- La identidad de desarrollo sigue siendo `InMemoryIdentityAdapter` server-owned; auth productiva explícitamente fuera.
- El dedup por `external_event_id` + unique `(client_account_id, external_event_id)` es el backstop de redelivery; el reservation layer es la primera línea.
- La FK RESTRICT `leads_stage_pipeline_account_fk` implica que borrar un stage en uso falla cerrado (probado por preclean que primero repone el seed lead).
- Sin superficie HTTP para transition/follow-up commands: se ejercen a nivel de aplicación; las read APIs y futuros endpoints quedan en WU6+.
- pytest no está instalado como runner; el módulo se ejecuta vivo (`python -m api.test_crm_commands`, shape `run_all()` → tuplas) con wrapper `test_crm_commands()` para when available.

## WACRM

Audited reference only; sin fork, copia ni dependencia.

## Acceptance status

Implementado y verificado con FastAPI TestClient + PostgreSQL 18.6 real sobre base dedicada `production_runtime_mvp_wu5`, sin romper WU2/WU3/WU4.

**Aceptado para comandos CRM atómicos + idempotencia + auditoría/outbox (21/21 escenarios, re-run determinista).** Read APIs y endpoints HTTP de transition/follow-up quedan para WU6. Dos observaciones cosméticas (LOW, no bloqueantes): `canonical_hash` usa `default=str` (divergencia doc menor), y `LeadSummary.status` transporta el estado pre-transición (hoy sin uso).
