\set ON_ERROR_STOP on
-- Property Catalog seed (change: property-catalog). Requires migration 005.
-- Extends the accepted mvp_seed.sql baseline: same fail-closed ownership rules
-- (designated ids + demo_* markers, advisory lock, single transaction). It only
-- enriches rows it designates and never claims foreign/unmarked rows.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('mvp-commercial-demo-seed-reset'));

-- Ownership gate for the rows this seed designates.
DO $$
DECLARE
  expected record;
  found_account uuid;
  found_key text;
  marker_owner uuid;
BEGIN
  FOR expected IN
    SELECT * FROM (VALUES
      ('properties',                '62000000-0000-0000-0000-000000000003'::uuid, '20000000-0000-0000-0000-000000000001'::uuid, 'demo-a-property-002'::text),
      ('properties',                '62000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000002', 'demo-b-property-002'),
      ('property_amenity_rel',      '00000000-0000-0000-0000-000000000000'::uuid, '20000000-0000-0000-0000-000000000001', 'demo-a-amenity-001'),
      ('property_amenity_rel',      '00000000-0000-0000-0000-000000000000', '20000000-0000-0000-0000-000000000001', 'demo-a-amenity-002'),
      ('property_amenity_rel',      '00000000-0000-0000-0000-000000000000', '20000000-0000-0000-0000-000000000002', 'demo-b-amenity-001'),
      ('property_amenity_rel',      '00000000-0000-0000-0000-000000000000', '20000000-0000-0000-0000-000000000002', 'demo-b-amenity-002'),
      ('property_contacts',         '00000000-0000-0000-0000-000000000000', '20000000-0000-0000-0000-000000000001', 'demo-a-property-contact-001'),
      ('property_contacts',         '00000000-0000-0000-0000-000000000000', '20000000-0000-0000-0000-000000000001', 'demo-a-property-contact-002'),
      ('property_contacts',         '00000000-0000-0000-0000-000000000000', '20000000-0000-0000-0000-000000000002', 'demo-b-property-contact-001'),
      ('property_interests',        '6b000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-interest-001'),
      ('property_interests',        '6b000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-interest-001')
    ) AS v(tbl, id, account, key)
  LOOP
    IF expected.tbl IN ('property_amenity_rel','property_contacts') THEN
      -- composite-PK tables have no id column: ownership is (marker, account)
      IF EXISTS (SELECT 1 FROM property_amenity_rel
                  WHERE demo_seed_key = expected.key AND client_account_id <> expected.account)
         OR EXISTS (SELECT 1 FROM property_contacts
                     WHERE demo_seed_key = expected.key AND client_account_id <> expected.account) THEN
        RAISE EXCEPTION 'marker % is already held outside account %; refusing to seed',
          expected.key, expected.account;
      END IF;
      CONTINUE;
    END IF;
    IF expected.id <> '00000000-0000-0000-0000-000000000000'::uuid THEN
      EXECUTE format('SELECT client_account_id, demo_seed_key FROM %I WHERE id = $1', expected.tbl)
        INTO found_account, found_key USING expected.id;
      IF found_account IS NOT NULL
         AND (found_account <> expected.account OR found_key IS DISTINCT FROM expected.key) THEN
        RAISE EXCEPTION 'seed row % in % conflicts with an existing row at id %; refusing to claim it',
          expected.key, expected.tbl, expected.id;
      END IF;
    END IF;
    EXECUTE format('SELECT id FROM %I WHERE demo_seed_key = $1 AND client_account_id <> $3 LIMIT 1', expected.tbl)
      INTO marker_owner USING expected.key, expected.id, expected.account;
    IF marker_owner IS NOT NULL THEN
      RAISE EXCEPTION 'marker % in % is already held by row %; refusing to seed',
        expected.key, expected.tbl, marker_owner;
    END IF;
  END LOOP;
END $$;

