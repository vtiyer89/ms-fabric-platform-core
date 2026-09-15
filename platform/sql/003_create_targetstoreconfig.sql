-- DDL supplied by the data team 2026-09-14. Not read by fabric-cicd — executed directly by
-- scripts/seed_metadata_db.py. Idempotent: safe to run on every seed.

IF OBJECT_ID('metadata.TargetStoreConfig', 'U') IS NULL
BEGIN
    CREATE TABLE [metadata].[TargetStoreConfig](
        [TargetStoreConfigId] [int] IDENTITY(1,1) NOT NULL,
        [TargetName] [nvarchar](255) NOT NULL,
        [TargetPath] [nvarchar](500) NOT NULL,
        [LayerName] [nvarchar](100) NOT NULL,
        [TargetWorkSpaceId] [nvarchar](255) NOT NULL,
        [TargetLakehouseId] [nvarchar](255) NOT NULL,
        [TargetSchema] [nvarchar](255) NULL,
        [UpdatedDate] [datetime2](7) NOT NULL,
        [IsActive] [bit] NOT NULL,
        [Config] [varchar](max) NULL,
        [ModifiedBy] [nvarchar](255) NOT NULL,
        CONSTRAINT [PK_TargetStoreConfig] PRIMARY KEY CLUSTERED ([TargetStoreConfigId] ASC),
        CONSTRAINT [UQ_TargetStoreConfig_TargetName] UNIQUE ([TargetName])
    );

    ALTER TABLE [metadata].[TargetStoreConfig] ADD DEFAULT (getutcdate()) FOR [UpdatedDate];
    ALTER TABLE [metadata].[TargetStoreConfig] ADD DEFAULT ((1)) FOR [IsActive];
    ALTER TABLE [metadata].[TargetStoreConfig] ADD DEFAULT (suser_sname()) FOR [ModifiedBy];
END
GO
