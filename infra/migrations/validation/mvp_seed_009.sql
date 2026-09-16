\set ON_ERROR_STOP on
-- Agent Console seed (change: agent-console, WU16). Requires migration 009.
-- Extends the accepted mvp_seed.sql baseline (+ mvp_seed_005.sql catalog rows
-- + mvp_seed_008.sql KB corpus): same fail-closed ownership rules (designated
-- ids + demo_* markers, advisory lock, single transaction). It only enriches
-- rows it designates and never claims foreign/unmarked rows. Re-apply
-- converges (DELETE-then-INSERT). Content: one agent_settings row per demo
-- account (markers demo-a/b-settings), 7 agent_policy_matrix rows per account
-- with verdicts equal to the api/application/agent_policy.py constants (the 6
-- AUTO_ACTION_TYPES auto, handoff_to_human escalate; markers
-- demo-a/b-policy-<action_type>), one active agent_prompts v1 per account
-- (tone = AGENT_TONE, instructions = '', composed_snapshot byte-equal to
-- build_system_prompt(); markers demo-a/b-prompt-v1), allowlist rows
-- stage_id + status per account (markers demo-a/b-allow-*), and one
-- agent_calendar_config row per account mirroring the WU13 BUSINESS_*
-- constants (markers demo-a/b-cal-config). Requires the 009 tables and the
-- knowledge_base_articles.deactivated_at column; fails closed without them.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('mvp-commercial-demo-seed-reset'));

-- Ownership gate for every row this seed designates. Any row at a designated
-- id (or holding a designated marker) must already belong to the expected
-- account and carry the exact marker; otherwise abort before touching anything.
DO $$
DECLARE
  expected record;
  found_account uuid;
  found_key text;
  marker_owner uuid;
