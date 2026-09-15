# PDR — Plataforma SaaS Multi-Tenant de Agentes de IA y Automatizaciones para Agencias

**Versión:** 1.0
**Fecha:** Agosto 2026
**Estado:** Borrador para validación con agencia piloto
**Vertical inicial:** Inmobiliario

---

## 1. Resumen ejecutivo

La plataforma permite que una agencia (de marketing, automatización, tecnología o especializada en inmobiliarias) administre, desde un único panel, múltiples cuentas cliente (inmobiliarias), cada una con su propio número de WhatsApp, base de propiedades, leads, agente de IA y automatizaciones.

El producto combina dos formas complementarias de crear automatizaciones:

- **Lenguaje natural** → creación rápida, conversando con un agente operador.
- **Lienzo visual** → revisión, edición y control detallado, inspirado en herramientas como wacrm.

El principio de diseño central del sistema es:

> El agente interpreta → las herramientas validan → el usuario confirma → el lienzo permite revisar → el motor ejecuta → el sistema audita.

El objetivo del MVP es demostrar, con una agencia piloto, que este modelo reduce la dispersión de herramientas (CRM + WhatsApp + chatbots + automatizadores + IA externa) que hoy enfrentan las agencias, resolviendo el vertical inmobiliario primero, con una arquitectura preparada para extenderse a otros sectores (academias, clínicas, concesionarias, servicios profesionales).

---

## 2. Problema a resolver

Las agencias que atienden a múltiples clientes suelen operar con una combinación de herramientas desconectadas: CRM, WhatsApp, chatbots, automatizadores y servicios de IA externos. Esto genera:

| Problema | Consecuencia |
|---|---|
| Datos dispersos entre herramientas | Falta de una fuente única de verdad por cliente |
| Configuraciones complejas | Alto costo de implementación por cada nueva cuenta |
| Costos acumulados de múltiples licencias | Baja rentabilidad para la agencia |
| Dificultad para escalar a nuevos clientes | Fricción operativa en el crecimiento |
| Seguimientos manuales | Leads perdidos, tiempos de respuesta altos |
| Falta de contexto para los agentes de IA | Respuestas genéricas, poco relevantes por cuenta |

---

## 3. Solución propuesta

Una plataforma especializada, multi-tenant, que integra en un mismo entorno:

- WhatsApp (canal conversacional)
- Inbox unificado
- CRM inmobiliario
- Agentes de IA (conversacional + operador)
- Base de conocimiento por cuenta
- Clasificación de leads
- Seguimientos automáticos
- Automatizaciones (visuales y por lenguaje natural)
- Integraciones externas
- Auditoría y métricas

Cada inmobiliaria (cuenta cliente) mantiene datos, canales, usuarios, permisos y configuración de agente completamente aislados del resto.

---

## 4. Usuarios objetivo

- **Agencias de marketing** que gestionan campañas y comunicación para múltiples clientes.
- **Agencias de automatización** que implementan flujos y bots por cuenta.
- **Agencias tecnológicas** que ofrecen soluciones a medida.
- **Agencias especializadas en inmobiliarias** (usuario principal del vertical inicial).

Dentro de cada agencia, se identifican dos perfiles de uso:

- **Usuario no técnico** (ej. asesor, dueño de inmobiliaria): necesita crear automatizaciones simples mediante lenguaje natural, sin fricción.
- **Usuario avanzado** (ej. administrador de la agencia): necesita revisar, ajustar y controlar el detalle técnico de los flujos en el lienzo visual.

---

## 5. Posicionamiento

> Plataforma de agentes de IA y automatizaciones visuales para agencias inmobiliarias.

Formulación alternativa:

> Un entorno donde las agencias administran WhatsApp, CRM, agentes de IA y automatizaciones para todos sus clientes desde un único lugar.

### 5.1 Diferenciación

El producto **no** es simplemente un chatbot, un CRM genérico, un inbox de WhatsApp o un constructor visual de automatizaciones por separado. Su diferencial está en la combinación de:

