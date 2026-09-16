\set ON_ERROR_STOP on
-- Deterministic WU2 seed. It owns only rows carrying the exact demo markers below.
-- Fail-closed ownership: a pre-existing row at a designated ID without the
-- expected marker (NULL or different) aborts the seed with no modifications.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('mvp-commercial-demo-seed-reset'));

-- 1. ClientAccount ownership gate: an existing designated account must already
-- carry the exact expected marker. Never claim or overwrite unmarked/foreign rows.
DO $$
DECLARE
  expected record;
BEGIN
  FOR expected IN
    SELECT * FROM (VALUES
      ('20000000-0000-0000-0000-000000000001'::uuid, 'demo-account-a'::text),
      ('20000000-0000-0000-0000-000000000002'::uuid, 'demo-account-b'::text)
    ) AS v(id, key)
  LOOP
    IF EXISTS (
      SELECT 1 FROM client_accounts
      WHERE id = expected.id AND demo_seed_key IS DISTINCT FROM expected.key
    ) THEN
      RAISE EXCEPTION 'designated client account % exists with an incompatible or missing ownership marker; refusing to claim it', expected.id;
    END IF;
  END LOOP;
END $$;

-- 2. CRM entity ownership gate: any row at a designated seed ID (or holding a
-- designated marker) must already belong to the expected ClientAccount and
-- carry the exact marker. Otherwise abort before deleting or inserting anything.
DO $$
DECLARE
  expected record;
  found_account uuid;
  found_key text;
  marker_owner uuid;
BEGIN
  FOR expected IN
    SELECT * FROM (VALUES
      ('contacts'::text,          '61000000-0000-0000-0000-000000000001'::uuid, '20000000-0000-0000-0000-000000000001'::uuid, 'demo-a-contact-001'::text),
      ('contacts',                '61000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-contact-001'),
      ('properties',              '62000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-property-001'),
      ('properties',              '62000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-property-001'),
      ('pipelines',               '63000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-pipeline-001'),
      ('pipelines',               '63000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-pipeline-001'),
      ('pipeline_stages',         '64000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-stage-001'),
      ('pipeline_stages',         '64000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-stage-001'),
      ('leads',                   '65000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-lead-001'),
      ('leads',                   '65000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-lead-001'),
      ('conversations',           '66000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-conversation-001'),
      ('conversations',           '66000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-conversation-001'),
      ('messages',                '67000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-message-001'),
      ('messages',                '67000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-message-001'),
      ('follow_ups',              '68000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-follow-up-001'),
      ('follow_ups',              '68000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-follow-up-001')
    ) AS v(tbl, id, account, key)
  LOOP
    EXECUTE format('SELECT client_account_id, demo_seed_key FROM %I WHERE id = $1', expected.tbl)
      INTO found_account, found_key USING expected.id;
    IF found_account IS NOT NULL
       AND (found_account <> expected.account OR found_key IS DISTINCT FROM expected.key) THEN
      RAISE EXCEPTION 'seed row % in % conflicts with an existing row at id %; refusing to claim it',
        expected.key, expected.tbl, expected.id;
    END IF;
    EXECUTE format('SELECT id FROM %I WHERE demo_seed_key = $1 AND (id <> $2 OR client_account_id <> $3) LIMIT 1', expected.tbl)
      INTO marker_owner USING expected.key, expected.id, expected.account;
    IF marker_owner IS NOT NULL THEN
      RAISE EXCEPTION 'marker % in % is already held by row %; refusing to seed',
        expected.key, expected.tbl, marker_owner;
    END IF;
  END LOOP;
END $$;

INSERT INTO agency (id, name) VALUES
  ('00000000-0000-0000-0000-000000000001', 'Agencia A')
ON CONFLICT (id) DO NOTHING;
INSERT INTO users (id) VALUES ('10000000-0000-0000-0000-000000000001')
ON CONFLICT (id) DO NOTHING;

-- 3. Create designated accounts only when absent (or already marker-matching,
-- verified above); never overwrite an unmarked account.
INSERT INTO client_accounts (id, agency_id, name, demo_seed_key) VALUES
  ('20000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Aurea Inmobiliaria Norte', 'demo-account-a'),
  ('20000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Litoral Inmobiliaria Sur', 'demo-account-b')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;

INSERT INTO memberships (id, agency_id, client_account_id, user_id, roles, permissions) VALUES
  ('30000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001', '{member}', '{client_account:read,client_account:write}'),
  ('30000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001', '{member}', '{client_account:read,client_account:write}')
ON CONFLICT (id) DO NOTHING;

