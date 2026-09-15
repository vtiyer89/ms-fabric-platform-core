-- Not read by fabric-cicd (SQLDatabase items are shell-only publish — see docs/metadata-db-mapping.md).
-- Executed directly by scripts/seed_metadata_db.py against the deployed database. Idempotent:
-- safe to run on every seed, whether or not the schema already exists.
--
-- Split into two statements deliberately (diagnostic, 2026-09-15): SELECT 1 against this
-- database succeeds even when this file's original single-batch version failed with "Principal
-- could not be resolved" / "Server identity is not configured" (error 33134). Splitting the
-- catalog read from the EXEC()-wrapped DDL isolates which half actually needs the AAD-principal
-- resolution that's failing -- a plain sys.schemas read almost certainly doesn't, a CREATE SCHEMA
-- authorization check plausibly does (it may need to expand this principal's AAD group
-- memberships, which is exactly what Directory Reader gates). Recombine into one statement once
-- the cause is confirmed.

SELECT 1 FROM sys.schemas WHERE name = 'metadata';
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'metadata')
BEGIN
    EXEC('CREATE SCHEMA metadata');
END
GO
