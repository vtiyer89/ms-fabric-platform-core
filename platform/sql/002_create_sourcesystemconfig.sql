-- DDL supplied by the data team 2026-09-14. Not read by fabric-cicd — executed directly by
-- scripts/seed_metadata_db.py. Idempotent: safe to run on every seed.

IF OBJECT_ID('metadata.SourceSystemConfig', 'U') IS NULL
BEGIN
    CREATE TABLE [metadata].[SourceSystemConfig](
        [SourceSystemId] [int] IDENTITY(1,1) NOT NULL,
        [SourceSystemName] [nvarchar](250) NOT NULL,
        [Config] [nvarchar](max) NULL,
        [UpdatedDate] [datetime2](7) NOT NULL,
        [IsActive] [bit] NOT NULL,
        [SourceType] [varchar](150) NULL,
        [ModifiedBy] [nvarchar](255) NOT NULL,
        CONSTRAINT [PK_SourceSystemConfig] PRIMARY KEY CLUSTERED ([SourceSystemId] ASC),
        CONSTRAINT [UQ_SourceSystemConfig_SourceSystemName] UNIQUE ([SourceSystemName])
    );

    ALTER TABLE [metadata].[SourceSystemConfig] ADD DEFAULT (getutcdate()) FOR [UpdatedDate];
    ALTER TABLE [metadata].[SourceSystemConfig] ADD DEFAULT ((1)) FOR [IsActive];
    ALTER TABLE [metadata].[SourceSystemConfig] ADD DEFAULT (suser_sname()) FOR [ModifiedBy];
END
GO
