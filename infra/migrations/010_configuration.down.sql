-- 010_configuration.down.sql
-- Reverts 010_configuration.up.sql (allowlist widening + EAV tables +
-- timezone column; 001-009 untouched). The runner only executes --down 004;
-- apply this manually via psql (ON_ERROR_STOP=1) only when the whole 010
-- layer must be unwound. Precondition: demo bases are disposable and reset
-- first, since restoring the 009 closed enum assumes no custom-field
-- allowlist rows remain (the field_account FK + dual CHECK are dropped first,
-- so field_definition_id can fall away).

ALTER TABLE agent_field_allowlist DROP CONSTRAINT IF EXISTS agent_field_allowlist_field_account_fk;
ALTER TABLE agent_field_allowlist DROP CONSTRAINT IF EXISTS agent_field_allowlist_column_name_check;
ALTER TABLE agent_field_allowlist DROP COLUMN IF EXISTS field_definition_id;

-- restore the original 009 closed enum (stage_id | status | selected_property_id)
ALTER TABLE agent_field_allowlist ADD CONSTRAINT agent_field_allowlist_column_name_check
    CHECK (column_name IN ('stage_id','status','selected_property_id'));

DROP TABLE IF EXISTS lead_custom_field_values;
DROP TABLE IF EXISTS custom_field_definitions;

ALTER TABLE agent_calendar_config DROP COLUMN IF EXISTS timezone;
