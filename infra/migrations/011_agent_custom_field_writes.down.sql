-- 011_agent_custom_field_writes.down.sql
-- Reverts 011_agent_custom_field_writes.up.sql (signal bindings table + the
-- 8-type policy-matrix CHECK; 001-010 untouched). The runner only executes
-- --down 004; apply this manually via psql (ON_ERROR_STOP=1) only when the
-- whole 011 layer must be unwound. Precondition: demo bases are disposable
-- and reset first, since restoring the 7-type CHECK assumes no
-- set_custom_field_value matrix rows or signal bindings remain (the binding
-- table is dropped first, so its FK to custom_field_definitions falls away;
-- custom_field_definitions itself, owned by 010, is left intact).

DROP TABLE IF EXISTS agent_signal_bindings;

ALTER TABLE agent_policy_matrix DROP CONSTRAINT IF EXISTS agent_policy_matrix_action_type_check;

-- restore the original 009 7-type CHECK (send_reply .. handoff_to_human)
ALTER TABLE agent_policy_matrix ADD CONSTRAINT agent_policy_matrix_action_type_check
    CHECK (action_type IN ('send_reply','register_interest','create_follow_up',
                           'transition_lead','create_event','reschedule_event',
                           'handoff_to_human'));
