-- 006_calendar_events.up.sql
-- Calendar / Agenda (change: calendar-agenda). NEW table; never edits 001-005.
-- A visit/appointment is first-class: optional contact/lead/property references
-- are scope-pinned (composite FK, like the 004 leads_*_account_fk pattern), so
-- a referenced row must live in the same account as the event. Time is
-- timestamptz (UTC); the demo renders in a fixed BUSINESS_TIMEZONE.

CREATE TABLE calendar_events (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    title varchar(200) NOT NULL CHECK (btrim(title) <> ''),
    description text,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    contact_id uuid,
    lead_id uuid,
    property_id uuid,
    status text NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled','cancelled','completed')),
    source text NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','agent')),
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calendar_events_time_order_check CHECK (starts_at < ends_at),
    CONSTRAINT calendar_events_contact_account_fk FOREIGN KEY (contact_id, client_account_id)
        REFERENCES contacts (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT calendar_events_lead_account_fk FOREIGN KEY (lead_id, client_account_id)
        REFERENCES leads (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT calendar_events_property_account_fk FOREIGN KEY (property_id, client_account_id)
        REFERENCES properties (id, client_account_id) ON DELETE RESTRICT
);

-- primary access: events in a time range for one account (also covers the
-- availability overlap query shape).
CREATE INDEX calendar_events_account_start_idx
    ON calendar_events (client_account_id, starts_at, id);
CREATE INDEX calendar_events_account_contact_idx
    ON calendar_events (client_account_id, contact_id);
