# HighEstate

Plataforma SaaS multi-tenant para agencias inmobiliarias: un único panel para administrar múltiples inmobiliarias (clientes), cada una con su propio catálogo de propiedades, leads, conversaciones, pipeline y agente de IA.

> **Estado:** demo local. No es producción: usa identidad dev-only y no tiene autenticación productiva ni RLS. La integración real de WhatsApp (Kapso) ya está implementada (webhook firmado + cola durable + workers), pero necesita credenciales y un túnel HTTPS público para operar; sin ellas la demo usa el simulador.

---

## Stack

| Capa | Tecnología |
|---|---|
| Frontend | Next.js 15 (App Router), React 19, TypeScript |
| API | FastAPI (Python) |
| Base de datos | PostgreSQL 18 |
| Dominio/contratos | TypeScript (puertos hexagonales) |
| Tests | Vitest (TS), harness Python contra PostgreSQL real |

---

## Arquitectura

```text
Agency
  └─ ClientAccount   (inmobiliaria, unidad de aislamiento)
       ├─ contacts / leads / properties / pipeline
       ├─ conversations / messages / follow_ups
       └─ amenities / intereses / vínculos con rol
```

- **Multi-tenant**: `Agency → ClientAccount` con aislamiento estricto por `client_account_id` (FK compuestas, predicado obligatorio en cada query).
- **Clean Architecture / Hexagonal**: dominio y casos de uso no dependen de FastAPI, Next.js ni PostgreSQL.
- **Comandos atómicos**: idempotencia durable, transacción única, auditoría y outbox (patrón WU5/WU6).
- **Providers**: el simulador entrante y el webhook real de Kapso entran por el MISMO boundary normalizado (`NormalizedInboundEvent`); el agente y el CRM no distinguen el origen.
- **Cola durable**: el webhook de Kapso solo persiste y responde rápido; el trabajo pesado corre en workers sobre el outbox de PostgreSQL (un worker por tipo de evento, con `SKIP LOCKED`).

---

## Funcionalidades (MVP demo)

- CRM inmobiliario: contactos, leads, propiedades, pipeline, conversaciones, seguimientos.
- Catálogo de propiedades enriquecido (amenities, vínculos con rol, intereses, máquina de estados comercial).
- Selector de cuenta demo (cambia entre dos inmobiliarias con aislamiento total).
- Simulador de mensaje entrante (dev-only): mensaje → lead → transición de pipeline → seguimiento → actividad.
- Calendario/agenda real con disponibilidad (lun–sáb 9–18 BA) y auditoría visible.
- Agente conversacional automático "Sofía": responde FAQs desde base de conocimiento, precios desde el catálogo vivo, agenda/reprograma visitas, clasifica prospectos y deriva al asesor humano ante pedidos explícitos o reclamos — todo auditado por acción.
- Consola del Agente (menú "Agente"): ajustes persistidos, redacción de prompt versionado, gestión de base de conocimiento, vinculación de calendario y allowlist de campos del prospecto.
- Pipeline comercial configurable (menú "Embudo"): varios embudos, etapas con nombre y orden, borrado bloqueado si hay leads y advertencia al renombrar etapas que el agente usa.
- Alta y edición de propiedades desde "Configuración"; el catálogo operativo queda de solo lectura.
- WhatsApp real vía Kapso: webhook firmado (HMAC-SHA256) → cola durable en PostgreSQL → worker del agente → worker de salida que envía por la API de Kapso.
- Panel "Configuración → WhatsApp / Kapso" (solo lectura): estado de configuración, teléfonos mapeados, cola (pendientes/procesando/fallidos) y última actividad — sin exponer secretos.
- Dashboard con datos reales persistidos en PostgreSQL.

---

## Requisitos previos

- Node.js 20+ y npm.
- Python 3.11+ (con `fastapi`, `uvicorn`, `psycopg` — ver `api/requirements.txt`).
- PostgreSQL local (la contraseña se resuelve vía `pgpass`, no se guarda en el repo).

---

## Configuración

Variables de entorno necesarias:

