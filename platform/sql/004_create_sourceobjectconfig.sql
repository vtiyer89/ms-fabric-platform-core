-- DDL supplied by the data team 2026-09-15. Not read by fabric-cicd -- executed directly by
-- scripts/seed_metadata_db.py. Idempotent: safe to run on every seed.
--
-- No FOREIGN KEY constraints on SourceSystemId / TargetStoreID -- the team's own DDL doesn't
-- define any, so none are added here either. UQ_SourceObjectConfig_NaturalKey is the one
-- deliberate addition beyond the given DDL (same as SourceSystemConfig/TargetStoreConfig): the
-- team's schema has no natural-key constraint, but scripts/seed_metadata_db.py's MERGE needs one
-- to upsert idempotently. (SourceSystemId, SourceObjectName, LayerName) is the natural key --
-- the same SourceObjectName legitimately appears once per layer it passes through (see the
-- team's own sample data: 'rejoose' appears at both Landing and Bronze).

IF OBJECT_ID('metadata.SourceObjectConfig', 'U') IS NULL
BEGIN
    CREATE TABLE [metadata].[SourceObjectConfig](
        [SourceObjectId] [int] IDENTITY(1,1) NOT NULL,
        [SourceSystemId] [int] NOT NULL,
        [SourceObjectName] [nvarchar](250) NOT NULL,
        [LayerName] [nvarchar](200) NOT NULL,
        [TargetStoreID] [nvarchar](200) NOT NULL,
        [LoadType] [nvarchar](200) NOT NULL,
        [UpdatedDate] [datetime2](7) NOT NULL,
        [IsActive] [bit] NOT NULL,
        [Config] [varchar](max) NULL,
        [ModifiedBy] [nvarchar](255) NOT NULL,
        CONSTRAINT [PK_SourceObjectConfig] PRIMARY KEY CLUSTERED ([SourceObjectId] ASC),
        CONSTRAINT [UQ_SourceObjectConfig_NaturalKey] UNIQUE ([SourceSystemId], [SourceObjectName], [LayerName])
    );

    ALTER TABLE [metadata].[SourceObjectConfig] ADD DEFAULT (getutcdate()) FOR [UpdatedDate];
    ALTER TABLE [metadata].[SourceObjectConfig] ADD DEFAULT ((1)) FOR [IsActive];
    ALTER TABLE [metadata].[SourceObjectConfig] ADD DEFAULT (suser_sname()) FOR [ModifiedBy];
END
GO
