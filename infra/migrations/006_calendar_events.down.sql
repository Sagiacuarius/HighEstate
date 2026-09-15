-- 006_calendar_events.down.sql
-- Reverts 006_calendar_events.up.sql (new table only; 001-005 untouched).

DROP TABLE IF EXISTS calendar_events;
