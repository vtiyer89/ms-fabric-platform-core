# Mapping this estate onto the team's metadata schema

**Status (2026-09-15): scoped, deferred. Nothing here is built.** Landed on `main` from
`feat/metadata-driven` (commit `7e794f2`) after a fresh scoping pass confirmed the finding still
holds post-revert. Written so the decision about the Fabric SQL metadata database is taken with
the facts, and so the reasoning is not re-derived.

The team's pattern is a **Fabric SQL database**, `metadata` schema, per-row JSON in a `Config`
column. Two tables are known: `metadata.SourceSystemConfig` and `metadata.TargetStoreConfig`,
DDL supplied by the team 2026-09-14.

---

## The finding that should drive the decision

**In this estate these tables would have no consumer.**

Every pipeline is hand-built and single-purpose. Verified twice: once during the original
analysis, and again 2026-09-15 against `main` after `ms-fabric-ingestion`,
`ms-fabric-dd-trip-data`, `ms-fabric-orchestration`, and `ms-fabric-platform-core` were rolled
back to `parameter.yml`. Both passes agree:

```
invoke_silver_transform   TridentNotebook
invoke_gold_aggregate     TridentNotebook
pl_landing_nyc_ingest     SetVariable          <- a stub; it ingests nothing
pl_bronze_nyc_taxi        InvokeCopyJob
pl_orch_trips             InvokePipeline x4
```

There is **no `Lookup` and no `ForEach` anywhere in the estate** — nothing reads configuration at
run time and nothing loops. There is **one** source system (`nyc_taxi`), and the parquet reaches
landing by manual upload because `pl_landing_nyc_ingest` is a `SetVariable` stub. The 2026-09-15
pass additionally confirmed the CopyJob's source and destination connections are both
Fabric-internal lakehouses, not an external DB/API/blob connection — so there is currently
nothing external for `SourceSystemConfig.Config` to describe either.

So `SourceSystemConfig` would hold one row and `TargetStoreConfig` four, both read by nothing.

> **This has already happened once.** Of the four `md_*` Delta tables built in the metadata
> framework, only `md_workspace` and `md_item` are ever read — two `md_table_path()` call sites
> per notebook. **`md_connection` and `md_config` are written on every deploy and consumed by
> nothing.** `md_config` even has a reader function with zero call sites. Building the SQL tables
> now would repeat that, in a second store.

The value of adopting the schema is **alignment and future substrate**, not operation. That is a
legitimate reason — it is just not the same reason, and it should be chosen knowingly.

**Decision (2026-09-15): defer.** Don't build `SourceSystemConfig`/`TargetStoreConfig` until
Spike S3 lands and bronze has an actual row to look up. See "What a first consumer would
require" below.

---

## `metadata.TargetStoreConfig` — 4 rows

Values read out of the item definitions, not invented.

| `TargetName` | `TargetPath` | `LayerName` | `TargetSchema` | Source of truth |
|---|---|---|---|---|
| `lh_landing_nyc_taxi` | `Files/` | `landing` | `NULL` | copy job `source.datasetSettings.location` |
| `yellow_cab_trip_data_bronze` | `Tables/dbo/yellow_cab_trip_data_bronze` | `bronze` | `dbo` | copy job `destination.datasetSettings` |
| `yellow_cab_trip_silver` | `Tables/dbo/yellow_cab_trip_silver` | `silver` | `dbo` | `nb_silver_yellow_cab_transform` write path |
| `yellow_cab_trip_gold` | `Tables/dbo/yellow_cab_trip_gold` | `gold` | `dbo` | `nb_gold_yellow_cab_aggregate` write path |

### `TargetWorkSpaceId` — known for Test

| Layer | GUID |
|---|---|
| landing | `e9472408-b9e1-45ae-8c2a-e22911c8b109` |
| bronze | `95c0dc3f-a90a-48a6-b00d-c4b502521c2d` |
| silver | `d76dab0a-ec8b-445c-aa8d-4c20332c6be5` |
| gold | `4520a3ae-e2fa-4fe3-8108-6018274664ea` |

### `TargetLakehouseId` — NOT known

`docs/ado-variable-groups.md` records workspace GUIDs only; **no Test lakehouse GUID was ever
written down anywhere in these repos.** They must be resolved from the Fabric API by display
name, which is what the deleted seeder did and the reason it resolved live rather than storing
them.

### `Config` — candidate content, NOT a specification

Drawn from values currently hardcoded in the item definitions. **The team's real `Config` sample
was cut off mid-screenshot, so the actual shape is unknown and none is invented here.**

