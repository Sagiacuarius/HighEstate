-- 005_property_catalog.up.sql
-- Property Catalog (change: property-catalog). EXTENDS the 004 properties table;
-- never edits 001-004. Forward-only and DATA-PRESERVING: legacy columns
-- (location/bedrooms/price/currency/availability) are KEPT so the accepted
-- WU3/WU5/WU6 code paths keep working; the richer columns replace them for
-- new writes (drop of legacy columns is deferred, documented in evidence).

-- 1. reference code: one per account; NULLS NOT DISTINCT (PG18) so rows without
--    code stay legal side by side.
ALTER TABLE properties ADD COLUMN reference_code varchar(64);
ALTER TABLE properties ADD CONSTRAINT properties_account_reference_unique
    UNIQUE NULLS NOT DISTINCT (client_account_id, reference_code);

-- 2. operation_type: widen the accepted CHECK (sale|rent) with temporary_rent.
ALTER TABLE properties DROP CONSTRAINT properties_operation_type_check;
ALTER TABLE properties ADD CONSTRAINT properties_operation_type_check
    CHECK (operation_type IN ('sale', 'rent', 'temporary_rent'));

-- 3. catalogue fields (nullable unless noted; CHECK enums inline).
ALTER TABLE properties ADD COLUMN property_type text
    CHECK (property_type IN ('apartment','house','ph','land','office','commercial','warehouse'));
ALTER TABLE properties ADD COLUMN address varchar(200);
ALTER TABLE properties ADD COLUMN floor_unit varchar(40);
ALTER TABLE properties ADD COLUMN neighborhood varchar(120);
ALTER TABLE properties ADD COLUMN city varchar(120);
-- future map support: fields only, no behaviour (out of scope per brief)
ALTER TABLE properties ADD COLUMN latitude numeric(9,6);
ALTER TABLE properties ADD COLUMN longitude numeric(9,6);
ALTER TABLE properties ADD COLUMN expenses_amount numeric(14,2) CHECK (expenses_amount >= 0);
ALTER TABLE properties ADD COLUMN accepts_swap boolean NOT NULL DEFAULT false;
ALTER TABLE properties ADD COLUMN covered_area_m2 numeric(10,2) CHECK (covered_area_m2 >= 0);
ALTER TABLE properties ADD COLUMN total_area_m2 numeric(10,2) CHECK (total_area_m2 >= 0);
ALTER TABLE properties ADD COLUMN rooms integer CHECK (rooms >= 0);
ALTER TABLE properties ADD COLUMN bathrooms integer CHECK (bathrooms >= 0);
ALTER TABLE properties ADD COLUMN parking_spaces integer CHECK (parking_spaces >= 0);
ALTER TABLE properties ADD COLUMN property_age_years integer CHECK (property_age_years >= 0);
ALTER TABLE properties ADD COLUMN construction_status text
    CHECK (construction_status IN ('brand_new','used','needs_renovation'));
ALTER TABLE properties ADD COLUMN photos jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(photos) = 'array');
ALTER TABLE properties ADD COLUMN video_url varchar(400);
ALTER TABLE properties ADD COLUMN capture_source text
    CHECK (capture_source IN ('zone_scan','digital_ads','landing_page'));
ALTER TABLE properties ADD COLUMN captured_at timestamptz;
-- responsible agent references the 001 foundation users table; membership-based
-- authorization of the target agent is enforced in the command layer, not by FK.
ALTER TABLE properties ADD COLUMN responsible_agent_id uuid;
ALTER TABLE properties ADD CONSTRAINT properties_responsible_agent_fk
    FOREIGN KEY (responsible_agent_id) REFERENCES users (id) ON DELETE RESTRICT;

-- 4. commercial status + price/currency replacement. Add NULLABLE (brief: nullable
--    unless stated) and backfill existing rows (data-preserving). The accepted WU3
--    repository and legacy seed do not write these columns; consistency for NEW
--    data is owned by the WU11 command layer (documented), no silent defaults.
--    Legacy 'unavailable' maps to 'paused' (documented mapping; no direct equivalent).
ALTER TABLE properties ADD COLUMN status text
    CHECK (status IN ('capturing','available','reserved','sold','rented','paused'));
