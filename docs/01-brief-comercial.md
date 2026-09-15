# Brief comercial actualizado

## Producto

Plataforma SaaS multi-tenant para que agencias administren agentes de IA y automatizaciones comerciales para múltiples clientes.

El primer vertical será inmobiliario, con un core reutilizable para futuros sectores.

## Cliente principal

- Agencias de marketing.
- Agencias de automatización.
- Agencias tecnológicas.
- Agencias especializadas en inmobiliarias.

La agencia podrá administrar desde una única plataforma:

- Varias inmobiliarias.
- Usuarios y permisos.
- Números de WhatsApp.
- Conversaciones.
- Propiedades y leads.
- Agentes de IA.
- Automatizaciones.
- Métricas.

## Problema

Las agencias suelen combinar CRM, WhatsApp, chatbots, automatizadores y servicios de IA externos. Esto provoca datos dispersos, configuraciones complejas, costos acumulados, dificultad para escalar y seguimientos manuales.

## Solución

Una plataforma especializada que integre:

- WhatsApp.
- Inbox.
- CRM inmobiliario.
- Agentes de IA.
- Base de conocimiento.
- Clasificación de leads.
- Seguimientos.
- Automatizaciones.
- Integraciones externas.
- Auditoría y métricas.

Cada inmobiliaria tendrá sus propios datos, canales, usuarios, permisos y configuración del agente.

## Dos formas de crear automatizaciones

### Constructor visual

La plataforma ofrecerá un lienzo visual para crear flujos mediante bloques:

```text
Mensaje recibido
      ↓
Enviar respuesta
      ↓
Esperar respuesta
      ↓
Evaluar interés
      ↓
Agregar etiqueta
      ↓
Notificar asesor
```

El usuario podrá crear nodos, conectarlos, definir condiciones, configurar esperas, agregar acciones y activar o pausar flujos.

### Creación mediante lenguaje natural

El usuario podrá pedirle al agente operador:

> Cuando un lead pregunte por una visita, etiquetalo como interesado, avisale al asesor y creá un recordatorio para mañana.

El agente generará una automatización estructurada que podrá revisarse, modificarse, confirmarse y activarse en el lienzo visual.

```text
Lenguaje natural para crear rápidamente
+
Lienzo visual para revisar y controlar
```

## Agentes de IA

### Agente conversacional

Atiende a los interesados por WhatsApp y puede:

- Responder preguntas.
- Buscar propiedades.
- Extraer datos del lead.
- Crear o actualizar leads.
- Agregar etiquetas.
- Notificar asesores.
- Crear seguimientos.
- Consultar calendarios.
- Coordinar visitas.
- Derivar a un humano.

Se configura desde la interfaz mediante prompt, modelo, temperatura, tono, horarios, herramientas, permisos y reglas de derivación.

### Agente operador

Asiste a la agencia o inmobiliaria desde el panel y puede:

- Crear automatizaciones.
- Crear tareas.
- Consultar métricas.
- Buscar leads.
- Detectar conversaciones sin responder.
- Modificar o pausar flujos.
- Proponer seguimientos.
- Generar resúmenes comerciales.

## Motor de automatizaciones

El agente no ejecuta directamente acciones sensibles.

```text
Agente interpreta
→ genera borrador
→ usuario confirma
→ motor ejecuta
→ sistema audita
```

El motor administra triggers, condiciones, esperas, recordatorios, reintentos, notificaciones, actualización de leads, asignación de conversaciones y citas.

## Integración de WhatsApp

Para el MVP se utilizará Kapso. La integración se encapsulará mediante una abstracción interna:

```text
WhatsAppProvider
  ├─ KapsoProvider
  └─ MetaCloudProvider futuro
```

Esto permite lanzar rápidamente con Kapso y mantener abierta la posibilidad de utilizar directamente WhatsApp Business Cloud API para clientes enterprise.

## Módulo inmobiliario

El primer vertical incluirá:

- Propiedades.
- Propietarios.
- Leads.
- Contactos.
- Asesores.
- Visitas.
- Operaciones de venta y alquiler.
- Ubicaciones.
- Etiquetas.
- Seguimientos.

Más adelante podrán incorporarse mapas, geolocalización, publicación en portales, portal de propiedades y disponibilidad avanzada.

## Arquitectura conceptual

```text
Agency
  └─ ClientAccount
       ├─ Users
       ├─ Agents
       ├─ Channels
       ├─ Conversations
       ├─ Properties
       ├─ Leads
       ├─ Automations
       ├─ Tasks
       └─ AuditLogs
```

## Arquitectura técnica

```text
Next.js
  → interfaz, tenants, usuarios, inbox y WhatsApp

FastAPI + LangGraph
  → agentes, herramientas, permisos y planes

Automation Engine
  → ejecución de flujos

WhatsAppProvider
  → Kapso o Meta Cloud API

OpenRouter/OpenAI
  → modelos de IA
```

