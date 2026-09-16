-- 008_agent_autonomy.down.sql
-- Reverts 008_agent_autonomy.up.sql (KB table + additive columns; 001-007 untouched).
-- The runner only executes --down 004; apply this manually via psql
-- (ON_ERROR_STOP=1) only when the whole 008 layer must be unwound.
-- Precondition: demo bases are disposable and reset first, since restoring the
-- 007 CHECKs assumes no 'auto'-status / 'handoff'-intent rows remain.

ALTER TABLE conversations DROP COLUMN IF EXISTS agent_mode;

ALTER TABLE agent_drafts DROP CONSTRAINT IF EXISTS agent_drafts_auto_attribution_check;
ALTER TABLE agent_drafts DROP COLUMN IF EXISTS auto_decision_actor;
ALTER TABLE agent_drafts DROP COLUMN IF EXISTS execution_mode;
ALTER TABLE agent_drafts DROP COLUMN IF EXISTS policy_decision;

-- restore the original 007 status domain (proposed | approved | rejected | executed)
ALTER TABLE agent_drafts DROP CONSTRAINT IF EXISTS agent_drafts_status_check;
ALTER TABLE agent_drafts ADD CONSTRAINT agent_drafts_status_check
    CHECK (status IN ('proposed','approved','rejected','executed'));

-- restore the original 007 intent domain (no handoff)
ALTER TABLE agent_drafts DROP CONSTRAINT IF EXISTS agent_drafts_intent_check;
ALTER TABLE agent_drafts ADD CONSTRAINT agent_drafts_intent_check
    CHECK (intent IN ('search_properties','schedule_visit','reschedule',
                      'ask_question','update_preferences','general'));

DROP TABLE IF EXISTS knowledge_base_articles;
