# Work Unit: Propiedades (enriquecimiento del modelo inmobiliario)

## Contexto

El dominio ya tiene: contacts, leads, pipelines, pipeline_stages,
conversations, messages, follow_ups y una tabla `properties` básica
(migración 004: id, client_account_id, demo_seed_key, title,
operation_type, location, bedrooms, price, currency, availability).
Los leads ya tienen `selected_property_id` (nullable FK a properties).

Seguir el mismo patrón arquitectónico validado en WU1–WU6:
tenant-scoped, comandos atómicos con idempotencia, audit/outbox,
read APIs y seed determinista. NO crear una tabla nueva desde cero:
EXTENDER la existente. Usar el baseline de mvp-commercial-demo como
referencia de patrón.

## Objetivo

Hacer de Propiedad una entidad de primera clase, con catálogo rico,
vínculos con rol, intereses livianos (sin crear leads) y estado
comercial propio, para que el agente haga matching real contra un
catálogo consultable y no contra datos sueltos.

## Migración 005 (extender, no tocar 001–004)

1. `properties` — agregar columnas (nullable salvo que se indique):
   - reference_code       varchar, UNIQUE por client_account_id
   - operation_type       (AMPLIAR enum existente: sale | rent | temporary_rent)
   - property_type        enum: apartment | house | ph | land | office | commercial | warehouse
   - address              varchar (calle + altura)
   - floor_unit           varchar
   - neighborhood         varchar
   - city                 varchar
   - latitude, longitude  numeric  (mapa futuro, fuera de alcance)
   - price_amount         numeric  (reemplaza/migra `price`)
   - price_currency       enum: USD | ARS  (reemplaza/migra `currency`)
   - expenses_amount      numeric  (nullable)
   - accepts_swap         boolean default false
   - covered_area_m2      numeric  (nullable)
   - total_area_m2        numeric  (nullable)
   - rooms                int      (nullable)
   - bathrooms            int      (nullable)
   - parking_spaces       int      (nullable)
   - property_age_years   int      (nullable)
   - construction_status  enum: brand_new | used | needs_renovation
   - status               enum (AMPLIAR/reemplazar `availability`):
                          capturing | available | reserved | sold | rented | paused
   - photos               jsonb (array de URLs)
   - video_url            varchar (nullable)
   - capture_source       enum: zone_scan | digital_ads | landing_page
   - captured_at          timestamp
   - responsible_agent_id uuid FK -> users (nullable)
   Mantener `title` y `client_account_id`; migrar `location` ->
   address/neighborhood/city, `price` -> price_amount, `currency` ->
   price_currency. La migración debe ser forward-only y preservar datos
   existentes (o declarar la base demo descartable y re-seed).

2. `property_amenities` (catálogo)
   - id uuid PK, name varchar UNIQUE

3. `property_amenity_rel` (M:N)
   - property_id uuid FK -> properties, amenity_id uuid FK -> property_amenities
   - PK compuesta (property_id, amenity_id)

4. `property_contacts` (asociación con rol)
   - property_id uuid FK, contact_id uuid FK, role enum: owner | tenant | referrer
   - linked_at timestamp
   - client_account_id NOT NULL + FK compuesta para aislamiento (patrón 004)

5. `property_interests` (vínculo liviano, NO crea lead)
   - id uuid PK, property_id uuid FK, contact_id uuid FK
   - origin enum: ai_agent | manual
   - created_at timestamp
   - client_account_id NOT NULL + FK compuesta
   - UNIQUE (property_id, contact_id) para no duplicar el mismo match

6. `leads.selected_property_id` ya existe; NO agregar un campo nuevo.
   El vínculo lead->property se setea solo cuando el interés se
   convierte en visita/negociación.

## Entidades + repositories (PostgreSQL, psycopg, tenant-scoped)

- PropertyRepository (extender el existente): list/filter, get, create, update, change_status
- PropertyAmenityRepository (catálogo)
- PropertyContactRepository (vínculo con rol)
- PropertyInterestRepository (registrar/consultar intereses)
Todas las operaciones exigen TenantContext y predicado client_account_id.

## Comandos atómicos (idempotencia + audit/outbox, patrón WU5)

- CreateProperty
- UpdateProperty
- ChangePropertyStatus (máquina de transiciones: capturing -> available
  -> reserved -> sold/rented; -> paused permitido y reversible)
- LinkContactToProperty (con rol)
- RegisterPropertyInterest (idempotente por property+contact)

## Read APIs (patrón WU6)

- GET /v1/crm/properties        (filtros: status, property_type, rango de precio, city)
- GET /v1/crm/properties/{id}   (detalle + amenities + contactos vinculados + intereses)
- GET /v1/crm/contacts/{id}/properties  (propiedades vinculadas a un contacto)

## Seed demo

- 4 propiedades con estados distintos (capturing, available, reserved,
  sold/rented) y amenities variados, para probar el matching sin
  captación real.
- Actualizar mvp_seed.sql (ownership fail-closed existente) y el reset.

## Fuera de alcance (no implementar)

- Mapa con lat/lng (los campos existen, sin UI).
- Reportes por propiedad (días en mercado, conversión) — depende de volumen real.
- Multi-titularidad (el M:N ya la soporta; sin UI por ahora).
- Kapso/WhatsApp real, LLM, RLS, auth productiva.

## Regresión

- typecheck, npm test, compileall, y los harness Python WU3–WU10 sin regresión.
- Base nueva production_runtime_mvp_wu11; no tocar bases anteriores.

No declarar aceptación; crear work-unit-11-evidence.md.