No se levantará un agente independiente por cliente. Se utilizará un runtime compartido con contexto y permisos aislados.

## Diferenciación

La plataforma combinará:

- Agente de IA embebido.
- Automatizaciones visuales.
- Automatizaciones creadas mediante lenguaje natural.
- Datos propios del negocio.
- Herramientas con permisos.
- WhatsApp e inbox.
- Seguimientos automáticos.
- CRM inmobiliario.
- Administración multi-tenant para agencias.

La propuesta central es:

> El usuario puede crear automatizaciones conversando, revisarlas visualmente en un lienzo y ejecutarlas de forma segura dentro del entorno de cada cliente.

## MVP

Incluirá:

- Agencia y cuentas cliente.
- Usuarios y permisos básicos.
- Conexión de WhatsApp mediante Kapso.
- Inbox.
- Propiedades.
- Leads.
- Agente conversacional.
- Configuración visual del agente.
- Herramientas básicas.
- Agente operador.
- Constructor visual de automatizaciones.
- Creación de automatizaciones mediante chat.
- Confirmación antes de activar.
- Seguimientos.
- Auditoría.
- Métricas básicas.

### Fuera del MVP inicial

- Múltiples canales.
- White-label completo.
- Facturación automatizada.
- Mapas avanzados.
- Publicación en portales.
- Self-hosted.
- Multi-agent complejo.
- Sandboxes.
- RAG empresarial avanzado.
- Marketplace de automatizaciones.

## Modelo comercial

### SaaS administrado

Suscripción mensual con infraestructura y mantenimiento incluidos.

### Instancia dedicada

Para agencias con mayor volumen, aislamiento o requisitos de privacidad.

### Self-hosted

Licencia, instalación y soporte para clientes que necesiten operar la plataforma en sus propios servidores.

La estrategia inicial será ofrecer un piloto accesible a una agencia durante aproximadamente una semana, a cambio de feedback y testimonio.

## Demo ideal

1. La agencia crea dos inmobiliarias.
2. Cada una conecta un número diferente.
3. Cada cuenta carga propiedades distintas.
4. Un interesado escribe por WhatsApp.
5. El agente busca únicamente propiedades de esa cuenta.
6. Responde y crea o actualiza el lead.
7. El operador solicita una automatización mediante chat.
8. El sistema genera un borrador.
9. El flujo aparece en el lienzo visual.
10. El operador lo confirma.
11. El flujo se activa.
12. La ejecución queda registrada.

## Posicionamiento

> Plataforma de agentes de IA y automatizaciones visuales para agencias inmobiliarias.

Otra formulación:

> Un entorno donde las agencias administran WhatsApp, CRM, agentes de IA y automatizaciones para todos sus clientes desde un único lugar.

## Visión

Construir un core reutilizable para agencias y módulos verticales por industria:

```text
core de agentes
+ conversaciones
+ automatizaciones visuales
+ herramientas
+ permisos
```

Con módulos específicos para inmobiliarias, academias, clínicas, concesionarias y servicios profesionales.

## Principio central

```text
El agente interpreta.
Las herramientas validan.
El usuario confirma.
El lienzo permite revisar.
El motor ejecuta.
El sistema audita.
```

## Resumen ejecutivo

La plataforma permitirá que una agencia administre múltiples inmobiliarias, cada una con su propio agente, datos, WhatsApp, conversaciones y automatizaciones.

Combinará dos experiencias:

```text
Lenguaje natural
→ creación rápida de automatizaciones

Lienzo visual
→ revisión, edición y control detallado
```

El objetivo será demostrar que una agencia puede recibir consultas, responder automáticamente, calificar leads, actualizar información, notificar asesores, crear seguimientos y diseñar automatizaciones desde una única plataforma.

La propuesta de valor final es:

> Una plataforma multi-tenant para que las agencias creen y administren agentes de IA y automatizaciones comerciales para sus clientes desde una única experiencia especializada.

## Próximos pasos

1. Definir `Agency → ClientAccount`.
2. Mantener Kapso detrás de `WhatsAppProvider`.
3. Completar el inbox y CRM inmobiliario básico.
4. Configurar el agente conversacional desde la interfaz.
5. Crear herramientas con permisos.
6. Implementar el agente operador.
7. Diseñar el modelo de automatizaciones.
8. Crear el lienzo visual.
9. Permitir generar flujos mediante lenguaje natural.
10. Agregar confirmación, ejecución y auditoría.
11. Probar con una agencia piloto.
12. Medir resultados y obtener testimonios.

La estrategia inicial es:

```text
Kapso para validar rápido
+ agent-service propio
+ LangGraph
+ motor de automatizaciones
+ lienzo visual
+ core reutilizable
```
