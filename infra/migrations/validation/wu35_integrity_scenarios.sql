\set ON_ERROR_STOP off
-- WU3.5 integrity scenarios. Every attempt runs inside an explicit ROLLBACK
-- transaction so no invalid data can persist.
\echo '===== S2: tenant_resource with non-existent client_account_id (expect FK failure) ====='
BEGIN;
INSERT INTO tenant_resource (id, client_account_id, resource_key)
VALUES ('ffffffff-0000-0000-0000-000000000001', 'deadbeef-0000-0000-0000-000000000000', 'ghost');
ROLLBACK;

\echo '===== S3: duplicate canonical membership agency+user+account (expect unique violation) ====='
BEGIN;
INSERT INTO memberships (id, agency_id, client_account_id, user_id)
VALUES ('ffffffff-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000001',
        '20000000-0000-0000-0000-000000000001',
        '10000000-0000-0000-0000-000000000001');
ROLLBACK;

\echo '===== S4: membership with agency_id inconsistent with account (expect composite FK failure) ====='
BEGIN;
INSERT INTO memberships (id, agency_id, client_account_id, user_id)
VALUES ('ffffffff-0000-0000-0000-000000000003',
        '00000000-0000-0000-0000-000000000002',
        '20000000-0000-0000-0000-000000000001',
        '10000000-0000-0000-0000-000000000001');
ROLLBACK;

\echo '===== S5: attempt to move ClientAccount to another Agency (expect trigger failure) ====='
BEGIN;
UPDATE client_accounts SET agency_id = '00000000-0000-0000-0000-000000000002'
WHERE id = '20000000-0000-0000-0000-000000000001';
ROLLBACK;

\echo '===== S6: tenant_resource with NULL client_account_id (expect NOT NULL failure) ====='
BEGIN;
INSERT INTO tenant_resource (id, client_account_id, resource_key)
VALUES ('ffffffff-0000-0000-0000-000000000004', NULL, 'unscoped');
ROLLBACK;

\echo '===== S7a: duplicate resource_key within same account (expect unique violation) ====='
BEGIN;
INSERT INTO tenant_resource (id, client_account_id, resource_key)
VALUES ('ffffffff-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000001', 'demo-a');
ROLLBACK;

\echo '===== S7b: same resource_key across Account A and Account B (expect success) ====='
BEGIN;
INSERT INTO tenant_resource (id, client_account_id, resource_key)
VALUES ('ffffffff-0000-0000-0000-000000000006', '20000000-0000-0000-0000-000000000002', 'demo-a');
SELECT 'S7B-INSERTED', count(*) FROM tenant_resource WHERE resource_key = 'demo-a';
ROLLBACK;

\echo '===== Post-scenario contamination check (expect no ffff rows, account unchanged, no move) ====='
SELECT 'contaminated_rows', count(*) FROM tenant_resource WHERE id::text LIKE 'ffffffff%'
UNION ALL
SELECT 'second_memberships', (SELECT count(*) FROM memberships WHERE client_account_id='20000000-0000-0000-0000-000000000001')
UNION ALL
SELECT 'account_a_agency', (SELECT agency_id::text FROM client_accounts WHERE id='20000000-0000-0000-0000-000000000001')
UNION ALL
SELECT 'demo_resources', (SELECT count(*) FROM tenant_resource WHERE resource_key IN ('demo-a','demo-b'));
