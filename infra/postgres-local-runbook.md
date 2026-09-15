# PostgreSQL local mínimo — Runbook para Work Unit 3.5

## Propósito

Provisionar temporalmente una instancia PostgreSQL local para validar las migraciones de Work Unit 3. El entorno no incluye Next.js ni FastAPI y no es el Compose de producción.

## Estado detectado

En el entorno actual no están disponibles:

```text
docker
Docker Compose
psql
pg_isready
```

Por ese motivo este runbook no fue ejecutado y la validación contra PostgreSQL queda bloqueada.

## Opción recomendada: Docker Desktop

Requisito externo para Windows 10: instalar Docker Desktop desde el instalador oficial de Docker y reiniciar si el instalador lo solicita. No se ejecutó ninguna instalación automáticamente.

Después de instalar Docker Desktop, abrir una terminal nueva y verificar:

```bash
docker --version
docker compose version
```

Crear un archivo local temporal, fuera del Compose completo de producción, con una versión PostgreSQL fijada, por ejemplo:

```yaml
services:
  postgres-work-unit-3-5:
    image: postgres:16.4
    container_name: production-runtime-wu35-postgres
    restart: "no"
    environment:
      POSTGRES_USER: wu35_local
      POSTGRES_PASSWORD: wu35_local_only
      POSTGRES_DB: production_runtime_wu35
    ports:
      - "55432:5432"
    volumes:
      - wu35_postgres_data:/var/lib/postgresql/data

volumes:
  wu35_postgres_data:
```

Los valores anteriores son exclusivamente locales y de prueba; no son credenciales reales. No reutilizar este archivo como Compose de producción y no incluir Next.js/FastAPI.

Levantar y verificar:

```bash
docker compose -f postgres-work-unit-3-5.compose.yml up -d
docker compose -f postgres-work-unit-3-5.compose.yml ps
pg_isready -h 127.0.0.1 -p 55432 -U wu35_local -d production_runtime_wu35
```

Si `psql` no está instalado en el host, ejecutar los comandos SQL dentro del contenedor:

```bash
docker exec -i production-runtime-wu35-postgres psql -U wu35_local -d production_runtime_wu35 < infra/migrations/001_initial_tenancy.up.sql
docker exec -i production-runtime-wu35-postgres psql -U wu35_local -d production_runtime_wu35 < infra/migrations/001_initial_tenancy.down.sql
```

Para una prueba de reinicio controlada:

```bash
docker compose -f postgres-work-unit-3-5.compose.yml restart postgres-work-unit-3-5
docker exec -i production-runtime-wu35-postgres psql -U wu35_local -d production_runtime_wu35 -c '\dt'
```

## Escenarios SQL requeridos

Tras aplicar `001_initial_tenancy.up.sql`, ejecutar fixtures A/B y comprobar individualmente:

1. FK inválida: insertar una fila con `client_account_id` inexistente; debe fallar.
2. Membership duplicada: repetir `(agency_id, user_id, client_account_id)`; debe fallar por la unicidad canónica.
3. Agency/ClientAccount inconsistente: insertar membership con `agency_id` distinto al de la cuenta; debe fallar por la FK compuesta.
4. Mover ClientAccount: actualizar `agency_id`; debe fallar por el trigger.
5. Resource sin `client_account_id`: insertar `NULL`; debe fallar por `NOT NULL`.
6. `resource_key` duplicada dentro de una cuenta; debe fallar.
7. Misma `resource_key` en Account A y Account B; debe ser permitida, demostrando unicidad por cuenta, no aislamiento de aplicación.
8. Reiniciar y reconsultar las filas A/B; deben persistir en el volumen local.

Las pruebas deben registrar el código de salida real de cada comando y el mensaje de PostgreSQL.

## Rollback

Con datos de prueba descartables y aprobación operativa explícita:

```bash
docker exec -i production-runtime-wu35-postgres psql -v ON_ERROR_STOP=1 -U wu35_local -d production_runtime_wu35 < infra/migrations/001_initial_tenancy.down.sql
docker exec -i production-runtime-wu35-postgres psql -U wu35_local -d production_runtime_wu35 -c '\dt'
docker compose -f postgres-work-unit-3-5.compose.yml down -v
```

`down` es destructivo. No debe usarse como rollback automático de una aplicación.

## Límites

- Este entorno sólo valida ejecución de migraciones e integridad estructural PostgreSQL.
- No prueba autorización de aplicación, repositories, RLS ni aislamiento real entre requests.
- No prueba autenticación, transacciones de aplicación, audit/outbox runtime ni idempotencia durable.
- No representa infraestructura productiva ni selección de proveedor.
- RPO/RTO y backup/restore permanecen abiertos.
