-- 004_commercial_crm.down.sql
-- Destructive and operator-reviewed only. The runner requires --down 004.
DROP TABLE follow_ups;
DROP TABLE messages;
DROP TABLE conversations;
DROP TABLE leads;
DROP TABLE pipeline_stages;
DROP TABLE pipelines;
DROP TABLE properties;
DROP TABLE contacts;
ALTER TABLE client_accounts DROP CONSTRAINT client_accounts_demo_seed_key_unique;
ALTER TABLE client_accounts DROP COLUMN demo_seed_key;