| Row | Candidate `Config` | Where the values are today |
|---|---|---|
| landing | `{"fileName":"yellow_tripdata_2026-01.parquet","format":"parquet","compression":"snappy","recursive":true}` | copy job source |
| bronze | `{"writeBehavior":"Overwrite","auditColumns":["copy_job_id","copy_job_run_id","copy_job_name","timestamp"]}` | copy job destination + `auditColumns` |
| silver | `{"mode":"overwrite","transform_layer":"silver_transform"}` | notebook |
| gold | `{"mode":"overwrite","transform_layer":"gold_aggregate"}` | notebook |

---

## `metadata.SourceSystemConfig` — 1 row

| Column | Value |
|---|---|
| `SourceSystemName` | `nyc_taxi_yellow` |
| `SourceType` | `file` / `parquet` — the schema does not say what this vocabulary is |
| `Config` | `{"fileName":"yellow_tripdata_2026-01.parquet","compression":"snappy","landingLakehouse":"lh_landing_nyc_taxi"}` |

One row, and nothing ingests it: `pl_landing_nyc_ingest` contains a single `SetVariable` activity
returning `trigger_value: "success"`. Whatever puts the parquet in `lh_landing_nyc_taxi/Files`
lives outside these five repos.

---

## Grain question — resolved for this estate, 2026-09-15

**Is `TargetName` a lakehouse, or a table?** The DDL alone couldn't settle it.

Resolved from a fresh read of `ms-fabric-dd-trip-data`'s silver/gold notebooks: each one
reads/writes one specific named Delta table (`yellow_cab_trip_data_bronze` →
`yellow_cab_trip_silver` → `yellow_cab_trip_gold`), never a whole lakehouse. **`TargetName` is
the table; `TargetStoreConfig` is one row per table**, with `TargetWorkSpaceId`/
`TargetLakehouseId` as the parent identity rather than the row grain. This matches the table
above (landing is the one exception — a `Files/` drop, no table, `TargetSchema NULL`).

This resolves the question **for cc-poc's own code**, not necessarily for the team's real
system — their pattern may have multi-table lakehouses where lakehouse-level config is the
right call. Confirm with the team before this grain decision is load-bearing anywhere outside
this estate.

Secondary, still open: the `SourceType` vocabulary, and whether a `Config` JSON schema is
enforced anywhere or is free-form per row.

---

## What a first consumer would require

| Consumer | What blocks it |
|---|---|
| Source file name from `SourceSystemConfig` | The CopyJob has no expression language. It must become a **Copy activity** (spike **S3**) — which replaces the four `$$COPYJOBID`-style audit columns with pipeline expressions. **That changes the shape of bronze's audit columns: a schema-contract change needing the data team's sign-off**, not a refactor. |
| Target routing from `TargetStoreConfig` | Needs a generic pipeline. Nothing in the estate has a `Lookup` or `ForEach`; this is new territory here. |
| Notebooks reading `TargetSchema` / table name | Possible today with no conversion, but `runtime-vs-deploytime-config.md` §B3 argues table names are schema contracts that *should* require a code change. Low value. |
| Anything reading the SQL DB cross-workspace | The access model for a workload identity reading a Fabric SQL database in another workspace is **unverified** — the analogue of spike S5, which was about the metadata lakehouse. |

The strongest near-term candidate remains the one `runtime-vs-deploytime-config.md` §B1 already
identified: the hardcoded `yellow_tripdata_2026-01.parquet`, "a data value, changing on a data
cadence, currently gated behind a CI run". It is blocked by S3.

---

## What does not translate from the metadata framework

**The ownership rule.** The framework gave every row an `owner` of `ci` or `ops`; the seeder
merged only `ci` rows, so an operator could pin a value by flipping it to `ops` and CI would
never revert it. **This schema has no `owner` column.**

`IsActive` is the nearest equivalent. Any seeder must treat `IsActive = 0` as operator intent and
**never resurrect a deactivated row**. Getting this wrong means CI silently overwriting a
deliberate change — exactly the drift the ownership rule existed to prevent.

`UpdatedDate` / `ModifiedBy` default to `getutcdate()` and `suser_sname()`, which gives per-row
audit but not run provenance: they cannot say *which* deploy or *which commit* wrote a row. The
framework's `md_seed_run` table existed for that. If provenance matters, it needs somewhere to
live in this schema too.

---

## References

- `docs/runtime-vs-deploytime-config.md` — the deploy-time vs runtime distinction, and the
  per-value inventory. Its Part B decisions are still open and are the real input to this.
- `docs/metadata-driven-deployment-plan.md` — Layer 1 / Layer 2 split and the Phase 0 spikes.
  **S1 and S3 remain unrun** and gate everything above.
- `docs/ado-variable-groups.md` — appendix holds the Test workspace GUIDs.