-- Amenity vocabulary (shared catalogue, idempotent by name-unique + stable ids).
INSERT INTO property_amenities (id, name) VALUES
  ('6a000000-0000-0000-0000-000000000001', 'Cochera'),
  ('6a000000-0000-0000-0000-000000000002', 'Balcón'),
  ('6a000000-0000-0000-0000-000000000003', 'Patio'),
  ('6a000000-0000-0000-0000-000000000004', 'Piscina'),
  ('6a000000-0000-0000-0000-000000000005', 'Seguridad 24h'),
  ('6a000000-0000-0000-0000-000000000006', 'Apto mascotas')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;

-- Converge: remove only rows this seed designates (marker + account). Children
-- are cleared in RESTRICT-FK order; an unmarked command-created child of a
-- designated row blocks re-apply fail-closed exactly like the accepted
-- mvp_reset (tested in the WU11 harness), and is never deleted silently.
DELETE FROM property_amenity_rel WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-amenity-001'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-amenity-002'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-amenity-001'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-amenity-002'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-amenity-003'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-amenity-003'));
DELETE FROM property_contacts WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-property-contact-001'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-property-contact-002'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-property-contact-001'));
DELETE FROM property_interests WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-interest-001'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-interest-001'));
DELETE FROM properties WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-property-002'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-property-002'));

-- Enrich the two accepted seed properties (marker-gated UPDATE; legacy columns
-- stay consistent mirrors, see evidence).
UPDATE properties SET
  reference_code = 'AUR-PAL-001', property_type = 'apartment',
  address = 'Thames 1500', floor_unit = '3 B', neighborhood = 'Palermo',
  city = 'Buenos Aires', price_currency = currency, price_amount = price,
  status = 'available', expenses_amount = 45000, accepts_swap = false,
  covered_area_m2 = 78, total_area_m2 = 92, rooms = 3, bathrooms = 2,
  parking_spaces = 1, property_age_years = 12, construction_status = 'used',
  photos = '["https://demo.invalid/palermo-loft-1.jpg"]'::jsonb,
  video_url = NULL, capture_source = 'landing_page',
  captured_at = '2026-08-30T10:00:00Z',
  responsible_agent_id = '10000000-0000-0000-0000-000000000001'
WHERE client_account_id = '20000000-0000-0000-0000-000000000001'
  AND demo_seed_key = 'demo-a-property-001';
UPDATE properties SET
  reference_code = 'LIT-FIS-001', property_type = 'house',
  address = 'Av. Pellegrini 2200', floor_unit = NULL, neighborhood = 'Fisherton',
  city = 'Rosario', price_currency = currency, price_amount = price,
  status = 'reserved', expenses_amount = NULL, accepts_swap = true,
  covered_area_m2 = 210, total_area_m2 = 340, rooms = 5, bathrooms = 3,
  parking_spaces = 2, property_age_years = 25, construction_status = 'used',
  photos = '["https://demo.invalid/fisherton-house-1.jpg"]'::jsonb,
  video_url = NULL, capture_source = 'zone_scan',
  captured_at = '2026-08-30T10:00:00Z',
  responsible_agent_id = '10000000-0000-0000-0000-000000000001'
WHERE client_account_id = '20000000-0000-0000-0000-000000000002'
  AND demo_seed_key = 'demo-b-property-001';

-- Two new designated properties: statuses capturing (A) and sold (B).
-- Four properties total cover capturing|available|reserved|sold (brief).
INSERT INTO properties
  (id, client_account_id, demo_seed_key, title, operation_type, location, bedrooms,
   price, currency, availability, reference_code, property_type, address,
   neighborhood, city, price_amount, price_currency, status, accepts_swap,
   rooms, bathrooms, parking_spaces, covered_area_m2, total_area_m2,
   construction_status, photos, capture_source, captured_at, responsible_agent_id)
