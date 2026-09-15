\set ON_ERROR_STOP on
-- Work Unit 4 fixture data (idempotent). Adds users with explicit permissions
-- in addition to the WU3.5 demo seed, for real isolation testing.
BEGIN;

DELETE FROM tenant_resource WHERE id IN (
  '41000000-0000-0000-0000-000000000101',
  '41000000-0000-0000-0000-000000000102',
  '50000000-0000-0000-0000-0000000000ff'
);

INSERT INTO users (id) VALUES
  ('11000000-0000-0000-0000-000000000001'),  -- U1: member of A only
  ('12000000-0000-0000-0000-000000000002'),  -- U2: member of A and B
  ('10000000-0000-0000-0000-000000000003')   -- U3: no memberships
ON CONFLICT DO NOTHING;

INSERT INTO client_accounts (id, agency_id, name) VALUES
  ('20000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002', 'Account OtherAgency')
ON CONFLICT DO NOTHING;

INSERT INTO memberships (id, agency_id, client_account_id, user_id, roles, permissions) VALUES
  ('31000000-0000-0000-0000-000000000101', '00000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', '11000000-0000-0000-0000-000000000001', '{member}', '{client_account:read,client_account:write}'),
  ('31000000-0000-0000-0000-000000000102', '00000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000002', '12000000-0000-0000-0000-000000000002', '{member}', '{client_account:read,client_account:write}'),
  ('31000000-0000-0000-0000-000000000103', '00000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', '12000000-0000-0000-0000-000000000002', '{member}', '{client_account:read,client_account:write}')
ON CONFLICT (agency_id, user_id, client_account_id) DO UPDATE
SET permissions = EXCLUDED.permissions, roles = EXCLUDED.roles;

-- A resource belonging to Account B, used as the "foreign" target for cross attempts.
INSERT INTO tenant_resource (id, client_account_id, resource_key, data) VALUES
  ('41000000-0000-0000-0000-000000000101', '20000000-0000-0000-0000-000000000002', 'secret-b', '{"value": "must-not-leak"}')
ON CONFLICT (client_account_id, resource_key) DO NOTHING;

INSERT INTO tenant_resource (id, client_account_id, resource_key, data) VALUES
  ('41000000-0000-0000-0000-000000000102', '20000000-0000-0000-0000-000000000001', 'own-a', '{"value": "a-owned"}')
ON CONFLICT (client_account_id, resource_key) DO NOTHING;

COMMIT;

\echo '--- wu4 fixtures ready ---'
SELECT 'memberships' AS kind, user_id::text || ':' || client_account_id::text AS key FROM memberships WHERE id::text LIKE '3100%'
UNION ALL
SELECT 'resources', resource_key || '@' || client_account_id FROM tenant_resource WHERE id::text LIKE '4100%';
