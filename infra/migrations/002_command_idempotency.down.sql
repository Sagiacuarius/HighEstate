-- 002_command_idempotency.down.sql
-- Destructive operator-reviewed rollback; not application rollback.
BEGIN;
DROP TABLE IF EXISTS command_idempotency;
COMMIT;
