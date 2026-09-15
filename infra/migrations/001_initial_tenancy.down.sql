-- 001_initial_tenancy.down.sql
-- Destructive rollback: operator-reviewed only; not an application rollback.
-- Drop in reverse dependency order.

BEGIN;

DROP TABLE IF EXISTS tenant_resource;
DROP TABLE IF EXISTS memberships;
DROP TRIGGER IF EXISTS client_accounts_agency_immutable ON client_accounts;
DROP FUNCTION IF EXISTS prevent_client_account_agency_change();
DROP TABLE IF EXISTS client_accounts;
DROP TABLE IF EXISTS identity_links;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS agency;

COMMIT;