- Agente de IA embebido con contexto propio por cuenta.
- Automatizaciones visuales (lienzo tipo wacrm).
- Automatizaciones creadas mediante lenguaje natural.
- Datos propios del negocio (CRM inmobiliario).
- Herramientas del agente con sistema de permisos.
- WhatsApp e inbox integrados de forma nativa.
- Seguimientos automáticos auditables.
- Administración multi-tenant pensada para agencias, no para una sola empresa.

La estrategia inicial es validar valor con una agencia piloto, **sin competir desde el día uno** en cantidad de funcionalidades con plataformas como GoHighLevel.

---

## 6. Arquitectura conceptual (modelo de tenants)

```
Agency
  └─ ClientAccount
       ├─ Users
       ├─ Agents
       ├─ Channels
       ├─ Conversations
        ├─ Meetings (Fase 2)
        ├─ Properties
        ├─ Leads
        ├─ CustomFields (Fase 2)
        ├─ Automations
        ├─ Tasks
        └─ AuditLogs
```

Cada `ClientAccount` (ej. una inmobiliaria) es una unidad aislada de datos, canales y configuración de agente, dentro del espacio de una `Agency`.

---

## 7. Arquitectura técnica

```
Next.js
  → interfaz, gestión de tenants, usuarios, inbox y WhatsApp

FastAPI + LangGraph
  → orquestación de agentes, herramientas, permisos y planes de acción

Automation Engine
  → ejecución de flujos (triggers, condiciones, esperas, reintentos)

WhatsAppProvider
  → abstracción sobre Kapso (MVP) o Meta Cloud API (futuro)

OpenRouter / OpenAI
  → modelos de lenguaje subyacentes
```

**Decisión de diseño clave:** no se levanta un agente ni un runtime independiente por cada cliente. Se utiliza un **runtime compartido**, con aislamiento de contexto y permisos por cuenta, para mantener costos y complejidad operativa controlados a medida que se suman clientes.

### 7.1 Abstracción de WhatsApp

```
WhatsAppProvider
  ├─ KapsoProvider        (MVP)
  └─ MetaCloudProvider     (futuro)
```

Esto permite lanzar rápido con Kapso sin acoplar el resto del sistema a ese proveedor, dejando abierta la migración a la API directa de WhatsApp Business Cloud para clientes enterprise.

**Modalidades futuras de canal:**

| Plan | Proveedor de WhatsApp |
|---|---|
| Estándar | Kapso |
| Avanzado / Enterprise | Meta Cloud API directa |
| Instalación privada | Proveedor configurable según el cliente |

---

## 8. Agentes de IA

### 8.1 Agente conversacional

Atiende directamente a los interesados por WhatsApp. Responsabilidades:

- Responder preguntas de los leads.
- Buscar propiedades (acotado a los datos de la cuenta correspondiente).
- Extraer datos relevantes del lead durante la conversación.
- Crear o actualizar registros de lead.
- Agregar etiquetas de clasificación.
- Notificar al asesor correspondiente.
- Crear tareas de seguimiento.
- Consultar disponibilidad en calendario.
- Coordinar visitas.
- Derivar la conversación a un humano cuando corresponda.

> La consulta de calendarios y la coordinación de visitas mediante Google Calendar
> pertenecen a Fase 2; no forman parte del MVP.

**Configuración disponible desde la interfaz (sin código):**

- Prompt / instrucciones del agente.
- Modelo de IA a utilizar.
- Temperatura.
- Tono de comunicación.
- Horarios de atención.
- Herramientas habilitadas.
- Permisos por herramienta.
- Reglas de derivación a humano.

### 8.2 Agente operador

Asiste a la agencia o a la inmobiliaria desde el panel de administración (no conversa con leads). Responsabilidades:

- Crear automatizaciones a partir de instrucciones en lenguaje natural.
- Crear tareas.
- Consultar métricas.
- Buscar leads.
- Detectar conversaciones sin responder.
- Modificar o pausar flujos existentes.
- Proponer seguimientos.
- Generar resúmenes comerciales.

---

## 9. Motor de automatizaciones

### 9.0 Dos capas de automatización (distinción arquitectónica clave)

