\set ON_ERROR_STOP on
-- WU5 disposable fixture reset; never touches production_runtime_local.
BEGIN;
TRUNCATE command_idempotency;
UPDATE tenant_resource SET data = '{"value":"A"}'::jsonb WHERE id = '40000000-0000-0000-0000-000000000001';
UPDATE tenant_resource SET data = '{"value":"B"}'::jsonb WHERE id = '40000000-0000-0000-0000-000000000002';
DELETE FROM tenant_resource WHERE resource_key LIKE 'wu5-%';
COMMIT;
