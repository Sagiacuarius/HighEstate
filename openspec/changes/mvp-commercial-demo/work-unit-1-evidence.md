# MVP Commercial Demo — Work Unit 1 Evidence (contract correction 5)

## Status

Se corrigieron únicamente contratos puros y tests del WU1 del MVP Commercial Demo. **No se declara aceptación ni MVP completo.**

## Path

Todo el trabajo se ejecutó exclusivamente en:

```text
C:\Users\Windows\Documents\AI Agency Platform
```

## Archivos modificados

```text
src/application/commercial-demo.ts
src/application/commercial-demo-contracts.test.ts
openspec/changes/mvp-commercial-demo/work-unit-1-evidence.md
```

No se agregaron migraciones, tablas, repositories, endpoints, UI, seed/reset ni implementación comercial.

## `DemoContextResolverTestDouble`

El double explícito recibe:

```text
TenantContext
RequestedClientAccountIntent
CurrentIdentity
```

Su enforcement runtime:

- rechaza `TenantContext` ausente;
- valida el scope del contexto;
- ejecuta `CurrentIdentity.get()`;
- registra cada llamada válida con contexto e intención solicitada;
- trata `requestedClientAccountId` como hint;
- rechaza una cuenta cross-account;
- no registra llamadas rechazadas;
- conserva el contexto server-owned;
- no autoriza por sí mismo como resolver productivo.

El test verifica explícitamente llamadas válidas, contexto recibido, hint solicitado, contexto ausente y cuenta cross-account.

## `requireRelationshipScope`

Además de las entidades CRM, ahora valida mediante constructores baseline el `TenantContext` completo:

```text
AgencyId
ClientAccountId
MembershipId
UserId
CorrelationId
```

Se validan en runtime:

- tipo string;
- no vacío;
- sin espacios laterales;
- longitud máxima de 256 caracteres;
- formato permitido.

Un branded TypeScript type por sí solo no se considera suficiente.

Se mantienen todas las validaciones CRM:

```text
ContactId
LeadId
PropertyId
PipelineId
StageId
ConversationId
MessageId
FollowUpId
```

Y las relaciones:

```text
Lead.contactId === Contact.id
Lead.selectedPropertyId === Property.id
Lead.stageId === Stage.id
Stage.pipelineId === Pipeline.id
Pipeline.stage.clientAccountId === Pipeline.clientAccountId
FollowUp.leadId === Lead.id
FollowUp.contactId === Contact.id
Conversation.contactId === Contact.id
Message.conversationId === Conversation.id
```

También se valida que las entidades pertenezcan al mismo `ClientAccount` del contexto. Las relaciones cruzadas o inconsistentes se rechazan antes de cualquier handler o mutación.

## `InboundMessageHandlerTestDouble`

El handler de prueba revalida internamente, aunque el evento haya pasado por el parser:

```text
event.clientAccountId === event.message.clientAccountId
event.conversationId === event.message.conversationId
```

El test envía directamente eventos inconsistentes al handler, sin depender del parser, y verifica:

- rechazo antes de registrar;
- rechazo antes de mutar estado;
- `handled` permanece igual;
- sólo el evento normalizado válido queda registrado.

Esto es un `InboundMessageHandlerTestDouble`, no un handler productivo.

## RED → GREEN

El test RED inicial fue ejecutado antes de crear el módulo contractual:

```text
Error: Cannot find module './commercial-demo'
exit code: 1
```

Después de implementar los contratos y doubles, el test contractual pasó:

```text
npm.cmd test -- src/application/commercial-demo-contracts.test.ts
exit code: 0
Test Files  1 passed (1)
Tests       12 passed (12)
```

## Casts de test

La evidencia mantiene documentado el cast baseline:

```text
undefined as never
```

Se usa únicamente para enviar un contexto ausente a una función cuyo contrato tipado requiere `TenantContext`, y comprobar su rechazo runtime.

También permanecen casts equivalentes para fabricar entidades inválidas deliberadas en tests, por ejemplo:

```text
"other-contact" as never
"other-pipeline" as never
"forged-account" as never
```

No se usan casts para fabricar IDs válidos en la implementación. Los fixtures válidos usan constructores:

```text
AgencyId(...)
ClientAccountId(...)
MembershipId(...)
UserId(...)
CorrelationId(...)
ContactId(...)
LeadId(...)
PropertyId(...)
PipelineId(...)
StageId(...)
ConversationId(...)
MessageId(...)
FollowUpId(...)
EventId(...)
```

## Verificaciones finales

### TypeScript typecheck

```text
npm.cmd run typecheck
exit code: 0
```

### Suite TypeScript completa

```text
npm.cmd test
exit code: 0
Test Files  6 passed (6)
Tests       39 passed (39)
Start at 21:57:05
Duration 1.46s
```

Desglose:

```text
27 tests previos
12 tests contractuales del MVP Commercial Demo
```

### Python

```text
python -m compileall -q api
COMPILE_EXIT=0
```

No se agregó implementación Python ni comercial para este work unit.

## No implementado

No se agregó:

```text
migración 004
tablas CRM
seed/reset
repositories
persistencia
endpoints
UI
dashboard
botón de simulación
Kapso
WhatsApp
LLM
agentes
LangGraph
automatizaciones
reuniones
calendario
formularios
auth productiva
Docker
CI/CD
```

## Limitaciones

- Los siete doubles son exclusivamente de prueba.
- `DemoContextResolverTestDouble` no es un resolver productivo.
- `InboundMessageHandlerTestDouble` no procesa mensajes productivamente.
- No existe persistencia CRM.
- No existe aislamiento persistente.
- No existen repositories ni endpoints comerciales.
- No existe UI.
- No existe proveedor de identidad productivo.
- Las validaciones de relaciones son puras y en memoria; la integridad persistente queda para work units posteriores.
- El cast `undefined as never` y los casts de entidades inválidas son fixtures deliberados, documentados y usados para probar rechazo runtime.
- No se declara MVP completo.

## Acceptance status

Los contratos y tests puros fueron corregidos y verificados con:

```text
12/12 tests contractuales
39/39 tests TypeScript totales
compileall exit 0
```

**El WU1 del MVP Commercial Demo queda aceptado para contratos y doubles puros.** Persistencia CRM, repositories, API, UI, seed/reset y el flujo end-to-end quedan fuera de este work unit y continúan pendientes.
