-- Not read by fabric-cicd (SQLDatabase items are shell-only publish — see docs/metadata-db-mapping.md).
-- Executed directly by scripts/seed_metadata_db.py against the deployed database. Idempotent:
-- safe to run on every seed, whether or not the schema already exists.

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'metadata')
BEGIN
    EXEC('CREATE SCHEMA metadata');
END
GO
