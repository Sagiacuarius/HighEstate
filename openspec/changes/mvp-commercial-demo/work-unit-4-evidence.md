# MVP Commercial Demo — Work Unit 4 Evidence

## Status

Composición FastAPI local del MVP y boundary inbound normalizado implementados y verificados contra PostgreSQL 18.6 real. **Aceptado para composición y endpoint inbound dev-only (12/12 escenarios).**

## Proyecto

```text
C:\Users\Windows\Documents\AI Agency Platform
```

## Archivos creados

```text
api/inbound_boundary.py            — parser/handler normalizado, dev-only
api/composition_wu4_mvp.py         — composition root FastAPI + endpoint /dev/inbound-message
api/test_inbound_boundary.py       — harness real FastAPI TestClient + PostgreSQL
contracts/runtime-v1.ts            — (agregado) DevInboundMessageBody / DevInboundAcceptedResponse
openspec/changes/mvp-commercial-demo/work-unit-4-evidence.md
```

No se modificaron:

```text
PDR, proposal.md, design.md, specs
WU1/WU2/WU3 del MVP (contratos TS, migración 004, seed/reset, runner, entidades, repositories, composición WU3)
migraciones 001–004
composiciones técnicas WU4/WU5/WU6 (composition.py, composition_wu4.py, composition_wu5.py) — intactas, sigue importando todo (IMPORTS_OK verificado en WU3; npm test 39/39 y compileall exitosos cubren app/tenant_boundary/tenant_resources)
api/app.py, api/http_contracts.py, api/tenant_boundary.py
WACRM, demo/agencia-inmobiliaria.*
```

## 1. Composición FastAPI local

`api/composition_wu4_mvp.py`:

```text
DemoCompositionWU4Mvp       — resolver + CrmRepositoryComposition + handler + cuenta objetivo
create_mvp_inbound_app()    — app FastAPI aislada con middleware de correlación
build_mvp_wu4_application() — composition root: env fail-fast + psycopg pool + repos WU3 + identidad server-owned
```

- Reutiliza el `TenantContextResolver` server-owned de `api/tenant_boundary.py` (identity + membership repositories) exactamente como en la composición técnica: el contexto se resuelve desde la identidad del servidor y la cuenta objetivo de sesión (`app.state.target_client_account_id`), nunca desde el body.
- Inyección por `Depends`/closure de composición; el route handler no recibe ninguna autoridad del cliente.
- WU3 del MVP, WU4/WU5/WU6 técnicos y `api/app.py` quedan intactos (se importan, no se tocan).

## 2. Inbound boundary normalizado

`api/inbound_boundary.py` — espejo Python del contrato WU1 (`parseInboundMessageEvent` + `InboundMessageHandler` en `src/application/commercial-demo.ts`):

```text
NormalizedInboundEvent (frozen; to_dict normalizado)
parse_inbound_message_event(payload)  → validación total + timestamp ISO-8601 UTC canónico
InboundPayloadInvalid (400) / InboundScopeDenied (403 no-enumerante)
InboundMessageHandler (Protocol — puerto)
DevJournalInboundHandler (handler dev-only con journal en memoria)
```

- Sin Kapso, sin WhatsApp, sin LLM: mensaje simulado = evento interno normalizado.
- El handler revalida independientemente: `event.client_account_id == context.client_account.id` y la conversación debe existir **dentro del account del contexto** vía `PostgresConversationRepository.get` (que ya exige TenantContext y predicado `client_account_id`).
- Rechazo = cero mutación: el journal sólo registra después de todas las verificaciones.

## 3. Endpoint dev-only

```text
POST /dev/inbound-message
```

- Claramente marcado: respuesta `environment: "dev-only-inbound-boundary"`, docstring "DEV-ONLY… not a production surface", ruta con prefijo `/dev/`.
- `InboundMessageBody` (pydantic, `extra="forbid"`) — shapes mínimas también espejadas en `contracts/runtime-v1.ts` (v1, coherente con el contrato versionado existente).
- El `clientAccountId` del payload es claim: si no coincide con el contexto server-resuelto → 403 no-enumerante (misma shape que conversación ajena o usuario sin membership).
- ErrorEnvelope + correlación reutilizados (`api/http_contracts.envelope`, middleware `X-Correlation-Id`): toda respuesta (200/400/401/403/202) lleva `error.correlation_id`/`correlation_id` y header `X-Correlation-Id` consistentes.

