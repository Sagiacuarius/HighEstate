-- 008_agent_autonomy.up.sql
-- Agent Autonomy (change: agent-autonomy, WU15). NEW table + additive columns;
-- never edits 001-007. knowledge_base_articles is the seeded, tenant-scoped
-- FAQ corpus the auto path may answer from (keyword retrieval is a pure read;
-- the account predicate is index-backed, matching the 004-007 pattern). On
-- agent_drafts, the 007 intent/status domains widen with 'handoff'/'auto'
-- (drop + re-add mirrors the accepted 005 operation_type pattern) and the
-- OD-2 attribution columns record WHO decided: decided_by stays human-only,
-- policy rows carry the sentinel actor instead — the DB backstop makes an
-- auto row with a human decider unrepresentable. conversations gains the
-- OD-3 per-conversation agent_mode column (manual is the accepted WU14
-- behavior; paused is handoff-only).

CREATE TABLE knowledge_base_articles (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    category text NOT NULL
        CHECK (category IN ('expensas','escritura','garantia','documentos',
                            'financiacion','amenities','requisitos','visitas')),
    question text NOT NULL,
    keywords text[] NOT NULL DEFAULT '{}',
    answer text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_base_articles_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT knowledge_base_articles_demo_seed_key_unique UNIQUE (demo_seed_key)
);

CREATE INDEX knowledge_base_articles_account_category_idx
    ON knowledge_base_articles (client_account_id, category, id);

-- 007's unnamed inline CHECKs carry the Postgres-generated names; drop +
-- re-add widens the domain exactly like the accepted 005 did for properties.
ALTER TABLE agent_drafts DROP CONSTRAINT agent_drafts_status_check;
ALTER TABLE agent_drafts ADD CONSTRAINT agent_drafts_status_check
    CHECK (status IN ('proposed','approved','rejected','executed','auto'));
ALTER TABLE agent_drafts DROP CONSTRAINT agent_drafts_intent_check;
ALTER TABLE agent_drafts ADD CONSTRAINT agent_drafts_intent_check
    CHECK (intent IN ('search_properties','schedule_visit','reschedule',
                      'ask_question','update_preferences','general','handoff'));

-- OD-2 attribution: policy_decision is the persisted verdict (independent of
-- execution), execution_mode says who ran the decision, auto_decision_actor
-- carries the deterministic sentinel on policy rows. The backstop CHECK keeps
-- decided_by honest: an 'auto' status row never has a human decider.
ALTER TABLE agent_drafts ADD COLUMN policy_decision text NOT NULL DEFAULT 'escalate'
    CHECK (policy_decision IN ('auto','escalate'));
ALTER TABLE agent_drafts ADD COLUMN execution_mode text NOT NULL DEFAULT 'human'
    CHECK (execution_mode IN ('human','agent_policy'));
ALTER TABLE agent_drafts ADD COLUMN auto_decision_actor uuid;
ALTER TABLE agent_drafts ADD CONSTRAINT agent_drafts_auto_attribution_check
    CHECK (status <> 'auto' OR decided_by IS NULL);

-- OD-3: per-conversation auto trigger scope; 'manual' keeps every non-demo
-- conversation on the accepted WU14 one-click behavior.
ALTER TABLE conversations ADD COLUMN agent_mode text NOT NULL DEFAULT 'manual'
    CHECK (agent_mode IN ('manual','auto','paused'));
