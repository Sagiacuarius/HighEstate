# HighEstate

Plataforma SaaS multi-tenant para agencias inmobiliarias: un único panel para administrar múltiples inmobiliarias (clientes), cada una con su propio catálogo de propiedades, leads, conversaciones, pipeline y agente de IA.

> **Estado:** demo local. No es producción: usa identidad dev-only, simulador de mensajes en lugar de WhatsApp real, y no tiene autenticación productiva ni RLS.

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
- **Providers**: el simulador de mensajes entrante entra por el mismo boundary normalizado que usará WhatsApp real (Kapso) a futuro.

---

## Funcionalidades (MVP demo)

- CRM inmobiliario: contactos, leads, propiedades, pipeline, conversaciones, seguimientos.
- Catálogo de propiedades enriquecido (amenities, vínculos con rol, intereses, máquina de estados comercial).
- Selector de cuenta demo (cambia entre dos inmobiliarias con aislamiento total).
- Simulador de mensaje entrante (dev-only): mensaje → lead → transición de pipeline → seguimiento → actividad.
- Calendario/agenda real con disponibilidad (lun–sáb 9–18 BA) y auditoría visible.
- Agente conversacional automático "Sofía": responde FAQs desde base de conocimiento, precios desde el catálogo vivo, agenda/reprograma visitas, clasifica prospectos y deriva al asesor humano ante pedidos explícitos o reclamos — todo auditado por acción.
- Consola del Agente (menú "Agente"): ajustes persistidos, redacción de prompt versionado, gestión de base de conocimiento, vinculación de calendario y allowlist de campos del prospecto.
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
RUNTIME_DB_NAME         # base demo, ej. production_runtime_mvp_wu16 (stack completo: motor + consola)
RUNTIME_DB_USER         # ej. postgres
NEXT_PUBLIC_DEMO_API_BASE_URL   # http://127.0.0.1:8001
```

No hay secrets en el repositorio: la contraseña de PostgreSQL va en `%APPDATA%\postgresql\pgpass.conf`.

---

## Setup

```powershell
# 1. Migraciones (001–009)
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 `
  RUNTIME_DB_NAME=production_runtime_mvp_wu16 RUNTIME_DB_USER=postgres `
  python infra/migrations/runner.py --up

# 2. Seeds demo (dos inmobiliarias + catálogo + KB + defaults de consola;
#    el orden importa: mvp, 005, 008, 009)
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu16 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed.sql
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu16 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed_005.sql
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu16 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed_008.sql
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu16 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed_009.sql
```

---

## Ejecutar

```powershell
# API demo (terminal 1) — base wu16 = stack completo (motor + consola)
RUNTIME_API_HOST=127.0.0.1 RUNTIME_API_PORT=8001 `
  RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 `
  RUNTIME_DB_NAME=production_runtime_mvp_wu16 RUNTIME_DB_USER=postgres `
  python -m api.demo_run

# Frontend (terminal 2) — OJO: NEXT_PUBLIC_* se hornea en el build,
# exportar ANTES de buildear o el cliente falla en runtime
$env:NEXT_PUBLIC_DEMO_API_BASE_URL = "http://127.0.0.1:8001"
npm.cmd --workspace web run build
npm.cmd --workspace web run start
```

Abrí `http://127.0.0.1:3000`.

> Si un puerto está ocupado por un proceso huérfano de una sesión anterior
> (`next start` o `api.demo_run` viejos), liberalo antes de levantar:
> `Get-NetTCPConnection -LocalPort 3000,8001 -State Listen` y
> `Stop-Process` del PID correspondiente.

---

## Tests

```powershell
# TypeScript (desde la raíz: dominio + contratos + UI, 10 archivos)
npm.cmd exec vitest run

# Python (contra PostgreSQL real; cada harness usa su base descartable
# production_runtime_mvp_wuNN vía RUNTIME_DB_NAME)
python -m compileall -q api infra
python -m api.test_agent_console_wu16    # consola (base wu16)
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
`test_agent_console_kb`, `test_agent_console_calendar`
(`python -m api.<modulo>`).

---

## Estructura

```text
docs/       PDR, brief comercial, arquitectura
demo/       maqueta visual de referencia
openspec/   cambios SDD (propuesta, spec, diseño, tareas, evidencia)
src/        dominio y puertos TypeScript
api/        FastAPI (domain, adapters, commands, queries, routes)
web/        Next.js (dashboard)
infra/      migraciones 001–009, runner, seeds/reset deterministas
contracts/  contratos HTTP versionados
```

La documentación de cada work unit (alcance, decisiones, evidencia de verificación) está en `openspec/changes/<change>/work-unit-*-evidence.md`.

---

## Roadmap

1. ~~**LLM / agente conversacional** — matching de propiedades contra el catálogo.~~ Hecho (WU14 borradores + WU15 autonomía + WU16 consola).
2. **Configuración** — custom fields (alimentan el allowlist de campos del agente), timezone y tags.
3. **WhatsApp real (Kapso)** — reemplaza el simulador por el mismo boundary.
4. **Automatizaciones** (lenguaje natural + lienzo visual).
5. **Fase 2**: reuniones, transcripciones, resúmenes, Google Calendar.
6. **Fase 3**: formularios, multicanal.
7. Deploy a VPS (Docker Compose + CI/CD).

---

## Limitaciones conocidas

- Identidad dev-only (single-session en memoria), sin autenticación productiva.
- Sin RLS (el aislamiento es por predicado de aplicación + FK compuestas).
- Simulador en lugar de integración real de WhatsApp.
- Cache de lectura process-local (válida para demo).