-- 4. Converge by replacing only previously seeded demo rows, keyed on
-- demo_seed_key + client_account_id, inside the same transaction.
DELETE FROM messages WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-message-001'), ('20000000-0000-0000-0000-000000000002', 'demo-b-message-001'));
DELETE FROM conversations WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-conversation-001'), ('20000000-0000-0000-0000-000000000002', 'demo-b-conversation-001'));
DELETE FROM follow_ups WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-follow-up-001'), ('20000000-0000-0000-0000-000000000002', 'demo-b-follow-up-001'));
DELETE FROM leads WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-lead-001'), ('20000000-0000-0000-0000-000000000002', 'demo-b-lead-001'));
DELETE FROM pipeline_stages WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-stage-001'), ('20000000-0000-0000-0000-000000000002', 'demo-b-stage-001'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-stage-calificado'), ('20000000-0000-0000-0000-000000000002', 'demo-b-stage-calificado'));
DELETE FROM pipelines WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-pipeline-001'), ('20000000-0000-0000-0000-000000000002', 'demo-b-pipeline-001'));
DELETE FROM properties WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-property-001'), ('20000000-0000-0000-0000-000000000002', 'demo-b-property-001'));
DELETE FROM contacts WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-contact-001'), ('20000000-0000-0000-0000-000000000002', 'demo-b-contact-001'));

INSERT INTO contacts (id, client_account_id, demo_seed_key, name, email, phone, tags) VALUES
  ('61000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-contact-001', 'Camila Torres', 'camila@aurea.example', '+54-11-4000-1001', '{comprador}'),
  ('61000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-contact-001', 'Bruno Salvatierra', 'bruno@litoral.example', '+54-341-400-2002', '{inversor}');
INSERT INTO properties (id, client_account_id, demo_seed_key, title, operation_type, location, bedrooms, price, currency) VALUES
  ('62000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-property-001', 'Loft con jardín en Palermo', 'sale', 'Palermo, Buenos Aires', 2, 185000, 'USD'),
  ('62000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-property-001', 'Casa familiar en Fisherton', 'rent', 'Fisherton, Rosario', 4, 1200, 'USD');
INSERT INTO pipelines (id, client_account_id, demo_seed_key, name) VALUES
  ('63000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-pipeline-001', 'Ventas Aurea'),
  ('63000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-pipeline-001', 'Alquileres Litoral');
INSERT INTO pipeline_stages (id, client_account_id, pipeline_id, demo_seed_key, name, position) VALUES
  ('64000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', '63000000-0000-0000-0000-000000000001', 'demo-a-stage-001', 'Nueva consulta', 0),
  ('64000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', '63000000-0000-0000-0000-000000000002', 'demo-b-stage-001', 'Visita agendada', 0);
INSERT INTO leads (id, client_account_id, demo_seed_key, contact_id, selected_property_id, pipeline_id, stage_id, status, source) VALUES
  ('65000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-lead-001', '61000000-0000-0000-0000-000000000001', '62000000-0000-0000-0000-000000000001', '63000000-0000-0000-0000-000000000001', '64000000-0000-0000-0000-000000000001', 'new', 'sitio web'),
  ('65000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-lead-001', '61000000-0000-0000-0000-000000000002', '62000000-0000-0000-0000-000000000002', '63000000-0000-0000-0000-000000000002', '64000000-0000-0000-0000-000000000002', 'qualified', 'referido');
INSERT INTO conversations (id, client_account_id, demo_seed_key, contact_id, unread_count) VALUES
  ('66000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-conversation-001', '61000000-0000-0000-0000-000000000001', 1),
  ('66000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-conversation-001', '61000000-0000-0000-0000-000000000002', 0);
INSERT INTO messages (id, client_account_id, conversation_id, demo_seed_key, direction, body, external_event_id, created_at) VALUES
  ('67000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', '66000000-0000-0000-0000-000000000001', 'demo-a-message-001', 'inbound', 'Me gustaría recibir detalles del loft de Palermo.', 'demo-a-event-001', '2026-08-30T12:00:00Z'),
  ('67000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', '66000000-0000-0000-0000-000000000002', 'demo-b-message-001', 'outbound', 'La casa de Fisherton está disponible para una visita.', 'demo-b-event-001', '2026-08-30T12:05:00Z');
INSERT INTO follow_ups (id, client_account_id, demo_seed_key, lead_id, contact_id, title, due_at) VALUES
  ('68000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-follow-up-001', '65000000-0000-0000-0000-000000000001', '61000000-0000-0000-0000-000000000001', 'Enviar folleto de Palermo', '2026-09-01T15:00:00Z'),
  ('68000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-follow-up-001', '65000000-0000-0000-0000-000000000002', '61000000-0000-0000-0000-000000000002', 'Confirmar visita a la casa', '2026-09-02T16:00:00Z');
COMMIT;
