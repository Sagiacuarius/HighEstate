\set ON_ERROR_STOP on
-- Work Unit 3.5 real validation: apply migration + seed A/B demo data (repeatable)
\i infra/migrations/001_initial_tenancy.up.sql

-- Seed only if not already present (canonical demo identifiers)
BEGIN;
INSERT INTO agency (id, name) VALUES
  ('00000000-0000-0000-0000-000000000001', 'Agency A'),
  ('00000000-0000-0000-0000-000000000002', 'Agency B')
ON CONFLICT DO NOTHING;

INSERT INTO users (id) VALUES
  ('10000000-0000-0000-0000-000000000001'),
  ('10000000-0000-0000-0000-000000000002')
ON CONFLICT DO NOTHING;

INSERT INTO identity_links (id, user_id, provider, external_subject) VALUES
  ('15000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001', 'test-provider', 'subject-1'),
  ('15000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000002', 'test-provider', 'subject-2')
ON CONFLICT DO NOTHING;

INSERT INTO client_accounts (id, agency_id, name) VALUES
  ('20000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Account A'),
  ('20000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Account B'),
  ('20000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002', 'Account OtherAgency')
ON CONFLICT DO NOTHING;

INSERT INTO memberships (id, agency_id, client_account_id, user_id, roles, permissions) VALUES
  ('30000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001', '{member}', '{client_account:read}'),
  ('30000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001', '{member}', '{client_account:read}')
ON CONFLICT DO NOTHING;

INSERT INTO tenant_resource (id, client_account_id, resource_key, data) VALUES
  ('40000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a', '{"value": "A"}'),
  ('40000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b', '{"value": "B"}')
ON CONFLICT DO NOTHING;
COMMIT;

\echo '--- tables ---'
\dt
\echo '--- agencies ---'
SELECT id, name FROM agency ORDER BY id;
\echo '--- accounts ---'
SELECT id, agency_id, name FROM client_accounts ORDER BY id;
\echo '--- memberships ---'
SELECT id, agency_id, client_account_id, user_id FROM memberships ORDER BY id;
\echo '--- tenant resources ---'
SELECT id, client_account_id, resource_key FROM tenant_resource ORDER BY id;
