-- 011_agent_custom_field_writes.up.sql
-- Agent Custom Field Writes (change: agent-custom-field-writes, WU18). NEW
-- per-account signal->field binding table + one honest CHECK widening; never
-- edits 001-010. agent_signal_bindings is one row per (client_account_id,
-- signal_key) over the closed 7-signal catalog; the composite FK pins a
-- binding to a same-account custom_field_definitions row (tenant safety by
-- construction, mirroring agent_field_allowlist in 010) with ON DELETE
-- CASCADE so deleting a definition clears its bindings. No binding rows are
-- seeded, so install is zero behavior change. The agent_policy_matrix CHECK
-- is widened 7->8 via DROP + re-ADD admitting 'set_custom_field_value'
-- (same drop/re-add convention 010 used for the allowlist CHECK).

CREATE TABLE agent_signal_bindings (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    signal_key text NOT NULL
        CHECK (signal_key IN ('phone','email','presupuesto_max','barrio','tipo_propiedad',
                              'fecha_objetivo','medio_de_contacto')),
    field_definition_id uuid NOT NULL,
    demo_seed_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_signal_bindings_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT agent_signal_bindings_account_signal_unique UNIQUE (client_account_id, signal_key),
    CONSTRAINT agent_signal_bindings_demo_seed_key_unique UNIQUE (demo_seed_key),
    CONSTRAINT agent_signal_bindings_field_account_fk
        FOREIGN KEY (field_definition_id, client_account_id)
        REFERENCES custom_field_definitions (id, client_account_id) ON DELETE CASCADE
);
CREATE INDEX agent_signal_bindings_account_idx
    ON agent_signal_bindings (client_account_id, signal_key);

-- Policy matrix: widen the 7-type CHECK to admit the 8th action type.
ALTER TABLE agent_policy_matrix DROP CONSTRAINT agent_policy_matrix_action_type_check;
ALTER TABLE agent_policy_matrix ADD CONSTRAINT agent_policy_matrix_action_type_check
    CHECK (action_type IN ('send_reply','register_interest','create_follow_up',
                           'transition_lead','create_event','reschedule_event',
                           'handoff_to_human','set_custom_field_value'));
