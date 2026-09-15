-- 003_audit_outbox.down.sql
-- Destructive, operator-reviewed rollback; never automatic application rollback.
BEGIN;
DROP TABLE IF EXISTS outbox_events;
DROP TABLE IF EXISTS audit_records;
COMMIT;