El sistema tiene **dos superficies de automatización distintas**, que no deben confundirse ni intentar unificarse en un mismo mecanismo:

| | Comportamiento del agente en el chat | Motor de automatizaciones |
|---|---|---|
| **Qué es** | Decisiones del agente turno a turno dentro de una conversación (etiquetar, extraer datos, decidir si un lead está interesado, elegir qué herramienta usar) | Reglas explícitas de trigger → condición → acción sobre eventos ya registrados en el sistema |
| **Cómo se controla** | De forma indirecta: prompt, herramientas habilitadas, permisos, tono, reglas de derivación. No es un objeto editable paso a paso | De forma directa: se crea, visualiza, edita, pausa y audita como un flujo concreto en el lienzo visual |
| **Qué dispara la acción** | El juicio del agente en tiempo real, dentro del contexto de la conversación | Un **evento estructurado y discreto** ya consumado en los datos (ej. una etiqueta que se aplicó, un lead que cambió de estado) — nunca una inferencia del agente en curso |
| **Visibilidad** | Opaca: no hay un diagrama que abrir para ver "por qué" el agente etiquetó algo en un momento puntual | Transparente: cada paso del flujo es visible, revisable y tiene historial de ejecución |

**Regla de diseño:** el motor de automatizaciones nunca debe intentar disparar sobre una interpretación subjetiva del agente mientras esa interpretación está ocurriendo. El puente entre las dos capas es que las acciones del agente (por ejemplo, aplicar una etiqueta) generan los eventos estructurados que el motor de automatizaciones sí puede usar como trigger.

**Ejemplo del flujo completo:**

```
Capa 1 (chat, opaca):
  El agente conversa con el lead → interpreta interés → aplica la etiqueta "interesado"

Capa 2 (motor, determinista):
  Trigger: etiqueta "interesado" aplicada a un lead
    → Condición: (ninguna, o las que se configuren)
    → Acción: actualizar el pipeline a la etapa "Interesado"
    → Acción: notificar al asesor
```

El trigger de la Capa 2 **no** es "el agente interpretó interés" — es el hecho ya consumado de que la etiqueta quedó aplicada. Esa diferencia es lo que permite que el flujo de la Capa 2 sea auditable, reproducible y editable en el lienzo, mientras que el comportamiento de la Capa 1 se sigue gobernando por configuración del agente (prompt, herramientas, permisos), no por un diagrama de flujo.

### 9.1 Principio de seguridad de ejecución

El agente **nunca ejecuta directamente** acciones sensibles sobre los datos del cliente. El flujo siempre pasa por una validación intermedia:

```
Agente interpreta
   → genera borrador
      → usuario confirma
         → motor ejecuta
            → sistema audita
```

### 9.2 Responsabilidades del motor

- Triggers (disparadores de eventos).
- Condiciones.
- Tiempos de espera.
- Recordatorios.
- Reintentos ante fallos.
- Notificaciones.
- Actualización de leads.
- Asignación de conversaciones a asesores.
- Gestión de citas/visitas.

### 9.3 Herramientas (tools) del agente — set inicial

```
search_properties      buscar propiedades de la cuenta
extract_lead_data      extraer datos estructurados del lead desde la conversación
create_lead            crear un nuevo lead
update_lead            actualizar un lead existente
add_tag                agregar etiqueta de clasificación
notify_advisor         notificar al asesor asignado
create_followup        crear un seguimiento programado
 check_calendar         consultar disponibilidad
 create_appointment     agendar una visita
 handoff_to_human       derivar la conversación a un humano
```

Cada herramienta se habilita por cuenta (`ClientAccount`) y se valida mediante el sistema de permisos, de modo que un agente nunca tiene acceso a capacidades no autorizadas explícitamente para esa cuenta.

Las herramientas de calendario y visitas anteriores se mantienen como capacidades
planificadas para Fase 2. No se consideran herramientas disponibles en el MVP.

---

## 10. Creación de automatizaciones — dos modalidades

### 10.1 Constructor visual (lienzo)

