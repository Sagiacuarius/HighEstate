-- 009_agent_console.up.sql
-- Agent Console (change: agent-console, WU16). NEW per-account console tables
-- + one additive KB column; never edits 001-008. The console persists the
-- levers the WU13/WU14/WU15 code paths compute from module constants today
-- (agent_policy verdicts, build_system_prompt() voice, BUSINESS_* scheduling
-- windows, lead-field writes), so seeds byte-equal in effect to the code
-- defaults keep install at zero behavior change: consumers fall back to the
-- constants when a row is missing. knowledge_base_articles gains the
-- soft-deactivate home (deactivated_at NULL = visible, matching the accepted
-- retrieval default); agent_drafts / conversations untouched. One settings
-- row per account; the policy matrix carries one row per account x action
-- type over the 7 real action-type names from api/application/agent_policy.py.

CREATE TABLE agent_settings (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    global_auto boolean NOT NULL DEFAULT true,
    calendar_auto_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_settings_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT agent_settings_account_unique UNIQUE (client_account_id),
    CONSTRAINT agent_settings_demo_seed_key_unique UNIQUE (demo_seed_key)
);

CREATE TABLE agent_policy_matrix (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    action_type text NOT NULL
        CHECK (action_type IN ('send_reply','register_interest','create_follow_up',
                              'transition_lead','create_event','reschedule_event',
                              'handoff_to_human')),
    demo_seed_key text,
    verdict text NOT NULL CHECK (verdict IN ('auto','escalate')),
    CONSTRAINT agent_policy_matrix_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT agent_policy_matrix_account_action_unique UNIQUE (client_account_id, action_type),
    CONSTRAINT agent_policy_matrix_demo_seed_key_unique UNIQUE (demo_seed_key)
);

CREATE INDEX agent_policy_matrix_account_idx
    ON agent_policy_matrix (client_account_id);

-- Prompt history is append-only (revert = INSERT, never UPDATE): one active
-- row per account via the partial unique index, monotonic versions per
-- account via (client_account_id, version).
CREATE TABLE agent_prompts (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    version integer NOT NULL CHECK (version > 0),
    tone text NOT NULL,
    instructions text NOT NULL,
    composed_snapshot text NOT NULL,
    is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    demo_seed_key text,
    CONSTRAINT agent_prompts_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT agent_prompts_account_version_unique UNIQUE (client_account_id, version),
    CONSTRAINT agent_prompts_demo_seed_key_unique UNIQUE (demo_seed_key)
);

CREATE UNIQUE INDEX agent_prompts_one_active_idx
    ON agent_prompts (client_account_id) WHERE is_active;

-- Presence = allowed. The domain is the only real mutable business columns;
-- 'selected_property_id' stays unseeded (denied until the console allows it).
CREATE TABLE agent_field_allowlist (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    column_name text NOT NULL
        CHECK (column_name IN ('stage_id','status','selected_property_id')),
    demo_seed_key text,
    CONSTRAINT agent_field_allowlist_account_column_unique UNIQUE (client_account_id, column_name),
    CONSTRAINT agent_field_allowlist_demo_seed_key_unique UNIQUE (demo_seed_key)
);

CREATE TABLE agent_calendar_config (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    window_start text,
    window_end text,
    slot_minutes integer CHECK (slot_minutes > 0),
    working_days integer[],
    destination_label text,
    demo_seed_key text,
    CONSTRAINT agent_calendar_config_account_unique UNIQUE (client_account_id),
    CONSTRAINT agent_calendar_config_demo_seed_key_unique UNIQUE (demo_seed_key)
);

-- KB deactivation seam: default NULL = visible, so accepted retrieval is
-- unchanged until a console soft-deactivate sets the timestamp.
ALTER TABLE knowledge_base_articles ADD COLUMN deactivated_at timestamptz;
