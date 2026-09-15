\set ON_ERROR_STOP off
\echo '=== S1 Lead/Contact cross-account (expect FK failure) ==='
BEGIN;
INSERT INTO leads (id, client_account_id, contact_id, pipeline_id, stage_id, status, source)
VALUES ('65000000-0000-0000-0000-000000000101', '20000000-0000-0000-0000-000000000001', '61000000-0000-0000-0000-000000000002', '63000000-0000-0000-0000-000000000001', '64000000-0000-0000-0000-000000000001', 'new', 'test');
ROLLBACK;

\echo '=== S2 Lead/Property cross-account (expect FK failure) ==='
BEGIN;
INSERT INTO leads (id, client_account_id, contact_id, selected_property_id, pipeline_id, stage_id, status, source)
VALUES ('65000000-0000-0000-0000-000000000102', '20000000-0000-0000-0000-000000000001', '61000000-0000-0000-0000-000000000001', '62000000-0000-0000-0000-000000000002', '63000000-0000-0000-0000-000000000001', '64000000-0000-0000-0000-000000000001', 'new', 'test');
ROLLBACK;

\echo '=== S3 Lead/Stage/Pipeline inconsistent (expect FK failure) ==='
BEGIN;
INSERT INTO leads (id, client_account_id, contact_id, pipeline_id, stage_id, status, source)
VALUES ('65000000-0000-0000-0000-000000000103', '20000000-0000-0000-0000-000000000001', '61000000-0000-0000-0000-000000000001', '63000000-0000-0000-0000-000000000001', '64000000-0000-0000-0000-000000000002', 'new', 'test');
ROLLBACK;

\echo '=== S4 Conversation/Contact cross-account (expect FK failure) ==='
BEGIN;
INSERT INTO conversations (id, client_account_id, contact_id, status, unread_count)
VALUES ('66000000-0000-0000-0000-000000000101', '20000000-0000-0000-0000-000000000001', '61000000-0000-0000-0000-000000000002', 'open', 0);
ROLLBACK;

\echo '=== S5 Message/Conversation cross-account (expect FK failure) ==='
BEGIN;
INSERT INTO messages (id, client_account_id, conversation_id, direction, body)
VALUES ('67000000-0000-0000-0000-000000000101', '20000000-0000-0000-0000-000000000001', '66000000-0000-0000-0000-000000000002', 'inbound', 'cross');
ROLLBACK;

\echo '=== S6 FollowUp/Lead cross-account (expect FK failure) ==='
BEGIN;
INSERT INTO follow_ups (id, client_account_id, lead_id, title, due_at)
VALUES ('68000000-0000-0000-0000-000000000101', '20000000-0000-0000-0000-000000000001', '65000000-0000-0000-0000-000000000002', 'cross', now());
ROLLBACK;

\echo '=== S7 FollowUp/Contact cross-account (expect FK failure) ==='
BEGIN;
INSERT INTO follow_ups (id, client_account_id, contact_id, title, due_at)
VALUES ('68000000-0000-0000-0000-000000000102', '20000000-0000-0000-0000-000000000001', '61000000-0000-0000-0000-000000000002', 'cross', now());
ROLLBACK;

\echo '=== S8 Contact demo_seed_key duplicate (expect unique violation) ==='
BEGIN;
INSERT INTO contacts (id, client_account_id, demo_seed_key, name)
VALUES ('61000000-0000-0000-0000-000000000101', '20000000-0000-0000-0000-000000000001', 'demo-a-contact-001', 'duplicate');
ROLLBACK;

\echo '=== S9 pipeline stage position duplicate (expect unique violation) ==='
BEGIN;
INSERT INTO pipeline_stages (id, client_account_id, pipeline_id, name, position)
VALUES ('64000000-0000-0000-0000-000000000101', '20000000-0000-0000-0000-000000000001', '63000000-0000-0000-0000-000000000001', 'duplicate', 0);
ROLLBACK;

\echo '=== S10 NOT NULL client_account_id (expect not-null violation) ==='
BEGIN;
INSERT INTO properties (id, client_account_id, title, operation_type, location, bedrooms, price)
VALUES ('62000000-0000-0000-0000-000000000101', NULL, 'invalid', 'sale', 'X', 1, 1);
ROLLBACK;

\echo '=== S11 valid same-name resources across accounts are structurally independent ==='
BEGIN;
INSERT INTO contacts (id, client_account_id, demo_seed_key, name)
VALUES ('61000000-0000-0000-0000-000000000102', '20000000-0000-0000-0000-000000000001', 'wu2-a-contact', 'same-name');
INSERT INTO contacts (id, client_account_id, demo_seed_key, name)
VALUES ('61000000-0000-0000-0000-000000000103', '20000000-0000-0000-0000-000000000002', 'wu2-b-contact', 'same-name');
SELECT count(*) AS same_name_rows FROM contacts WHERE name='same-name';
ROLLBACK;

\echo '=== S12 final contamination check ==='
SELECT count(*) AS invalid_leads FROM leads WHERE id IN ('65000000-0000-0000-0000-000000000101','65000000-0000-0000-0000-000000000102','65000000-0000-0000-0000-000000000103');
SELECT count(*) AS invalid_contacts FROM contacts WHERE id IN ('61000000-0000-0000-0000-000000000101','61000000-0000-0000-0000-000000000102','61000000-0000-0000-0000-000000000103');
