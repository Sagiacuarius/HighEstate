# Work Unit 14 Evidence: Conversational Agent (change: llm-conversational-agent)

Status: **implemented, pending review** — verified directly (compileall, vitest 54/54, harness 24/24, units 8/8 + 6/6, regresión WU13/WU12/WU11/WU10/WU9/WU6) contra PostgreSQL 18.6 real.

## Scope

Agente conversacional demo: interpreta mensajes entrantes con proveedor determinista stub (sin red/secrets) e interfaz para proveedor OpenAI-compatible `opencode-go` cuando hay `OPENCODE_GO_API_KEY`, genera borradores (drafts) con acciones propuestas acotadas, y permite aprobar/rechazar con ejecución transaccional idempotente + auditoría + outbox. Se monta aditivo sobre WU8; no modifica WU5/WU6/WU9/WU10/WU11/WU12/WU13.

## Archivos

- `infra/migrations/007_agent_drafts.up.sql` / `.down.sql` — tabla `agent_drafts` + índices + CHECKs + FKs scope-pinned.
- `api/domain/agent_draft.py` — `AgentDraft`, estados, acciones propuestas `ProposedAction`.
- `api/application/agent_persona.py` — system prompt (Sofía) + `validate_interpretation` (schema estricto).
- `api/application/model_provider.py` — `StubModelProvider` (determinista), `OpenAIGoProvider` (env), `ProviderError`.
- `api/application/conversation_agent.py` — `ConversationAgent.analyze` (intent → search/slot/criteria), `_pick_slot`, `_derive_actions`.
- `api/adapters/postgres_agent_drafts.py` — `AgentDraftRepository`, `free_slots`, `search_properties_for_criteria`, `upcoming_event`, `conversation_contact`, `lead_for_contact`.
- `api/application/agent_commands_wu14.py` — `AnalyzeInboundMessageCommand`, `DecideAgentDraftCommand`, `FinalizeAgentDraftCommand`, `CreateFollowUpCommand`, `RegisterPropertyInterestCommand`, `CreateCalendarEventCommand`, `UpdateCalendarEventCommand`.
- `api/routes/agent_wu14.py` — router montado aditivo en WU8 (`/v1/demo/agent/*`).
- `api/composition_wu8_mvp.py` — un `include_router` (WU14).
- `contracts/runtime-v1.ts` + `.test.ts` — `AgentDraft`/`AgentAnalyzeResponse` + parsers estrictos (13 tests).
- `web/app/demo-api.ts` — `agentAnalyze`/`agentDrafts`/`agentDraftApprove`/`agentDraftReject`.
- `web/app/page.tsx` — vista "Agente" (tarjetas de draft con aprobar/rechazar, input de mensaje).
- `web/app/agent-drafts.test.tsx` — 5 tests de UI.
- `api/test_agent_wu14.py` — harness real A1–A17 + envelope 401/404/405.

## Decisiones implementadas

- **Stub determinista primero**: sin API key el sistema funciona offline con interpretación por keywords (orden reschedule → schedule_visit → search_properties → update_preferences → ask_question → general). Con `OPENCODE_GO_API_KEY` se usa el proveedor real; la clave nunca se persiste.
- **Draft = propuesta acotada**: intent + criterios + acciones propuestas (register_interest/create_event/reschedule_event/create_follow_up). El analyze persiste el borrador; aprobar ejecuta en transacción todos los comandos y registra `execution_result` por acción (success/denied) + auditoría + outbox.
- **Idempotencia**: analyze reserva `agent-analyze:sha256(account|conversation|body)[:24]` (mismo body → replay); decide reserva `agent-decide:{draft_id}` (segundo approve/reject → 200 replayed, sin efectos duplicados); cada acción de aprobación usa `agent-draft:{id}:{index}`.
- **Disponibilidad honesta**: el agente usa `free_slots` (mismo oracle de WU13: horario comercial Lun–Sáb 9–18 BA, franjas 60 min, eventos no cancelados). Si el día preferido está ocupado → solo follow_up, NUNCA inventa un slot. Si el slot se ocupa entre analyze y approve → `create_event` reporta denied (verdad, sin mentir).
- **Aislamiento tenant**: drafts, follow-ups, eventos y lecturas scope-pinned por `client_account_id`; cross-account aprobar/leer → 404 no enumerable.
- **Rechazo = terminal**: reject no ejecuta nada; approve después de reject → 409 `draft_not_proposed`.

## Verificación (PostgreSQL 18.6 real)

| Comando | Resultado |
|---|---|
| `python -m compileall -q api infra` | exit 0 |
| `npm.cmd --workspace web run build` | exit 0 (types ok) |
| `npm.cmd exec vitest run` (web) | **54 tests** passed (incluye 5 agent-drafts + 13 contracts) |
| `python -m api.test_agent_wu14` | **24/24** passed (base `production_runtime_mvp_wu14`) |
| `python -m api.test_model_provider` | 8/8 (units) |
| `python -m api.test_conversation_agent` | 6/6 (units) |
| `python -m api.test_calendar_wu13` | 22/22 (regresión) |
| `python -m api.test_audit_screen_wu12` | 13/13 (regresión) |
| `python -m api.test_property_catalog_wu11` | 16/16 (regresión) |
| `python -m api.test_e2e_mvp` | 12/12 (regresión) |
| `python -m api.test_simulator` | 12/12 (regresión) |
| `python -m api.test_crm_reads` | 13/13 (regresión) |

A1–A17: analyze persiste + forjado 404, malformed 400 sin drafts, match=oracle + B invisible, analyze replay 1 fila, approve ejecuta + interest/follow_up + auditoría/outbox, segundo approve 409 sin efectos duplicados, reject nada + approve 409, reads scope + ghost 404, slot ocupado post-analyze → denied verdadero, simulator WU9 intacto, reschedule resuelve + audit update, stale reschedule fails closed, preferred date/time (mar 10:00 → 13:00Z), evento `source=agent` + audit create, día lleno → solo follow_up (sin slot inventado), cross-account 404, 401/404/405 envelope.

## Limitaciones

- Stub de interpretación acotado (keywords) hasta conectar el proveedor real en producción.
- Sin memoria de conversación entre mensajes (cada analyze es independiente; el conversation queda fijado por cuenta).
- `source=agent` solo en eventos creados por el agente; agenda/dashboard no distinguen visualmente aún.
- Sin editor de texto de la propuesta UI (solo borrador); aprobar/rechazar la UI sí están.
- Deuda conocida del WU14 harness: A4/A13/A17 dependen de que el body de analyze no colisione la fallback key ni ocupe el día objetivo (documentado inline).

## WACRM

Referencia auditada únicamente; sin fork, copia ni dependencia.