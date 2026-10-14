\set ON_ERROR_STOP on
-- Configuration seed (change: configuration, WU17). Requires migration 010.
-- Extends the accepted mvp_seed.sql baseline (+ _005 + _008 + _009): same
-- fail-closed ownership rules (designated ids + demo_* markers, advisory
-- lock, single transaction). It only enriches rows it designates and never
-- claims foreign/unmarked rows. Re-apply converges (idempotent UPDATE).
-- Content: sets the per-account timezone on the two marker-owned demo
-- calendar-config rows (demo-a-cal-config / demo-b-cal-config) to
-- 'America/Argentina/Buenos_Aires' (== BUSINESS_TIMEZONE, so availability
-- keeps reproducing the constant). No custom fields, no tags seeded. Requires
-- the 010 timezone column; fails closed without it.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('mvp-commercial-demo-seed-reset'));

-- Ownership gate for every row this seed designates. Any row at a designated
-- id must already belong to the expected account and carry the exact marker;
-- otherwise abort before touching anything.
DO $$
DECLARE
  expected record;
  found_account uuid;
  found_key text;
  marker_owner uuid;
BEGIN
  FOR expected IN
    SELECT * FROM (VALUES
      ('agent_calendar_config', '74000000-0000-0000-0000-000000000001'::uuid, '20000000-0000-0000-0000-000000000001'::uuid, 'demo-a-cal-config'::text),
      ('agent_calendar_config', '74000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-cal-config')
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

-- Converge: idempotent UPDATE (re-apply keeps the same value). No DELETE —
-- these rows are owned by the 009 seed, not re-inserted here.
UPDATE agent_calendar_config
   SET timezone = 'America/Argentina/Buenos_Aires'
 WHERE id IN ('74000000-0000-0000-0000-000000000001',
              '74000000-0000-0000-0000-000000000002');

COMMIT;