ALTER TABLE properties ADD COLUMN price_amount numeric(14,2) CHECK (price_amount >= 0);
ALTER TABLE properties ADD COLUMN price_currency text CHECK (price_currency IN ('USD','ARS'));

UPDATE properties SET
    status = CASE availability
        WHEN 'available' THEN 'available'
        WHEN 'reserved'  THEN 'reserved'
        ELSE 'paused' END,
    price_amount = price,
    price_currency = CASE WHEN currency IN ('USD','ARS') THEN currency ELSE 'USD' END,
    address = location,
    rooms = bedrooms
WHERE status IS NULL;

CREATE INDEX properties_account_status_idx ON properties (client_account_id, status, created_at, id);
CREATE INDEX properties_account_city_idx ON properties (client_account_id, city, status);
CREATE INDEX properties_account_price_idx ON properties (client_account_id, price_amount);

-- 5. amenity catalogue (global, not tenant-scoped: it is a shared vocabulary)
CREATE TABLE property_amenities (
    id uuid PRIMARY KEY,
    name varchar(80) NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- 6. M:N property<->amenity. client_account_id + composite FKs replicate the
--    004 isolation pattern: the mandatory predicate is index-backed.
CREATE TABLE property_amenity_rel (
    property_id uuid NOT NULL,
    amenity_id uuid NOT NULL REFERENCES property_amenities (id) ON DELETE RESTRICT,
    client_account_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    demo_seed_key text,
    PRIMARY KEY (property_id, amenity_id),
    CONSTRAINT property_amenity_rel_demo_seed_key_unique UNIQUE (demo_seed_key),
    CONSTRAINT property_amenity_rel_property_account_fk FOREIGN KEY (property_id, client_account_id)
        REFERENCES properties (id, client_account_id) ON DELETE RESTRICT
);
CREATE INDEX property_amenity_rel_account_idx ON property_amenity_rel (client_account_id, property_id);

-- 7. contact links with role (multi-titleholdership supported in data, no UI)
CREATE TABLE property_contacts (
    property_id uuid NOT NULL,
    contact_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('owner','tenant','referrer')),
    client_account_id uuid NOT NULL,
    linked_at timestamptz NOT NULL DEFAULT now(),
    demo_seed_key text,
    PRIMARY KEY (property_id, contact_id, role),
    CONSTRAINT property_contacts_demo_seed_key_unique UNIQUE (demo_seed_key),
    CONSTRAINT property_contacts_property_account_fk FOREIGN KEY (property_id, client_account_id)
        REFERENCES properties (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT property_contacts_contact_account_fk FOREIGN KEY (contact_id, client_account_id)
        REFERENCES contacts (id, client_account_id) ON DELETE RESTRICT
);
CREATE INDEX property_contacts_account_contact_idx ON property_contacts (client_account_id, contact_id);

-- 8. lightweight interest: NEVER creates a lead. The lead->property link is set
--    only when an interest converts into a visit/negotiation (documented).
CREATE TABLE property_interests (
    id uuid PRIMARY KEY,
    property_id uuid NOT NULL,
    contact_id uuid NOT NULL,
    origin text NOT NULL CHECK (origin IN ('ai_agent','manual')),
    client_account_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    demo_seed_key text,
    CONSTRAINT property_interests_demo_seed_key_unique UNIQUE (demo_seed_key),
    CONSTRAINT property_interests_property_contact_unique UNIQUE (property_id, contact_id),
    CONSTRAINT property_interests_property_account_fk FOREIGN KEY (property_id, client_account_id)
        REFERENCES properties (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT property_interests_contact_account_fk FOREIGN KEY (contact_id, client_account_id)
        REFERENCES contacts (id, client_account_id) ON DELETE RESTRICT
);
CREATE INDEX property_interests_account_idx ON property_interests (client_account_id, created_at, id);
CREATE INDEX property_interests_account_property_idx ON property_interests (client_account_id, property_id);
