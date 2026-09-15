-- 002_command_idempotency.up.sql
-- Work Unit 5: durable command idempotency only. Forward migration.
BEGIN;

CREATE TABLE command_idempotency (
    id uuid PRIMARY KEY,
    actor_id uuid NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    operation text NOT NULL CHECK (btrim(operation) <> ''),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    request_hash text NOT NULL CHECK (btrim(request_hash) <> ''),
    result_payload jsonb,
    status text NOT NULL DEFAULT 'completed'
        CHECK (status IN ('processing', 'completed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT command_idempotency_actor_operation_key_unique
        UNIQUE (actor_id, operation, idempotency_key)
);

CREATE INDEX command_idempotency_actor_created_idx
    ON command_idempotency (actor_id, created_at, id);
CREATE INDEX command_idempotency_operation_created_idx
    ON command_idempotency (operation, created_at, id);

COMMIT;