Lienzo tipo canvas, inspirado en soluciones como wacrm, donde el usuario arma flujos por bloques.

**Ejemplo de flujo:**

```
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

**Capacidades del usuario en el lienzo:**

- Crear nodos.
- Conectar pasos entre sí.
- Definir condiciones.
- Configurar tiempos de espera.
- Agregar acciones (mapeadas a las herramientas del agente).
- Activar, pausar y editar flujos.
- Consultar historial de ejecuciones.

### 10.2 Creación mediante lenguaje natural

El usuario le pide al **agente operador**, en texto libre, algo como:

> "Cuando un lead pregunte por una visita, etiquetalo como interesado, avisale al asesor y creá un recordatorio para mañana."

El agente traduce esa instrucción en una automatización estructurada, que sigue este ciclo:

1. Se genera el borrador de la automatización.
2. El usuario lo revisa.
3. El usuario puede modificarlo.
4. El usuario lo confirma.
5. El flujo queda activo y visible/editable en el lienzo visual.

### 10.3 Modelo combinado

```
Lenguaje natural   → creación rápida
Lienzo visual      → revisión y control detallado
```

Esto permite que usuarios sin conocimientos técnicos puedan crear flujos funcionales, mientras que los usuarios avanzados conservan control granular sobre el comportamiento exacto del sistema.

---

## 11. Módulo inmobiliario (vertical inicial)

### 11.1 Incluido en el MVP

- Propiedades
- Propietarios
- Leads
- Contactos
- Asesores
- Visitas
- Operaciones de venta y alquiler
- Ubicaciones
- Etiquetas
- Seguimientos

### 11.2 Postergado para etapas posteriores

- Mapas y geolocalización
- Publicación en portales inmobiliarios
- Portal de propiedades propio
- Disponibilidad avanzada
- Integraciones externas adicionales

---

## 12. Alcance del MVP

### 12.1 Incluido

- Modelo de agencia y cuentas cliente (`Agency` → `ClientAccount`).
- Usuarios y permisos básicos.
- Conexión de WhatsApp mediante Kapso.
- Inbox de conversaciones.
- Módulo de propiedades.
- Módulo de leads.
- Agente conversacional funcional.
- Configuración visual del agente (sin código).
- Set básico de herramientas para el agente.
- Agente operador.
- Constructor visual de automatizaciones.
- Creación de automatizaciones mediante chat/lenguaje natural.
- Paso de confirmación obligatorio antes de activar cualquier automatización.
- Seguimientos.
- Auditoría de acciones del sistema.
- Métricas básicas.

### 12.2 Fuera de alcance (postergado)

- Múltiples canales más allá de WhatsApp.
- Reuniones, transcripciones, resúmenes e integraciones con Fathom, Zoom, Google Meet o Google Calendar.
- Campos personalizados configurables por `ClientAccount`.
- Automatizaciones post-reunión y las herramientas normalizadas de reuniones.
- Formularios, planificados para Fase 3.
- White-label completo.
- Facturación automatizada.
- Mapas avanzados.
- Publicación en portales inmobiliarios.
- Modalidad self-hosted.
- Orquestación multi-agente compleja.
- Sandboxes de ejecución.
- RAG empresarial avanzado.
- Marketplace de automatizaciones.

---

## 13. Requisitos funcionales (RF)

| ID | Requisito |
|---|---|
| RF-01 | El sistema debe permitir crear una `Agency` y múltiples `ClientAccount` asociadas. |
| RF-02 | Cada `ClientAccount` debe tener datos, usuarios, canales y configuración de agente completamente aislados de las demás. |
| RF-03 | El sistema debe permitir conectar un número de WhatsApp por cuenta a través de Kapso. |
| RF-04 | El sistema debe ofrecer un inbox unificado de conversaciones por cuenta. |
| RF-05 | El sistema debe permitir cargar y administrar propiedades, propietarios, contactos, asesores y visitas por cuenta. |
| RF-06 | El agente conversacional debe poder buscar propiedades limitándose exclusivamente a los datos de la cuenta activa. |
| RF-07 | El agente conversacional debe poder crear y actualizar leads a partir de la conversación de WhatsApp. |
| RF-08 | El sistema debe permitir configurar el agente conversacional (prompt, modelo, temperatura, tono, horarios, herramientas, permisos, reglas de derivación) desde la interfaz, sin código. |
| RF-09 | El agente operador debe poder generar un borrador de automatización a partir de una instrucción en lenguaje natural. |
| RF-10 | Toda automatización generada por lenguaje natural debe poder revisarse y editarse en el lienzo visual antes de activarse. |
| RF-11 | El sistema debe requerir confirmación explícita del usuario antes de activar cualquier automatización nueva o modificada. |
| RF-12 | El lienzo visual debe permitir crear nodos, conectarlos, definir condiciones, configurar esperas y agregar acciones. |
| RF-13 | El usuario debe poder activar, pausar y editar automatizaciones existentes. |
| RF-14 | El sistema debe mantener un historial de ejecuciones por automatización. |
| RF-15 | Cada herramienta del agente (`search_properties`, `create_lead`, etc.) debe poder habilitarse o deshabilitarse por cuenta. |
| RF-16 | El sistema debe validar los permisos de cada herramienta antes de permitir su ejecución. |
| RF-17 | El sistema debe registrar en un log de auditoría toda acción ejecutada por el motor de automatizaciones o por un agente. |
| RF-18 | El sistema debe permitir crear seguimientos (`followups`) programados, manuales o generados por el agente. |
| RF-19 | El sistema debe notificar al asesor asignado ante eventos relevantes (ej. lead interesado). |
| RF-20 | El sistema debe exponer métricas básicas de conversaciones, leads y automatizaciones por cuenta. |
| RF-21 | El sistema debe permitir la derivación de una conversación del agente a un humano. |

---

## 14. Requisitos no funcionales (RNF)

| ID | Requisito |
|---|---|
| RNF-01 | **Aislamiento de datos:** ninguna cuenta cliente debe poder acceder, directa o indirectamente, a datos de otra cuenta. |
| RNF-02 | **Costo operativo:** el runtime de agentes debe ser compartido entre cuentas (no un proceso dedicado por cliente), para mantener costos de infraestructura controlados al escalar. |
| RNF-03 | **Extensibilidad de proveedores:** la integración de WhatsApp debe estar encapsulada detrás de una interfaz (`WhatsAppProvider`) que permita agregar proveedores sin modificar la lógica de negocio. |
| RNF-04 | **Seguridad de ejecución:** ninguna acción sensible (crear/modificar datos, notificar, agendar) debe ejecutarse sin pasar por el motor de automatizaciones y su validación de permisos. |
| RNF-05 | **Auditabilidad:** toda acción automatizada debe quedar registrada de forma trazable (quién/qué la originó, cuándo se ejecutó, resultado). |
| RNF-06 | **Modularidad del core:** la lógica de agencias, usuarios, permisos, conversaciones, agentes, herramientas y automatizaciones debe mantenerse desacoplada de la lógica específica del vertical inmobiliario, para permitir agregar nuevos verticales sin reescribir el core. |
| RNF-07 | **Configurabilidad sin código:** la configuración del agente conversacional debe poder realizarse íntegramente desde la interfaz, sin requerir despliegues ni cambios de código. |
| RNF-08 | **Disponibilidad del canal:** la capa de WhatsApp debe soportar reintentos ante fallos de entrega o de la API del proveedor. |

---

## 15. Flujo de referencia (demo ideal / caso de uso principal)

1. La agencia crea dos inmobiliarias (`ClientAccount`).
2. Cada inmobiliaria conecta un número de WhatsApp diferente.
3. Cada cuenta carga su propio set de propiedades.
4. Un interesado escribe por WhatsApp a una de las cuentas.
5. El agente conversacional busca únicamente propiedades de esa cuenta.
6. El agente responde y crea o actualiza el lead correspondiente.
7. El operador de la agencia solicita, por chat, una nueva automatización.
8. El sistema genera un borrador de automatización.
9. El flujo generado aparece representado en el lienzo visual.
10. El operador revisa y confirma el flujo.
11. El flujo se activa.
12. La ejecución del flujo queda registrada en el log de auditoría.

**Automatización de ejemplo resultante:**

```
Mensaje recibido
→ responder
→ extraer datos
→ agregar etiqueta "interesado"
→ notificar asesor
→ crear seguimiento
```

---

## 16. Modelo comercial

| Modalidad | Descripción |
|---|---|
| **SaaS administrado** | Suscripción mensual, infraestructura y mantenimiento incluidos. |
| **Instancia dedicada** | Para agencias con mayor volumen o requisitos de aislamiento/privacidad. |
| **Self-hosted** | Licencia, instalación y soporte para clientes que operan la plataforma en sus propios servidores (postergado, no forma parte del MVP). |

**Estrategia de entrada al mercado:** ofrecer un piloto accesible a una agencia real durante aproximadamente una semana, a cambio de feedback estructurado y testimonio.

---

## 17. Visión de producto y roadmap

### 17.1 Core reutilizable

```
core de agentes
+ conversaciones
+ automatizaciones visuales
+ herramientas
+ permisos
```

### 17.2 Verticales futuros sobre el mismo core

```
inmobiliarias   (MVP)
academias
clínicas
concesionarias
servicios profesionales
```

### 17.3 Extensiones futuras (post-MVP)

- Integración directa con Meta Cloud API.
- White-label completo.
- Instancias dedicadas.
- Modalidad self-hosted.
- Mapas y portales inmobiliarios.
- Nuevos verticales de industria.
- Integraciones enterprise adicionales.

### 17.4 Fase 2 — Reuniones, integraciones y contexto del contacto

#### Objetivo y alcance

Fase 2 extenderá la plataforma desde la gestión de conversaciones hacia un
contexto unificado del contacto. El objetivo es que una reunión, su contenido y
las acciones derivadas queden vinculados al `ClientAccount` y al contacto
correspondiente, sin romper el aislamiento multi-tenant ni el principio de
ejecución validada y auditable.

Fase 2 incluirá:

- El módulo **Reuniones**.
- Integraciones posibles con **Fathom**, **Zoom**, **Google Meet** y **Google Calendar**.
- Campos personalizados configurables por `ClientAccount`.
- Automatizaciones post-reunión creadas desde lenguaje natural y revisables en el lienzo visual.
- Herramientas normalizadas del agente para consultar reuniones, actualizar contactos, crear tareas, crear eventos y agregar etiquetas.

Las integraciones se incorporarán de forma modular, sin asumir en este PDR un
proveedor único ni definir detalles de implementación que todavía no fueron
decididos.

#### Módulo Reuniones

El módulo registrará, por cuenta cliente, reuniones grabadas y su información
asociada:

- Grabación de la reunión, cuando el proveedor la exponga.
- Transcripción.
- Resumen.
- Participantes.
- Fecha y duración.
- Contacto asociado.
- Acciones detectadas.

El registro de la reunión deberá conservar la relación con el contacto y la
cuenta que originó la integración. Las acciones detectadas serán insumos para
revisión y automatización, no ejecuciones directas del agente.

#### Flujo reunión → acción

```text
Reunión
  → transcripción
    → resumen
      → extracción de datos, acciones y próximos pasos
        → actualización del contacto
          → tarea o automatización post-reunión
