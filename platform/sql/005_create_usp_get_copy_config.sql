-- New for the master/child copy pattern (2026-09-15): resolves everything a copy pipeline
-- needs for one (SourceObjectName, LayerName) pair, joining all three metadata tables in one
-- call instead of each pipeline doing its own separate Lookups. Not read by fabric-cicd --
-- executed directly by scripts/seed_metadata_db.py, same as the table DDL.
--
-- CREATE OR ALTER must be the only statement in its batch (same reason CREATE SCHEMA needed
-- EXEC() wrapping earlier) -- kept in its own file/GO block for that reason.
--
-- Flattens Config via JSON_VALUE so callers (pl_master_landing's Lookup activity) get plain
-- scalar columns back, not nested JSON to parse in pipeline expression language.
--
-- SourceSystemConfig is joined only for Compression -- SourceObjectConfig.Config carries the
-- object+layer-specific fileName/filePath (the more specific source, per
-- docs/metadata-db-mapping.md's own reasoning for why SourceObjectConfig exists), while
-- compression is system-level truth, not repeated per object/layer.

CREATE OR ALTER PROCEDURE metadata.usp_GetCopyJobConfig
    @SourceObjectName NVARCHAR(250),
    @LayerName NVARCHAR(200)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        so.SourceObjectName,
        so.LayerName,
        so.LoadType,
        JSON_VALUE(so.Config, '$.fileName')  AS FileName,
        JSON_VALUE(so.Config, '$.filePath')  AS FilePath,
        JSON_VALUE(ss.Config, '$.compression') AS Compression,
        ts.TargetName,
        ts.TargetPath,
        ts.TargetWorkSpaceId,
        ts.TargetLakehouseId,
        ts.TargetSchema
    FROM metadata.SourceObjectConfig so
    JOIN metadata.SourceSystemConfig ss
        ON ss.SourceSystemId = so.SourceSystemId
    JOIN metadata.TargetStoreConfig ts
        ON ts.TargetStoreConfigId = TRY_CAST(so.TargetStoreID AS INT)
    WHERE so.SourceObjectName = @SourceObjectName
      AND so.LayerName = @LayerName
      AND so.IsActive = 1
      AND ss.IsActive = 1
      AND ts.IsActive = 1;
END
GO
