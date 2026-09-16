\set ON_ERROR_STOP on
-- Removes only rows carrying the exact demo markers, inside the two designated
-- demo accounts. Fail-closed: if either target account lacks the expected
-- ownership marker, abort before deleting anything.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('mvp-commercial-demo-seed-reset'));

-- Ownership gate: both designated accounts must exist AND carry the exact marker.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM client_accounts
                 WHERE id = '20000000-0000-0000-0000-000000000001' AND demo_seed_key = 'demo-account-a')
     OR NOT EXISTS (SELECT 1 FROM client_accounts
                 WHERE id = '20000000-0000-0000-0000-000000000002' AND demo_seed_key = 'demo-account-b') THEN
    RAISE EXCEPTION 'target demo accounts are absent or their ownership markers do not match demo-account-a/b; refusing to reset';
  END IF;
END $$;

-- Delete only demo-marked rows owned by the verified accounts (marker + account).
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
COMMIT;