BEGIN
  FOR expected IN
    SELECT * FROM (VALUES
      ('agent_settings',        '70000000-0000-0000-0000-000000000001'::uuid, '20000000-0000-0000-0000-000000000001'::uuid, 'demo-a-settings'::text),
      ('agent_settings',        '70000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-settings'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-policy-send_reply'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'demo-a-policy-register_interest'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 'demo-a-policy-create_follow_up'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000001', 'demo-a-policy-transition_lead'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000001', 'demo-a-policy-create_event'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000006', '20000000-0000-0000-0000-000000000001', 'demo-a-policy-reschedule_event'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000007', '20000000-0000-0000-0000-000000000001', 'demo-a-policy-handoff_to_human'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000008', '20000000-0000-0000-0000-000000000002', 'demo-b-policy-send_reply'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000009', '20000000-0000-0000-0000-000000000002', 'demo-b-policy-register_interest'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000010', '20000000-0000-0000-0000-000000000002', 'demo-b-policy-create_follow_up'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000011', '20000000-0000-0000-0000-000000000002', 'demo-b-policy-transition_lead'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000012', '20000000-0000-0000-0000-000000000002', 'demo-b-policy-create_event'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000013', '20000000-0000-0000-0000-000000000002', 'demo-b-policy-reschedule_event'),
      ('agent_policy_matrix',   '71000000-0000-0000-0000-000000000014', '20000000-0000-0000-0000-000000000002', 'demo-b-policy-handoff_to_human'),
      ('agent_prompts',         '72000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-prompt-v1'),
      ('agent_prompts',         '72000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-prompt-v1'),
      ('agent_field_allowlist', '73000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-allow-stage_id'),
      ('agent_field_allowlist', '73000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'demo-a-allow-status'),
      ('agent_field_allowlist', '73000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000002', 'demo-b-allow-stage_id'),
      ('agent_field_allowlist', '73000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000002', 'demo-b-allow-status'),
      ('agent_calendar_config', '74000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-cal-config'),
      ('agent_calendar_config', '74000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-cal-config')
    ) AS v(tbl, id, account, key)
  LOOP
    EXECUTE format('SELECT client_account_id, demo_seed_key FROM %I WHERE id = $1', expected.tbl)
      INTO found_account, found_key USING expected.id;
    IF found_account IS NOT NULL
       AND (found_account <> expected.account OR found_key IS DISTINCT FROM expected.key) THEN
      RAISE EXCEPTION 'seed row % in % conflicts with an existing row at id %; refusing to claim it',
        expected.key, expected.tbl, expected.id;
    END IF;
    EXECUTE format('SELECT id FROM %I WHERE demo_seed_key = $1 AND (id <> $2 OR client_account_id <> $3) LIMIT 1', expected.tbl)
      INTO marker_owner USING expected.key, expected.id, expected.account;
    IF marker_owner IS NOT NULL THEN
      RAISE EXCEPTION 'marker % in % is already held by row %; refusing to seed',
        expected.key, expected.tbl, marker_owner;
    END IF;
  END LOOP;
END $$;

-- Converge: remove only rows this seed designates (marker + account). None of
-- the console tables has children, so DELETE-then-INSERT is safe.
DELETE FROM agent_calendar_config WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-cal-config'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-cal-config'));
DELETE FROM agent_field_allowlist WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-allow-stage_id'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-allow-status'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-allow-stage_id'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-allow-status'));
DELETE FROM agent_prompts WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-prompt-v1'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-prompt-v1'));
DELETE FROM agent_policy_matrix WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-policy-send_reply'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-policy-register_interest'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-policy-create_follow_up'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-policy-transition_lead'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-policy-create_event'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-policy-reschedule_event'),
  ('20000000-0000-0000-0000-000000000001', 'demo-a-policy-handoff_to_human'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-policy-send_reply'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-policy-register_interest'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-policy-create_follow_up'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-policy-transition_lead'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-policy-create_event'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-policy-reschedule_event'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-policy-handoff_to_human'));
DELETE FROM agent_settings WHERE (client_account_id, demo_seed_key) IN (
  ('20000000-0000-0000-0000-000000000001', 'demo-a-settings'),
  ('20000000-0000-0000-0000-000000000002', 'demo-b-settings'));

-- Settings: master switches TRUE/TRUE reproduces today's code behavior exactly
-- (AUTO_EVENT_CREATION = True; per-conversation agent_mode stays the finer
-- switch).
INSERT INTO agent_settings (id, client_account_id, demo_seed_key, global_auto, calendar_auto_enabled) VALUES
  ('70000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'demo-a-settings', true, true),
  ('70000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'demo-b-settings', true, true);

-- Policy matrix: verdicts equal to the agent_policy.py OD-1 default table —
-- the six AUTO_ACTION_TYPES auto (calendar writes auto while the kill-switch
-- stays True), handoff_to_human escalate (mechanics-only, never a reply).
INSERT INTO agent_policy_matrix (id, client_account_id, action_type, demo_seed_key, verdict) VALUES
  ('71000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'send_reply', 'demo-a-policy-send_reply', 'auto'),
  ('71000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'register_interest', 'demo-a-policy-register_interest', 'auto'),
  ('71000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 'create_follow_up', 'demo-a-policy-create_follow_up', 'auto'),
  ('71000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000001', 'transition_lead', 'demo-a-policy-transition_lead', 'auto'),
  ('71000000-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000001', 'create_event', 'demo-a-policy-create_event', 'auto'),
  ('71000000-0000-0000-0000-000000000006', '20000000-0000-0000-0000-000000000001', 'reschedule_event', 'demo-a-policy-reschedule_event', 'auto'),
  ('71000000-0000-0000-0000-000000000007', '20000000-0000-0000-0000-000000000001', 'handoff_to_human', 'demo-a-policy-handoff_to_human', 'escalate'),
  ('71000000-0000-0000-0000-000000000008', '20000000-0000-0000-0000-000000000002', 'send_reply', 'demo-b-policy-send_reply', 'auto'),
  ('71000000-0000-0000-0000-000000000009', '20000000-0000-0000-0000-000000000002', 'register_interest', 'demo-b-policy-register_interest', 'auto'),
  ('71000000-0000-0000-0000-000000000010', '20000000-0000-0000-0000-000000000002', 'create_follow_up', 'demo-b-policy-create_follow_up', 'auto'),
  ('71000000-0000-0000-0000-000000000011', '20000000-0000-0000-0000-000000000002', 'transition_lead', 'demo-b-policy-transition_lead', 'auto'),
  ('71000000-0000-0000-0000-000000000012', '20000000-0000-0000-0000-000000000002', 'create_event', 'demo-b-policy-create_event', 'auto'),
  ('71000000-0000-0000-0000-000000000013', '20000000-0000-0000-0000-000000000002', 'reschedule_event', 'demo-b-policy-reschedule_event', 'auto'),
  ('71000000-0000-0000-0000-000000000014', '20000000-0000-0000-0000-000000000002', 'handoff_to_human', 'demo-b-policy-handoff_to_human', 'escalate');

-- Prompts v1: tone = AGENT_TONE, instructions = '', so the composed output is
-- byte-identical to build_system_prompt() today (harness asserts the oracle).
-- The composed_snapshot below was captured from build_system_prompt() output.
INSERT INTO agent_prompts (id, client_account_id, version, tone, instructions, composed_snapshot, is_active, demo_seed_key) VALUES
  ('72000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 1, 'cordial, profesional, empática y concisa', '',
   'Sos Sofía, asesora inmobiliaria virtual de HighEstate.

Voz y estilo: cordial, profesional, empática y concisa. Respondé siempre en español neutro.
Límites: Podés informar un horario de visita que el sistema ya confirmó; ante cualquier incertidumbre diferís la decisión al asesor humano, ante una queja o un pedido de hablar con una persona derivás a un humano, y nunca inventás datos: lo no confirmado se propone, no se promete..

Tu tarea: leer el mensaje de un cliente/lead inmobiliario y devolver EXACTAMENTE
un objeto JSON con esta forma, sin texto adicional:

{
  "intent": "search_properties | schedule_visit | reschedule | ask_question | update_preferences | handoff | general",
  "criteria": {
    "operation_type": "sale | rent | temporary_rent | null",
    "property_type": "apartment | house | ph | land | office | commercial | warehouse | null",
    "city": "string o null",
    "budget_max": numero o null,
    "rooms_min": numero entero o null,
    "bathrooms_min": numero entero o null,
    "parking": true/false,
    "preferred_date": "YYYY-MM-DD" o null,
    "preferred_time": "HH:MM" o null
  },
  "reply_text": "respuesta breve al lead, en tu voz, coherente con la tarea propuesta"
}

Reglas:
- No inventés datos: usá null ante cualquier dato no explícito en el mensaje.
- `reply_text`: 1 a 3 oraciones, en tu voz, sin prometer lo no confirmado.
  En la vía automática solo se envía texto con fuente verificada (artículo o
  catálogo); tu `reply_text` libre queda para revisión del asesor.
- Un mensaje que pide hablar con un humano, o que trae un reclamo o queja,
  es "handoff".
- Un mensaje de visita que pide cambiar una existente es "reschedule".
- Si el mensaje no pide un humano pero expresa enojo o decepción, igual se
  deriva: marcá "handoff" y pedí disculpas con honestidad.
- Si no hay clara intención, usá "general".
- No envuelvas el JSON en markdown ni agregues claves no declaradas.
', true, 'demo-a-prompt-v1'),
  ('72000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 1, 'cordial, profesional, empática y concisa', '',
   'Sos Sofía, asesora inmobiliaria virtual de HighEstate.

Voz y estilo: cordial, profesional, empática y concisa. Respondé siempre en español neutro.
Límites: Podés informar un horario de visita que el sistema ya confirmó; ante cualquier incertidumbre diferís la decisión al asesor humano, ante una queja o un pedido de hablar con una persona derivás a un humano, y nunca inventás datos: lo no confirmado se propone, no se promete..

Tu tarea: leer el mensaje de un cliente/lead inmobiliario y devolver EXACTAMENTE
un objeto JSON con esta forma, sin texto adicional:

{
  "intent": "search_properties | schedule_visit | reschedule | ask_question | update_preferences | handoff | general",
  "criteria": {
    "operation_type": "sale | rent | temporary_rent | null",
    "property_type": "apartment | house | ph | land | office | commercial | warehouse | null",
    "city": "string o null",
    "budget_max": numero o null,
    "rooms_min": numero entero o null,
    "bathrooms_min": numero entero o null,
    "parking": true/false,
    "preferred_date": "YYYY-MM-DD" o null,
    "preferred_time": "HH:MM" o null
  },
  "reply_text": "respuesta breve al lead, en tu voz, coherente con la tarea propuesta"
}

Reglas:
- No inventés datos: usá null ante cualquier dato no explícito en el mensaje.
- `reply_text`: 1 a 3 oraciones, en tu voz, sin prometer lo no confirmado.
  En la vía automática solo se envía texto con fuente verificada (artículo o
  catálogo); tu `reply_text` libre queda para revisión del asesor.
- Un mensaje que pide hablar con un humano, o que trae un reclamo o queja,
  es "handoff".
- Un mensaje de visita que pide cambiar una existente es "reschedule".
- Si el mensaje no pide un humano pero expresa enojo o decepción, igual se
  deriva: marcá "handoff" y pedí disculpas con honestidad.
- Si no hay clara intención, usá "general".
- No envuelvas el JSON en markdown ni agregues claves no declaradas.
', true, 'demo-b-prompt-v1');

-- Allowlist: stage_id + status only (TransitionLeadStageCommand._apply writes
-- both together, so both stay allowed; selected_property_id denied until the
-- console allows it).
INSERT INTO agent_field_allowlist (id, client_account_id, column_name, demo_seed_key) VALUES
  ('73000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'stage_id', 'demo-a-allow-stage_id'),
  ('73000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'status', 'demo-a-allow-status'),
  ('73000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000002', 'stage_id', 'demo-b-allow-stage_id'),
  ('73000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000002', 'status', 'demo-b-allow-status');

-- Calendar config: mirrors the WU13 BUSINESS_* constants (Monday-Saturday,
-- 09:00-18:00, 60-minute slots) so UI availability and agent booking agree.
INSERT INTO agent_calendar_config (id, client_account_id, window_start, window_end, slot_minutes, working_days, destination_label, demo_seed_key) VALUES
  ('74000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', '09:00', '18:00', 60, '{0,1,2,3,4,5}', 'Agenda de la cuenta', 'demo-a-cal-config'),
  ('74000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', '09:00', '18:00', 60, '{0,1,2,3,4,5}', 'Agenda de la cuenta', 'demo-b-cal-config');

COMMIT;
