-- Apply only to an explicitly approved database/schema using an administrator.
-- The running application never executes this migration or creates resources.
BEGIN;
CREATE SCHEMA IF NOT EXISTS desktop_bridge;
CREATE TABLE IF NOT EXISTS desktop_bridge.personal_context (
    owner_id text PRIMARY KEY CHECK (owner_id ~ '^[A-Za-z0-9_-]{1,64}$'),
    revision bigint NOT NULL CHECK (revision >= 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
        CHECK (octet_length(payload::text) <= 131072)
        CHECK ((payload->>'revision')::bigint = revision),
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMIT;
-- Give the dedicated application role only USAGE on this schema and
-- SELECT, INSERT, UPDATE on this table. Do not use a production/admin role.
-- This single-owner app is not a tenant-isolation boundary or an RLS policy.