VALUES
  ('62000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001',
   'demo-a-property-002', 'PH con patio en Belgrano', 'sale',
   'Superí 1200, Belgrano, Buenos Aires', 3, 210000, 'USD', 'unavailable',
   'AUR-BEL-002', 'ph', 'Superí 1200', 'Belgrano', 'Buenos Aires',
   210000, 'USD', 'capturing', false, 3, 2, 0, 65, 88, 'needs_renovation',
   '[]'::jsonb, 'zone_scan', '2026-09-01T09:00:00Z',
   '10000000-0000-0000-0000-000000000001'),
  ('62000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000002',
   'demo-b-property-002', 'Departamento nuevo en Centro', 'rent',
   'Córdoba 900, Rosario', 2, 480000, 'ARS', 'unavailable',
   'LIT-CEN-002', 'apartment', 'Córdoba 900', 'Centro', 'Rosario',
   480000, 'ARS', 'sold', false, 2, 1, 1, 58, 62, 'brand_new',
   '["https://demo.invalid/centro-dept-1.jpg","https://demo.invalid/centro-dept-2.jpg"]'::jsonb,
   'digital_ads', '2026-09-02T09:00:00Z',
   '10000000-0000-0000-0000-000000000001');

-- legacy 'sold' has no availability equivalent; keep unavailable mirror.
UPDATE properties SET availability = 'unavailable'
 WHERE demo_seed_key IN ('demo-a-property-002', 'demo-b-property-002');

-- Amenity links
INSERT INTO property_amenity_rel (property_id, amenity_id, client_account_id, demo_seed_key) VALUES
  ('62000000-0000-0000-0000-000000000001', '6a000000-0000-0000-0000-000000000002',
   '20000000-0000-0000-0000-000000000001', 'demo-a-amenity-001'),
  ('62000000-0000-0000-0000-000000000001', '6a000000-0000-0000-0000-000000000006',
   '20000000-0000-0000-0000-000000000001', 'demo-a-amenity-002'),
  ('62000000-0000-0000-0000-000000000003', '6a000000-0000-0000-0000-000000000003',
   '20000000-0000-0000-0000-000000000001', 'demo-a-amenity-003'),
  ('62000000-0000-0000-0000-000000000002', '6a000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000002', 'demo-b-amenity-001'),
  ('62000000-0000-0000-0000-000000000002', '6a000000-0000-0000-0000-000000000004',
   '20000000-0000-0000-0000-000000000002', 'demo-b-amenity-002'),
  ('62000000-0000-0000-0000-000000000004', '6a000000-0000-0000-0000-000000000005',
   '20000000-0000-0000-0000-000000000002', 'demo-b-amenity-003');

-- Contact links with role (owner for the two designated seed contacts)
INSERT INTO property_contacts (property_id, contact_id, role, client_account_id, demo_seed_key) VALUES
  ('62000000-0000-0000-0000-000000000001', '61000000-0000-0000-0000-000000000001',
   'owner', '20000000-0000-0000-0000-000000000001', 'demo-a-property-contact-001'),
  ('62000000-0000-0000-0000-000000000003', '61000000-0000-0000-0000-000000000001',
   'referrer', '20000000-0000-0000-0000-000000000001', 'demo-a-property-contact-002'),
  ('62000000-0000-0000-0000-000000000002', '61000000-0000-0000-0000-000000000002',
   'owner', '20000000-0000-0000-0000-000000000002', 'demo-b-property-contact-001');

-- Interests: lightweight markers only; they never create leads.
INSERT INTO property_interests (id, property_id, contact_id, origin, client_account_id, demo_seed_key) VALUES
  ('6b000000-0000-0000-0000-000000000001', '62000000-0000-0000-0000-000000000003',
   '61000000-0000-0000-0000-000000000001', 'ai_agent',
   '20000000-0000-0000-0000-000000000001', 'demo-a-interest-001'),
  ('6b000000-0000-0000-0000-000000000002', '62000000-0000-0000-0000-000000000004',
   '61000000-0000-0000-0000-000000000002', 'manual',
   '20000000-0000-0000-0000-000000000002', 'demo-b-interest-001');

COMMIT;