```

El flujo respetará el principio general de la plataforma: el agente interpreta
el contenido, las herramientas validan los cambios, el usuario confirma las
automatizaciones o acciones sensibles, el motor ejecuta y el sistema audita.

#### Automatizaciones post-reunión

El usuario podrá expresar reglas en lenguaje natural, por ejemplo:

> Después de cada reunión, si se detecta interés en una propiedad, actualizá el contacto, agregá la etiqueta "interesado" y creá una tarea de seguimiento.

Un caso de uso representativo será:

```text
Trigger: resumen de reunión creado
Condición: intención de compra = alta
Acciones:
  - agregar la etiqueta "lead caliente"
  - crear una tarea para contactar al cliente en 24 horas
  - notificar al asesor responsable
```

La automatización no se ejecutará directamente a partir de una inferencia del
modelo. El agente deberá presentar los datos extraídos y el borrador de la
regla para que el usuario los revise y confirme.

El agente operador convertirá la instrucción en un borrador estructurado con
trigger, condiciones y acciones. Antes de publicarla en el lienzo visual o
activarla, deberá existir confirmación humana explícita. El usuario podrá
revisar y editar el borrador en el lienzo, de acuerdo con el modelo combinado de
lenguaje natural y control visual ya definido para la plataforma.

#### Herramientas normalizadas del agente

Fase 2 definirá un conjunto estable de herramientas, independiente del
proveedor externo:

```text
list_meetings          listar reuniones de la cuenta o del contacto
get_meeting_summary    obtener el resumen de una reunión
update_contact         actualizar datos del contacto
create_task            crear una tarea de seguimiento
create_calendar_event  crear un evento de calendario
add_tag                agregar una etiqueta al contacto
```

Cada herramienta quedará sujeta a habilitación por `ClientAccount`, permisos,
validación del motor y auditoría, igual que el resto de las herramientas del
agente.

#### Arquitectura modular de integraciones

La arquitectura de Fase 2 mantendrá separadas las interfaces de integración y
la lógica de negocio:

```text
IntegrationProvider
  ├─ MeetingProvider
  │    ├─ FathomProvider (posible)
  │    ├─ ZoomProvider (posible)
  │    └─ GoogleMeetProvider (posible)
  └─ CalendarProvider
       └─ GoogleCalendarProvider (posible)
