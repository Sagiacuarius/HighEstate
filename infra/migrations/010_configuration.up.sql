-- 010_configuration.up.sql
-- Configuration (change: configuration, WU17). NEW per-account configuration
-- surface + one additive column; never edits 001-009. timezone rides the
-- existing agent_calendar_config as a nullable column (NO DB default — the
-- read layer falls back to BUSINESS_TIMEZONE when NULL/absent, so a pre-010
-- base keeps byte-identical free_slots output at install). custom_field_definitions
-- is the per-account catalogue of custom lead fields (name + type + select
-- choices); lead_custom_field_values is the EAV value store (one canonical
-- text value per lead x field). The agent_field_allowlist closed CHECK is
-- widened to a composite FK: a row is either a fixed leads column
-- ('stage_id','status','selected_property_id') or a defined custom field for
-- the same account, enforced by the dual CHECK + composite FK below.

ALTER TABLE agent_calendar_config ADD COLUMN timezone text;

CREATE TABLE custom_field_definitions (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    name text NOT NULL CHECK (btrim(name) <> ''),
    type text NOT NULL CHECK (type IN ('text','number','select','date')),
    choices text[],
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT custom_field_definitions_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT custom_field_definitions_account_name_unique UNIQUE (client_account_id, name),
    CONSTRAINT custom_field_definitions_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX custom_field_definitions_account_idx
    ON custom_field_definitions (client_account_id, id);

CREATE TABLE lead_custom_field_values (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    lead_id uuid NOT NULL,
    field_id uuid NOT NULL,
    value text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lead_custom_field_values_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT lead_custom_field_values_lead_field_unique UNIQUE (lead_id, field_id),
    CONSTRAINT lead_custom_field_values_lead_account_fk FOREIGN KEY (lead_id, client_account_id)
        REFERENCES leads (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT lead_custom_field_values_field_account_fk FOREIGN KEY (field_id, client_account_id)
        REFERENCES custom_field_definitions (id, client_account_id) ON DELETE RESTRICT
);
CREATE INDEX lead_custom_field_values_account_lead_idx
    ON lead_custom_field_values (client_account_id, lead_id, id);

-- Allowlist widening: closed CHECK -> composite FK. custom_field_definitions
-- is created above, so the FK resolves. A row is either a fixed leads column
-- (field_definition_id IS NULL) or a defined custom field for the same
-- account (field_definition_id IS NOT NULL, column_name mirrors the name).
ALTER TABLE agent_field_allowlist DROP CONSTRAINT agent_field_allowlist_column_name_check;
ALTER TABLE agent_field_allowlist ADD COLUMN field_definition_id uuid;
ALTER TABLE agent_field_allowlist ADD CONSTRAINT agent_field_allowlist_column_name_check
    CHECK ((field_definition_id IS NULL AND column_name IN ('stage_id','status','selected_property_id'))
        OR (field_definition_id IS NOT NULL AND btrim(column_name) <> ''));
ALTER TABLE agent_field_allowlist ADD CONSTRAINT agent_field_allowlist_field_account_fk
    FOREIGN KEY (field_definition_id, client_account_id)
    REFERENCES custom_field_definitions (id, client_account_id) ON DELETE RESTRICT;
