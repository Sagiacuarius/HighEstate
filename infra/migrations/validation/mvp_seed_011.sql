\set ON_ERROR_STOP on
-- Agent Custom Field Writes seed (change: agent-custom-field-writes, WU18).
-- Requires migration 011. Extends the accepted baseline (mvp_seed.sql +
-- _005 + _008 + _009 + _010): same fail-closed ownership rules (designated
-- ids + demo_* markers, advisory lock, single transaction). It only enriches
-- rows it designates and never claims foreign/unmarked rows. Re-apply
-- converges (DELETE-then-INSERT). Content: ONLY the 8th agent_policy_matrix
-- row set_custom_field_value = auto per demo account (markers
-- demo-a/b-policy-set_custom_field_value, matching the AUTO verdict the
-- api/application/agent_policy.py constants will assign in Phase 4). Zero
-- binding rows are seeded, so install is zero behavior change. Requires the
-- widened 011 action_type CHECK; fails closed without it.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('mvp-commercial-demo-seed-reset'));

-- Ownership gate for every row this seed designates. Any row at a designated
-- id (or holding a designated marker) must already belong to the expected
-- account and carry the exact marker; otherwise abort before touching anything.
DO $$
DECLARE
  expected record;
  found_account uuid;
  found_key text;
  marker_owner uuid;
BEGIN
  FOR expected IN
    SELECT * FROM (VALUES
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000015'::uuid, '20000000-0000-0000-0000-000000000001'::uuid, 'demo-a-policy-set_custom_field_value'::text),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000016', '20000000-0000-0000-0000-000000000002', 'demo-b-policy-set_custom_field_value')
    ) AS v(tbl, id, account, key)
  LOOP
    EXECUTE format('SELECT client_account_id, demo_seed_key FROM %I WHERE id = $1', expected.tbl)
      INTO found_account, found_key USING expected.id;
    IF found_account IS NOT NULL
       AND (found_account <> expected.account OR found_key IS DISTINCT FROM expected.key) THEN
      RAISE EXCEPTION 'seed row % in % conflicts with an existing row at id %; refusing to claim it',
        expected.key, expected.tbl, expected.id;
    END IF;
    EXECUTE format('SELECT id FROM %I WHERE demo_seed_key = $1 AND (id <> $2 OR client_account_id <> $3) LIMIT 1', expected.tbl)
      INTO marker_owner USING expected.key, expected.id, expected.account;
    IF marker_owner IS NOT NULL THEN
      RAISE EXCEPTION 'marker % in % is already held by row %; refusing to seed',
        expected.key, expected.tbl, marker_owner;
    END IF;
  END LOOP;
END $$;

-- Converge: remove only rows this seed designates (marker + account), then
-- re-insert. agent_signal_bindings has no seeded rows and is untouched.
DELETE FROM agent_policy_matrix WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-policy-set_custom_field_value'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-policy-set_custom_field_value'));

-- Policy matrix 8th row: set_custom_field_value = auto per demo account.
INSERT INTO agent_policy_matrix (id, client_account_id, action_type, demo_seed_key, verdict) VALUES
  ('71000000-0000-0000-0000-000000000015', '20000000-0000-0000-0000-000000000001', 'set_custom_field_value', 'demo-a-policy-set_custom_field_value', 'auto'),
  ('71000000-0000-0000-0000-000000000016', '20000000-0000-0000-0000-000000000002', 'set_custom_field_value', 'demo-b-policy-set_custom_field_value', 'auto');

COMMIT;
