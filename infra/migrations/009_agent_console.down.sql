-- 009_agent_console.down.sql
-- Reverts 009_agent_console.up.sql (console tables + KB deactivated_at;
-- 001-008 untouched).
-- Precondition: demo bases are disposable and reset first, since dropping the
-- tables assumes no console rows remain referenced (no child tables exist).

ALTER TABLE knowledge_base_articles DROP COLUMN IF EXISTS deactivated_at;

DROP TABLE IF EXISTS agent_calendar_config;
DROP TABLE IF EXISTS agent_field_allowlist;
DROP TABLE IF EXISTS agent_prompts;
DROP TABLE IF EXISTS agent_policy_matrix;
DROP TABLE IF EXISTS agent_settings;