```text
RUNTIME_API_HOST        # ej. 127.0.0.1
RUNTIME_API_PORT        # ej. 8001
RUNTIME_DB_HOST         # ej. 127.0.0.1
RUNTIME_DB_PORT         # ej. 5432
RUNTIME_DB_NAME         # base demo, ej. production_runtime_mvp_wu18 (stack completo: motor + consola + embudo)
RUNTIME_DB_USER         # ej. postgres
NEXT_PUBLIC_DEMO_API_BASE_URL   # http://127.0.0.1:8001
```

Integración WhatsApp real (Kapso) — opcional; sin estas variables la demo funciona con el simulador:

```text
KAPSO_API_KEY                  # API key de Kapso (envío saliente)
KAPSO_WEBHOOK_SECRET           # secret del webhook (verificación HMAC-SHA256 del entrante)
KAPSO_PHONE_ACCOUNT_MAP        # JSON {"<phone_number_id>": "<client_account_id>"}
KAPSO_API_BASE_URL             # default https://api.kapso.ai/meta/whatsapp/v24.0
KAPSO_API_TIMEOUT_SECONDS      # default 10
KAPSO_WORKER_INTERVAL_SECONDS  # default 5
```

`KAPSO_PHONE_ACCOUNT_MAP` debe ser JSON válido (si no, la API no arranca: falla cerrada). Con
`KAPSO_WEBHOOK_SECRET` vacío, todos los webhooks responden 401.

No hay secrets en el repositorio: la contraseña de PostgreSQL va en `%APPDATA%\postgresql\pgpass.conf`.

---

## Setup

```powershell
# 1. Migraciones (001–011)
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 `
  RUNTIME_DB_NAME=production_runtime_mvp_wu18 RUNTIME_DB_USER=postgres `
  python infra/migrations/runner.py --up

# 2. Seeds demo (dos inmobiliarias + catálogo + KB + consola + configuración;
#    el orden importa: mvp, 005, 008, 009, 010, 011)
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu18 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed.sql
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu18 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed_005.sql
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu18 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed_008.sql
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu18 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed_009.sql
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu18 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed_010.sql
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu18 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed_011.sql
```

---

## Ejecutar

```powershell
# API demo (terminal 1) — base wu18 = stack completo (motor + consola + embudo)
RUNTIME_API_HOST=127.0.0.1 RUNTIME_API_PORT=8001 `
  RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 `
  RUNTIME_DB_NAME=production_runtime_mvp_wu18 RUNTIME_DB_USER=postgres `
  python -m api.demo_run

# Frontend (terminal 2) — OJO: NEXT_PUBLIC_* se hornea en el build,
# exportar ANTES de buildear o el cliente falla en runtime
$env:NEXT_PUBLIC_DEMO_API_BASE_URL = "http://127.0.0.1:8001"
npm.cmd --workspace web run build
npm.cmd --workspace web run start
```

Abrí `http://127.0.0.1:3000`.

### WhatsApp real (Kapso)

Requiere las variables `KAPSO_*` de la sección anterior. Son **tres procesos**: la API y dos workers
(las env vars deben estar seteadas en la misma consola antes de arrancarlos).

```powershell
# Terminal 3 — worker del agente (procesa el entrante y decide la respuesta)
python -m api.kapso_agent_worker

# Terminal 4 — worker de salida (envía la respuesta por la API de Kapso)
python -m api.kapso_worker
```

Kapso necesita una URL pública HTTPS. Para desarrollo local, un túnel:

```powershell
ngrok http 8001
```

Registrá en Kapso (Integrations → Webhooks) el webhook:

```text
URL:     https://<tu-tunel>.ngrok-free.app/v1/webhooks/kapso
Evento:  whatsapp.message.received
Secret:  el mismo valor que KAPSO_WEBHOOK_SECRET
Formato: webhook de Kapso (NO Meta)
```

Verificación en el panel: `Configuración → WhatsApp / Kapso` (estado, teléfonos, cola, actividad).
Endpoints: `GET /v1/demo/kapso/status` (estado) y `POST /v1/webhooks/kapso` (entrante).

> Si un puerto está ocupado por un proceso huérfano de una sesión anterior
> (`next start` o `api.demo_run` viejos), liberalo antes de levantar:
> `Get-NetTCPConnection -LocalPort 3000,8001 -State Listen` y
> `Stop-Process` del PID correspondiente.

---

## Tests

