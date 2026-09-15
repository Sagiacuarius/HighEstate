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
RUNTIME_DB_NAME         # base demo, ej. production_runtime_mvp_wu10
RUNTIME_DB_USER         # ej. postgres
NEXT_PUBLIC_DEMO_API_BASE_URL   # http://127.0.0.1:8001
```

No hay secrets en el repositorio: la contraseña de PostgreSQL va en `%APPDATA%\postgresql\pgpass.conf`.

---

## Setup

```powershell
# 1. Migraciones (001–005)
RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 `
  RUNTIME_DB_NAME=production_runtime_mvp_wu10 RUNTIME_DB_USER=postgres `
  python infra/migrations/runner.py --up

# 2. Seed demo (dos inmobiliarias + datos deterministas)
psql -h 127.0.0.1 -p 5432 -U postgres -d production_runtime_mvp_wu10 `
  -v ON_ERROR_STOP=1 -f infra/migrations/validation/mvp_seed.sql
```

---

## Ejecutar

```powershell
# API demo (terminal 1)
RUNTIME_API_HOST=127.0.0.1 RUNTIME_API_PORT=8001 `
  RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 `
  RUNTIME_DB_NAME=production_runtime_mvp_wu10 RUNTIME_DB_USER=postgres `
  python -m api.demo_run

# Frontend (terminal 2)
$env:NEXT_PUBLIC_DEMO_API_BASE_URL = "http://127.0.0.1:8001"
npm.cmd --workspace web run build
npm.cmd --workspace web run start
```

Abrí `http://127.0.0.1:3000`.

---

## Tests

```powershell
# TypeScript (dominio + contratos + UI)
npm.cmd run typecheck
npm.cmd test

# Python (contra PostgreSQL real, base descartable por módulo)
python -m compileall -q api infra
python -m api.test_property_catalog_wu11
python -m api.test_crm_commands
python -m api.test_crm_reads
python -m api.test_demo_context
python -m api.test_simulator
python -m api.test_e2e_mvp
```

---

## Estructura

```text
docs/       PDR, brief comercial, arquitectura
demo/       maqueta visual de referencia
openspec/   cambios SDD (propuesta, spec, diseño, tareas, evidencia)
src/        dominio y puertos TypeScript
api/        FastAPI (domain, adapters, commands, queries, routes)
web/        Next.js (dashboard)
infra/      migraciones 001–005, runner, seed/reset
contracts/  contratos HTTP versionados
```

La documentación de cada work unit (alcance, decisiones, evidencia de verificación) está en `openspec/changes/<change>/work-unit-*-evidence.md`.

---

## Roadmap

1. **LLM / agente conversacional** — matching de propiedades contra el catálogo.
2. **WhatsApp real (Kapso)** — reemplaza el simulador por el mismo boundary.
3. **Automatizaciones** (lenguaje natural + lienzo visual).
4. **Fase 2**: reuniones, transcripciones, resúmenes, Google Calendar.
5. **Fase 3**: formularios, multicanal.
6. Deploy a VPS (Docker Compose + CI/CD).

---

## Limitaciones conocidas

- Identidad dev-only (single-session en memoria), sin autenticación productiva.
- Sin RLS (el aislamiento es por predicado de aplicación + FK compuestas).
- Simulador en lugar de integración real de WhatsApp.
- Cache de lectura process-local (válida para demo).
