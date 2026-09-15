-- 003_audit_outbox.up.sql
-- Work Unit 6: durable audit records and transactional outbox.
BEGIN;

CREATE TABLE audit_records (
    id uuid PRIMARY KEY,
    actor_id uuid REFERENCES users (id) ON DELETE RESTRICT,
    agency_id uuid NOT NULL REFERENCES agency (id) ON DELETE RESTRICT,
    client_account_id uuid REFERENCES client_accounts (id) ON DELETE RESTRICT,
    action text NOT NULL CHECK (btrim(action) <> ''),
    resource_type text NOT NULL CHECK (btrim(resource_type) <> ''),
    resource_id text,
    outcome text NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
    correlation_id text NOT NULL CHECK (btrim(correlation_id) <> ''),
    idempotency_key text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audit_records_metadata_not_sensitive CHECK (
        NOT (metadata ?| ARRAY['password','token','secret','authorization','cookie','raw_body','request_body'])
    ),
    CONSTRAINT audit_records_account_agency_fk
        FOREIGN KEY (client_account_id, agency_id)
        REFERENCES client_accounts (id, agency_id)
        ON DELETE RESTRICT
);

CREATE INDEX audit_records_agency_created_idx ON audit_records (agency_id, created_at, id);
CREATE INDEX audit_records_account_created_idx ON audit_records (client_account_id, created_at, id);
CREATE INDEX audit_records_correlation_idx ON audit_records (correlation_id, created_at, id);

CREATE TABLE outbox_events (
    id uuid PRIMARY KEY,
    event_type text NOT NULL CHECK (btrim(event_type) <> ''),
    aggregate_type text NOT NULL CHECK (btrim(aggregate_type) <> ''),
    aggregate_id text NOT NULL CHECK (btrim(aggregate_id) <> ''),
    agency_id uuid NOT NULL REFERENCES agency (id) ON DELETE RESTRICT,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    correlation_id text NOT NULL CHECK (btrim(correlation_id) <> ''),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'published', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    CONSTRAINT outbox_events_logical_unique
        UNIQUE (event_type, aggregate_type, aggregate_id, idempotency_key),
    CONSTRAINT outbox_events_account_agency_fk
        FOREIGN KEY (client_account_id, agency_id)
        REFERENCES client_accounts (id, agency_id)
        ON DELETE RESTRICT,
    CONSTRAINT outbox_events_payload_not_sensitive CHECK (
        NOT (payload ?| ARRAY['password','token','secret','authorization','cookie','raw_body','request_body'])
    )
);

CREATE INDEX outbox_events_account_status_available_idx
    ON outbox_events (client_account_id, status, available_at, created_at, id);
CREATE INDEX outbox_events_agency_status_available_idx
    ON outbox_events (agency_id, status, available_at, created_at, id);

COMMIT;
