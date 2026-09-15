-- 001_initial_tenancy.up.sql
-- Production Runtime Foundation / Work Unit 3
-- PostgreSQL-only schema; application owns authorization separately.
-- Apply this file once, in order, inside a migration transaction.

BEGIN;

CREATE TABLE agency (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (btrim(name) <> ''),
    lifecycle_state text NOT NULL DEFAULT 'active'
        CHECK (lifecycle_state IN ('active', 'suspended', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id uuid PRIMARY KEY,
    lifecycle_state text NOT NULL DEFAULT 'active'
        CHECK (lifecycle_state IN ('active', 'suspended', 'deactivated')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE identity_links (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    provider text NOT NULL CHECK (btrim(provider) <> ''),
    external_subject text NOT NULL CHECK (btrim(external_subject) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT identity_links_provider_subject_unique UNIQUE (provider, external_subject),
    CONSTRAINT identity_links_id_user_unique UNIQUE (id, user_id)
);

CREATE TABLE client_accounts (
    id uuid PRIMARY KEY,
    agency_id uuid NOT NULL REFERENCES agency (id) ON DELETE RESTRICT,
    name text NOT NULL CHECK (btrim(name) <> ''),
    lifecycle_state text NOT NULL DEFAULT 'active'
        CHECK (lifecycle_state IN ('active', 'suspended', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- Supports composite foreign keys that prove Agency ownership.
    CONSTRAINT client_accounts_id_agency_unique UNIQUE (id, agency_id)
);

CREATE INDEX client_accounts_agency_id_idx ON client_accounts (agency_id, id);

CREATE FUNCTION prevent_client_account_agency_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
        RAISE EXCEPTION 'client account agency ownership is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER client_accounts_agency_immutable
BEFORE UPDATE OF agency_id ON client_accounts
FOR EACH ROW
EXECUTE FUNCTION prevent_client_account_agency_change();

CREATE TABLE memberships (
    id uuid PRIMARY KEY,
    agency_id uuid NOT NULL REFERENCES agency (id) ON DELETE RESTRICT,
    client_account_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    roles text[] NOT NULL DEFAULT '{}',
    permissions text[] NOT NULL DEFAULT '{}',
    lifecycle_state text NOT NULL DEFAULT 'active'
        CHECK (lifecycle_state IN ('active', 'suspended', 'revoked')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT memberships_agency_account_fk
        FOREIGN KEY (client_account_id, agency_id)
        REFERENCES client_accounts (id, agency_id)
        ON DELETE RESTRICT,
    CONSTRAINT memberships_canonical_unique
        UNIQUE (agency_id, user_id, client_account_id),
    CONSTRAINT memberships_id_agency_account_unique
        UNIQUE (id, agency_id, client_account_id)
);

CREATE INDEX memberships_agency_user_account_idx
    ON memberships (agency_id, user_id, client_account_id);
CREATE INDEX memberships_agency_account_idx
    ON memberships (agency_id, client_account_id);

CREATE TABLE tenant_resource (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL
        REFERENCES client_accounts (id) ON DELETE RESTRICT,
    resource_key text NOT NULL CHECK (btrim(resource_key) <> ''),
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tenant_resource_account_key_unique
        UNIQUE (client_account_id, resource_key)
);

CREATE INDEX tenant_resource_account_id_idx
    ON tenant_resource (client_account_id, id);
CREATE INDEX tenant_resource_account_created_idx
    ON tenant_resource (client_account_id, created_at, id);

COMMIT;