```powershell
# TypeScript (desde la raíz: dominio + contratos + UI, 11 archivos / 134 tests)
npm.cmd exec vitest run

# Python (contra PostgreSQL real; cada harness usa su base descartable
# production_runtime_mvp_wuNN vía RUNTIME_DB_NAME)
python -m compileall -q api infra
python -m api.test_agent_console_wu16    # consola (base wu18)
python -m api.test_agent_custom_field_writes
python -m api.test_commercial_pipelines_wu19
python -m api.test_agent_autonomy_wu15   # motor autónomo (base wu15)
python -m api.test_agent_wu14            # borradores + aprobación (base wu15)
python -m api.test_calendar_wu13
python -m api.test_audit_screen_wu12
python -m api.test_property_catalog_wu11
python -m api.test_e2e_mvp
python -m api.test_simulator
python -m api.test_crm_reads
```

Units Python (sin DB o sobre las mismas bases): `test_model_provider`,
`test_conversation_agent`, `test_agent_policy`, `test_kb_retrieval`,
`test_agent_console_settings`, `test_agent_console_prompts`,
`test_agent_console_kb`, `test_agent_console_calendar`,
`test_pipeline_commands_wu19`, `test_property_catalog_wu8`,
`test_kapso_webhook`, `test_kapso_outbound`, `test_kapso_worker`,
`test_kapso_agent_consumer`, `test_kapso_agent_orchestrator`,
`test_kapso_agent_worker`, `test_kapso_status`, `test_outbox_filter`
(`python -m api.<modulo>`).

> `pytest` no es requisito: los harness/units se ejecutan como módulos. Cada
> `test_*.py` imprime su resultado; los que no traen `__main__` se invocan por
> función.

---

## Estructura

```text
docs/       PDR, brief comercial, arquitectura
demo/       maqueta visual de referencia
openspec/   cambios SDD (propuesta, spec, diseño, tareas, evidencia)
src/        dominio y puertos TypeScript
api/        FastAPI (domain, adapters, commands, queries, routes)
web/        Next.js (dashboard)
infra/      migraciones 001–011, runner, seeds/reset deterministas
contracts/  contratos HTTP versionados
```

La documentación de cada work unit (alcance, decisiones, evidencia de verificación) está en `openspec/changes/<change>/work-unit-*-evidence.md`.

---

## Roadmap

1. ~~**LLM / agente conversacional**~~ Hecho (WU14 borradores + WU15 autonomía + WU16 consola).
2. ~~**Configuración** — custom fields, timezone, tags y escritura de campos por el agente.~~ Hecho (WU17 + WU18).
3. ~~**Pipeline comercial** — embudos y etapas configurables.~~ Hecho (WU19).
4. ~~**WhatsApp real (Kapso)** — webhook firmado, cola durable y workers de entrada/salida.~~ Hecho en código (faltan credenciales y despliegue).
5. **Kapso — pendientes de producción**: eventos de estado (enviado/entregado/leído/fallido), tipos de mensaje no-texto (imagen/audio/documento/ubicación), configuración multi-tenant por cuenta (hoy es por entorno), auto-registro del webhook, monitoreo y alertas.
6. **Automatizaciones** (lenguaje natural + lienzo visual).
7. **Fase 2**: reuniones, transcripciones, resúmenes, Google Calendar.
8. **Fase 3**: formularios, multicanal.
9. Deploy a VPS (Docker Compose + CI/CD).

---

## Limitaciones conocidas

- Identidad dev-only (single-session en memoria), sin autenticación productiva.
- Sin RLS (el aislamiento es por predicado de aplicación + FK compuestas).
- Kapso: la configuración (API key, secret, mapeo de teléfonos) es global por proceso, no por cuenta; no hay auto-registro del webhook ni heartbeat real del worker (el panel muestra `not_observed`).
- Kapso: solo se procesan mensajes de texto; no hay eventos de estado (enviado/entregado/leído/fallido) ni mensajes no-texto.
- El panel de Kapso es de solo lectura: no carga credenciales ni muestra cuerpos de mensajes.
- `POST /v1/demo/reset` devuelve 500 sobre bases sembradas (defecto preexistente: el reset borra propiedades sin sus vínculos). Usar un re-seed limpio.
- Cache de lectura process-local (válida para demo).
