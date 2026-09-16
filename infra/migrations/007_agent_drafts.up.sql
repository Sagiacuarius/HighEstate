-- 007_agent_drafts.up.sql
-- LLM Conversational Agent (change: llm-conversational-agent, WU14). NEW table;
-- never edits 001-006. A draft is the durable proposal record the advisor
-- confirms: intent + bounded criteria + deterministic match + proposed actions.
-- jsonb payloads are schema-validated in the command layer before INSERT; the
-- DB carries type CHECKs as backstop. message_id is an unscoped reference on
-- purpose: the accepted demo reset purges messages and must not fail on the
-- durable draft trail.

CREATE TABLE agent_drafts (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    conversation_id uuid NOT NULL,
    message_id uuid,
    contact_id uuid NOT NULL,
    lead_id uuid,
    status text NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','approved','rejected','executed')),
    intent text NOT NULL
        CHECK (intent IN ('search_properties','schedule_visit','reschedule',
                          'ask_question','update_preferences','general')),
    criteria jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(criteria) = 'object'),
    matched_property_ids jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(matched_property_ids) = 'array'),
    proposed_actions jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(proposed_actions) = 'array'),
    reply_text text,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    decided_by uuid,
    execution_result jsonb CHECK (execution_result IS NULL OR jsonb_typeof(execution_result) = 'array'),
    CONSTRAINT agent_drafts_conversation_account_fk FOREIGN KEY (conversation_id, client_account_id)
        REFERENCES conversations (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT agent_drafts_contact_account_fk FOREIGN KEY (contact_id, client_account_id)
        REFERENCES contacts (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT agent_drafts_lead_account_fk FOREIGN KEY (lead_id, client_account_id)
        REFERENCES leads (id, client_account_id) ON DELETE RESTRICT
);

CREATE INDEX agent_drafts_account_created_idx
    ON agent_drafts (client_account_id, created_at, id);
CREATE INDEX agent_drafts_conversation_idx
    ON agent_drafts (client_account_id, conversation_id, created_at);