Semántica verificada:

| Caso | Resultado |
|---|---|
| payload canónico válido | 202 + evento normalizado + journal=1 |
| malformed (6 variantes: no-dict, direction outbound, body vacío, message scope inconsistente, timestamp inválido, extra key) | 400 `invalid_payload` |
| scope faltante (`clientAccountId` ausente) | 400 |
| claims de cuenta ajena | 403 `tenant_access_denied`, sin fuga de la cuenta en el body |
| conversación de B con sesión A | 403, journal intacto |
| identidad ausente | 401 `authentication_required` |
| usuario sin membership | 403 misma shape no-enumerante |
| replay sin idempotency | 202 doble, correlación propia por request (idempotencia → WU5, documentado) |
| sesión B | claim A → 403; conversación propia → 202 |

## 4. Base usada

```text
production_runtime_mvp_wu4 (nueva)
```

```text
runner --up → applied 001/002/003/004, RUNNER_EXIT=0
mvp_seed.sql → SEED_EXIT=0
```

Bases anteriores verificadas intactas tras todo el ciclo:

```text
production_runtime_mvp_wu2 rows=16
production_runtime_mvp_wu3 rows=16  (su harness sigue pasando: 25/25)
production_runtime_mvp_wu4 rows=16 (post-test; el boundary no persiste filas)
```

Ninguna base fue destruida; `production_runtime_local` y las wu4/wu6 técnicas intactas.

## 5. Tests

```bash
python -m api.test_inbound_boundary
```

Resultado:

```text
MVP-CRM WU4 INBOUND SCENARIOS: 12/12 passed
WU4_TEST_EXIT=0
```

## 6. Verificaciones obligatorias

| Comando | Resultado |
|---|---|
| `npm.cmd run typecheck` | exit 0 |
| `npm.cmd test` | 6 files, 39/39, exit 0 |
| `python -m compileall -q api` | exit 0 |
| `python -m api.test_inbound_boundary` | 12/12, exit 0 |
| `python -m api.test_crm_repositories` (re-run WU3 sobre su base) | 25/25, exit 0 |

## No implementado (fuera de alcance WU4)

```text
comandos CRM atómicos con canonical hash/idempotencia/audit/outbox (WU5 MVP)
read APIs (WU6 MVP)
UI/dashboard/simulador con botón Inbox
Kapso/WhatsApp real, LLM/LangGraph, RLS, auth productiva, Docker/CI/CD
```

## Limitaciones

- `DevJournalInboundHandler` es dev-only: journal en memoria por proceso, sin persistencia; la conversión mensaje→lead y la escritura CRM llegarán con comandos atómicos en WU5.
- El endpoint de desarrollo **no aplica idempotencia**: dos POSTs del mismo `eventId` generan dos entradas de journal. Está probado y documentado como gap intencional hasta WU5 (que introduce `reserve(actor, operation, key, canonical_hash)`).
- `X-Idempotency-Key` no es consumido por esta ruta (será parte de la superficie de comandos WU5).
- La identidad de desarrollo es `InMemoryIdentityAdapter` (server-owned, ignora claims del request); la auth productiva está explícitamente fuera.
- El `target_client_account_id` es estado de sesión/composición; en WU7 el selector demo lo cambiará tras resolver membership.
- El parser Python replica las reglas del contrato TS WU1 (mismos límites/predicados) pero no hay code-gen compartido; la coherencia queda cubierta por los 39 tests TS y este harness.

## Acceptance status

Implementado y verificado con FastAPI TestClient + PostgreSQL 18.6 real sobre base descartable dedicada, sin romper nada anterior.

**Work Unit 4 del MVP Commercial Demo queda aceptado para composición y endpoint inbound dev-only.** Quedan como follow-ups no bloqueantes: manejo de `CrossTenantAccessDenied` en el handler, validación estricta de claves anidadas en `message`, y limpieza del `except CrmNotFoundError` muerto.
