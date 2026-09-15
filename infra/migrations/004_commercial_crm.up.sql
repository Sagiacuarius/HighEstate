-- 004_commercial_crm.up.sql
-- MVP Commercial Demo WU2. Product-owned CRM tables; no RLS or auth.

ALTER TABLE client_accounts ADD COLUMN demo_seed_key text;
ALTER TABLE client_accounts ADD CONSTRAINT client_accounts_demo_seed_key_unique UNIQUE (demo_seed_key);

CREATE TABLE contacts (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    name text NOT NULL CHECK (btrim(name) <> ''),
    email text,
    phone text,
    tags text[] NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT contacts_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT contacts_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX contacts_account_id_idx ON contacts (client_account_id, id);
CREATE INDEX contacts_account_status_idx ON contacts (client_account_id, status, created_at, id);

CREATE TABLE properties (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    title text NOT NULL CHECK (btrim(title) <> ''),
    operation_type text NOT NULL CHECK (operation_type IN ('sale', 'rent')),
    location text NOT NULL CHECK (btrim(location) <> ''),
    bedrooms integer NOT NULL CHECK (bedrooms >= 0),
    price numeric(14,2) NOT NULL CHECK (price >= 0),
    currency text NOT NULL DEFAULT 'USD' CHECK (btrim(currency) <> ''),
    availability text NOT NULL DEFAULT 'available' CHECK (availability IN ('available', 'reserved', 'unavailable')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT properties_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT properties_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX properties_account_id_idx ON properties (client_account_id, id);
CREATE INDEX properties_account_availability_idx ON properties (client_account_id, availability, created_at, id);

CREATE TABLE pipelines (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    name text NOT NULL CHECK (btrim(name) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pipelines_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT pipelines_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX pipelines_account_id_idx ON pipelines (client_account_id, id);

CREATE TABLE pipeline_stages (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    pipeline_id uuid NOT NULL,
    demo_seed_key text,
    name text NOT NULL CHECK (btrim(name) <> ''),
    position integer NOT NULL CHECK (position >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pipeline_stages_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT pipeline_stages_id_pipeline_account_unique UNIQUE (id, pipeline_id, client_account_id),
    CONSTRAINT pipeline_stages_pipeline_account_fk FOREIGN KEY (pipeline_id, client_account_id)
        REFERENCES pipelines (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT pipeline_stages_pipeline_position_unique UNIQUE (pipeline_id, position),
    CONSTRAINT pipeline_stages_pipeline_name_unique UNIQUE (pipeline_id, name),
    CONSTRAINT pipeline_stages_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX pipeline_stages_account_pipeline_idx ON pipeline_stages (client_account_id, pipeline_id, position, id);

CREATE TABLE leads (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    contact_id uuid NOT NULL,
    selected_property_id uuid,
    pipeline_id uuid NOT NULL,
    stage_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'qualified', 'won', 'lost')),
    source text NOT NULL CHECK (btrim(source) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT leads_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT leads_contact_account_fk FOREIGN KEY (contact_id, client_account_id)
        REFERENCES contacts (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT leads_property_account_fk FOREIGN KEY (selected_property_id, client_account_id)
        REFERENCES properties (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT leads_pipeline_account_fk FOREIGN KEY (pipeline_id, client_account_id)
        REFERENCES pipelines (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT leads_stage_pipeline_account_fk FOREIGN KEY (stage_id, pipeline_id, client_account_id)
        REFERENCES pipeline_stages (id, pipeline_id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT leads_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX leads_account_id_idx ON leads (client_account_id, id);
CREATE INDEX leads_account_pipeline_stage_idx ON leads (client_account_id, pipeline_id, stage_id, created_at, id);

CREATE TABLE conversations (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    contact_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'archived')),
    unread_count integer NOT NULL DEFAULT 0 CHECK (unread_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT conversations_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT conversations_contact_account_fk FOREIGN KEY (contact_id, client_account_id)
        REFERENCES contacts (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT conversations_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX conversations_account_id_idx ON conversations (client_account_id, id);
CREATE INDEX conversations_account_status_idx ON conversations (client_account_id, status, updated_at, id);

CREATE TABLE messages (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    conversation_id uuid NOT NULL,
    demo_seed_key text,
    direction text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body text NOT NULL CHECK (btrim(body) <> ''),
    external_event_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT messages_conversation_account_fk FOREIGN KEY (conversation_id, client_account_id)
        REFERENCES conversations (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT messages_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE UNIQUE INDEX messages_account_external_event_unique
    ON messages (client_account_id, external_event_id) WHERE external_event_id IS NOT NULL;
CREATE INDEX messages_account_conversation_created_idx ON messages (client_account_id, conversation_id, created_at, id);

CREATE TABLE follow_ups (
    id uuid PRIMARY KEY,
    client_account_id uuid NOT NULL REFERENCES client_accounts (id) ON DELETE RESTRICT,
    demo_seed_key text,
    lead_id uuid,
    contact_id uuid,
    title text NOT NULL CHECK (btrim(title) <> ''),
    due_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT follow_ups_id_account_unique UNIQUE (id, client_account_id),
    CONSTRAINT follow_ups_lead_account_fk FOREIGN KEY (lead_id, client_account_id)
        REFERENCES leads (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT follow_ups_contact_account_fk FOREIGN KEY (contact_id, client_account_id)
        REFERENCES contacts (id, client_account_id) ON DELETE RESTRICT,
    CONSTRAINT follow_ups_target_required CHECK (lead_id IS NOT NULL OR contact_id IS NOT NULL),
    CONSTRAINT follow_ups_demo_seed_key_unique UNIQUE (demo_seed_key)
);
CREATE INDEX follow_ups_account_due_idx ON follow_ups (client_account_id, status, due_at, id);
CREATE INDEX follow_ups_account_lead_idx ON follow_ups (client_account_id, lead_id, id);