```

`MeetingProvider` normalizará la recepción de reuniones, grabaciones,
transcripciones, resúmenes, participantes, fecha, duración y acciones
detectadas. `CalendarProvider` normalizará la creación de eventos de calendario.
Ambos se encuadran bajo `IntegrationProvider` para evitar acoplar el módulo
Reuniones a Fathom, Zoom, Google Meet o Google Calendar.

#### Campos personalizados

Cada `ClientAccount` podrá configurar sus propios campos personalizados para
contactos y para los datos que se extraigan de reuniones. La configuración será
por cuenta, respetará el aislamiento de datos y permitirá que las extracciones y
actualizaciones se expresen mediante las herramientas normalizadas, sin imponer
un esquema idéntico a todas las inmobiliarias.

Ejemplos de campos configurables por una inmobiliaria:

- Presupuesto.
- Tipo de operación.
- Zona preferida.
- Cantidad de dormitorios.
- Fecha estimada de compra.
- Nivel de interés.
- Origen del lead.

Estos campos pertenecen a `ClientAccount`; no son campos globales compartidos
por todas las inmobiliarias.

#### Propuesta de valor y evolución

Fase 2 permitirá pasar de un historial fragmentado de conversaciones a un
contexto unificado del contacto: conversaciones, reuniones, transcripciones,
resúmenes, datos personalizados, tareas y automatizaciones relacionadas en un
mismo entorno por `ClientAccount`. Esto mejora la continuidad comercial y
permite que el agente proponga acciones con contexto verificable, manteniendo al
usuario en control antes de publicar o ejecutar automatizaciones.

La navegación prevista para esta etapa podrá incluir:

```text
Resumen · Inbox · Leads · Propiedades · Pipeline · Agenda · Automatizaciones
Reuniones · WhatsApp · Conocimiento · Métricas · Configuración
```

### 17.5 Fase 3 — Formularios y capacidades avanzadas

Los formularios quedan explícitamente fuera de Fase 2. Se planifican para Fase
3 junto con capacidades avanzadas que se definan después de validar el uso de
reuniones, integraciones, contexto unificado y automatizaciones post-reunión.

Fase 3 podrá ampliar el modelo de captura y automatización sobre la base
validada en las fases anteriores, pero este PDR no fija todavía decisiones
técnicas ni un alcance detallado para esas capacidades.

El alcance previsto contempla:

- Formularios nativos de captación asociados a una cuenta.
- Formularios externos.
- Constructor avanzado de campos.
- Creación automática de leads y activación de automatizaciones después del envío.
- Automatizaciones multicanal y reportes de rendimiento de reuniones.

---

## 18. Decisión estratégica de construcción

```
Fork de WACRM como base de implementación inicial
+ adaptación progresiva del frontend, canvas y componentes reutilizables
+ core comercial y multi-tenant propio
+ patrones de scopes de QM (permisos)
+ Kapso como integración inicial de WhatsApp
+ LangGraph para orquestación de agentes
+ lienzo y componentes adaptados bajo contratos propios
```

La plataforma partirá de un **fork de WACRM** y lo adaptará progresivamente al dominio propio. WACRM será la base de implementación inicial, no una dependencia de dominio permanente. El core comercial y multi-tenant conservará control arquitectónico propio sobre `Agency → ClientAccount`, permisos, auditoría, providers y modelo de datos.

El fork no significa copiar sin evaluar. Antes de adoptarlo o distribuirlo se debe completar una auditoría de licencia, arquitectura, seguridad, extensibilidad y compatibilidad con el stack. El upstream/origen se mantendrá separado y sincronizable cuando sea viable. Las partes que no cumplan los contratos del producto se encapsularán o reemplazarán progresivamente, evitando que los supuestos de WACRM definan el core propio.

La decisión de conservar, adaptar o reemplazar cada componente dependerá de su compatibilidad con esos contratos, la calidad y seguridad del código, la facilidad de mantener límites claros y el costo de la deuda y de la divergencia. La due diligence de licencia es una precondición; también se deben considerar los riesgos de deuda técnica, modelo de datos, acoplamiento y mantenimiento del fork.

---

## 19. Riesgos y consideraciones

| Riesgo | Mitigación propuesta |
|---|---|
| Dependencia de Kapso como único proveedor de WhatsApp en el MVP | Abstracción `WhatsAppProvider` desde el diseño inicial, para migrar a Meta Cloud API sin reescribir lógica de negocio. |
| El agente ejecuta una acción no deseada sobre datos reales del cliente | Todo pasa por el ciclo interpretar → borrador → confirmación → ejecución → auditoría; ninguna herramienta sensible se ejecuta sin confirmación. |
| Complejidad de mantener runtime compartido con aislamiento por cuenta | Definir desde el MVP el modelo de permisos y contexto por `ClientAccount` como parte del core, no como agregado posterior. |
| Acoplar el core al vertical inmobiliario y dificultar la expansión a otros sectores | Separar explícitamente el core (agencias, usuarios, conversaciones, agentes, automatizaciones, auditoría) del módulo inmobiliario desde la arquitectura inicial. |
| Validar el producto con una sola agencia piloto puede no ser representativo | Definir criterios de éxito claros para el piloto y recolectar feedback estructurado antes de expandir funcionalidades. |

---

## 20. Próximos pasos

1. Definir el modelo `Agency` → `ClientAccount`.
2. Mantener Kapso encapsulado detrás de `WhatsAppProvider`.
3. Completar el inbox y el CRM inmobiliario básico.
4. Habilitar la configuración del agente conversacional desde la interfaz.
5. Crear el set inicial de herramientas con su sistema de permisos.
6. Implementar el agente operador.
7. Diseñar el modelo de datos de automatizaciones.
8. Construir el lienzo visual.
9. Habilitar la generación de flujos mediante lenguaje natural.
10. Agregar el ciclo de confirmación, ejecución y auditoría.
11. Probar la plataforma con una agencia piloto.
12. Medir resultados y obtener testimonios para validar el modelo antes de escalar.

---

## 21. Propuesta de valor (síntesis final)

> Una plataforma multi-tenant para que las agencias creen y administren agentes de IA y automatizaciones comerciales para sus clientes, combinando WhatsApp, CRM, conocimiento propio del negocio y un constructor visual de flujos, en una experiencia especializada — sin intentar competir en volumen de funciones desde el primer día, sino demostrando primero valor real con un vertical acotado (inmobiliario) y una arquitectura preparada para crecer.
