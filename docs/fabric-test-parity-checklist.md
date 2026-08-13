# Test Parity Checklist

Run through this after deploying (or redeploying) to Test, to confirm Test actually behaves
like Dev — not just that the GitHub Actions runs went green. A green fabric-cicd deploy only
proves the JSON got pushed; it doesn't prove a notebook is reading from the right lakehouse or
that the pipeline chain actually runs end to end. Pairs with
[fabric-test-workspace-sync.md](fabric-test-workspace-sync.md), which covers *setting up* the
sync — this covers *verifying* it.

Re-run the relevant section any time: a `parameter.yml` rule changes, a Test workspace gets
deleted/recreated, a new item is added to any of the five repos, or a connection is
recreated.

## 0. Prerequisites still in place

- [ ] Org secrets present and scoped to all four caller repos: `AZURE_CLIENT_ID`,
      `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID` (Settings → Secrets and variables → Actions,
      org level)
- [ ] Client secret hasn't expired (check its expiry date in Entra ID — nothing surfaces this
      until a deploy suddenly fails with an auth error)
- [ ] Repo variables set: `ms-fabric-ingestion` → `TEST_WORKSPACE_ID`; `ms-fabric-dd-trip-data`
      → `TEST_SILVER_WORKSPACE_ID` + `TEST_GOLD_WORKSPACE_ID`; `ms-fabric-orchestration` →
      `TEST_WORKSPACE_ID`; `ms-fabric-dp-trip-report` → `TEST_WORKSPACE_ID`
- [ ] `ms-fabric-platform-core` → Settings → Actions → General → Access allows the other four
      repos to call its reusable workflow
- [ ] `spn-fabric-test-deploy` is Contributor on all Test workspaces it deploys to (Landing,
      Silver, Gold, Orchestration, Reporting)
- [ ] All four Fabric connections exist, using **Service principal** auth with
      `spn-fabric-test-deploy`'s credentials (not Organizational account)

## 1. Git-level correctness (before deploying)

- [ ] No leftover placeholders anywhere:
      `grep -rn '<TEST-' */parameter.yml` across all five repos returns nothing
- [ ] Every `$workspace.<name>...` reference uses a name that matches the "Topology" table in
      the process doc exactly (`ws-test-landing-rjoose`, `ws-test-dd-sustainability-silver`,
      `ws-test-dd-sustainability-gold`) — a typo here fails loudly at deploy time, but it's
      faster to catch by eye first
- [ ] `scripts/requirements.txt`'s pinned `fabric-cicd` version matches what
      `deploy-fabric-item.yml` and `debug_parameterization.py` (if you ran it) were tested
      against

## 2. Per-workspace deploy checks

Run in deploy order — each section assumes the previous ones already passed.

### Ingestion (`ws-test-landing-rjoose`, from `ms-fabric-ingestion`)

- [ ] `deploy-test.yml` run is green
- [ ] Workspace contains: `lh_landing_nyc_taxi` (Lakehouse), `lh_bronze_nyc_taxi` (Lakehouse),
      `pl_landing_nyc_ingest` (DataPipeline), `pl_bronze_nyc_taxi` (DataPipeline),
      `landing_bronze_copy_job` (CopyJob) — five items, no more, no fewer
- [ ] Open `landing_bronze_copy_job` → its source points at *this* workspace's
      `lh_landing_nyc_taxi`, destination at *this* workspace's `lh_bronze_nyc_taxi` (not a Dev
      workspace — check the workspace name shown in the source/destination picker, not just
      that it resolved without error)
- [ ] Open `pl_bronze_nyc_taxi` → the `copy_landing_bronze` activity's connection is the Test
      copy-job connection you created (name matches what you gave it in step 3 of the setup
      doc)

### Silver (`ws-test-dd-sustainability-silver`, from `ms-fabric-dd-trip-data`)

- [ ] `deploy-silver` job is green
- [ ] Workspace contains: `lh_silver_dd_trip_records` (Lakehouse),
      `nb_silver_yellow_cab_transform` (Notebook), `invoke_silver_transform` (DataPipeline)
- [ ] Open `nb_silver_yellow_cab_transform` → the attached/default lakehouse shown in the
      notebook's lakehouse explorer is **this workspace's** `lh_silver_dd_trip_records`, and
      the `known_lakehouses` list includes **Ingestion's** `lh_bronze_nyc_taxi` — both should
      show up as real, resolved lakehouse names, not raw GUIDs or "not found"
- [ ] Open `invoke_silver_transform` → the `invoke_silver_transform_nb` activity's connection
      is the Test notebook connection you created for Silver

### Gold (`ws-test-dd-sustainability-gold`, from `ms-fabric-dd-trip-data`)

- [ ] `deploy-gold` job is green (should only start after `deploy-silver` succeeds)
- [ ] Workspace contains: `lh_gold_dd_trip_records` (Lakehouse),
      `nb_gold_yellow_cab_aggregate` (Notebook), `invoke_gold_aggregate` (DataPipeline)
- [ ] Open `nb_gold_yellow_cab_aggregate` → attached lakehouse is **this workspace's**
      `lh_gold_dd_trip_records`, `known_lakehouses` includes **Silver's**
      `lh_silver_dd_trip_records`
- [ ] Open `invoke_gold_aggregate` → the `invoke_gold_aggregate_nb` activity's connection is
      the Test notebook connection you created for Gold

### Orchestration (from `ms-fabric-orchestration`)

- [ ] `deploy-test.yml` run is green (should only start after Ingestion, Silver, and Gold have
      all deployed)
- [ ] Workspace contains: `pl_orch_trips` (DataPipeline) only
- [ ] Open `pl_orch_trips` → each of the four activities resolves to the **Test** target, not
      Dev — click into `master_landing`, `master_bronze`, `master_silver`, `master_gold` one
      at a time and confirm the **Workspace** and **Pipeline** dropdowns show the Test
      workspace/pipeline names (`ws-test-landing-rjoose` / `pl_landing_nyc_ingest`, etc.), not
      blank/unresolved
- [ ] All four activities' connection is the Test pipeline-invoke connection from step 3

### Reporting (from `ms-fabric-dp-trip-report`)

- [ ] `deploy-test.yml` run is green (should only start after Gold deploys)
- [ ] Workspace contains: `sm_nyc_taxi_trip` (SemanticModel), `pbi_report_nyc_taxi` (Report)
- [ ] Open `sm_nyc_taxi_trip` → it loads without a "couldn't resolve OneLake path" or similar
      error (this is the strongest signal the Direct Lake `expressions.tmdl` substitution
      landed correctly — a wrong workspace/lakehouse ID here fails at model-open time, not at
      deploy time)
- [ ] Open `pbi_report_nyc_taxi` → it opens against `sm_nyc_taxi_trip` in *this* workspace
      (not silently bound to the Dev semantic model)

## 3. End-to-end functional run

Structural checks above confirm the wiring; this confirms it actually moves data.

- [ ] Run `pl_landing_nyc_ingest` (Ingestion) → succeeds, a file lands in
      `lh_landing_nyc_taxi`
- [ ] Run `pl_bronze_nyc_taxi` (Ingestion) → succeeds, `dbo.yellow_cab_trip_data_bronze` in
      `lh_bronze_nyc_taxi` is populated, row count > 0
- [ ] Run `invoke_silver_transform` (Silver) → succeeds, `dbo.yellow_cab_trip_silver` in
      `lh_silver_dd_trip_records` is populated, includes `transform_timestamp` and
      `transform_layer = "silver_transform"` columns
- [ ] Run `invoke_gold_aggregate` (Gold) → succeeds, `dbo.yellow_cab_trip_gold` in
      `lh_gold_dd_trip_records` is populated, includes `transform_layer = "gold_aggregate"`
- [ ] Run `pl_orch_trips` (Orchestration) end to end, unattended — all four stages succeed in
      order without manually kicking off the next one
- [ ] Open `pbi_report_nyc_taxi` and confirm visuals render real numbers (not blank/error
      tiles) — Direct Lake models can pass the "loads without error" check in step 2 but still
      show empty visuals if the gold table has zero rows

Quick row-count/schema sanity check, run from a notebook attached to the relevant lakehouse:

```python
df = spark.sql("SELECT * FROM lh_gold_dd_trip_records.dbo.yellow_cab_trip_gold")
print(df.count())
df.printSchema()
```

Compare the column list against Dev's gold table — it should be identical, since both are
produced by the same git-tracked notebook code. Row counts won't match Dev's exactly (Test's
source file may differ), but a healthy run should be nonzero and roughly proportional to
whatever file landed in step "Run `pl_landing_nyc_ingest`" above.

## 4. Isolation — confirm Test never touched Dev

- [ ] Skim each deployed pipeline's JSON (via the Fabric REST API, `GET
      /v1/workspaces/{workspaceId}/items/{itemId}/getDefinition`, or just eyeball it in the
      portal) for any of the original Dev GUIDs — none should appear. Dev GUIDs worth grepping
      for: `3ab4eb52-fcd3-48e0-a92c-3e3feb437a79` (Dev Ingestion ws),
      `307d28b4-50b2-43c0-ba34-4f8e0e5c53a0` (Dev Silver ws),
      `2b8cb241-d4ae-4c26-affc-6f89941b18af` (Dev Gold ws),
      `4b97f6ae-61d4-4547-98dc-81ed6a137c28` (Dev orchestration's Ingestion target) — the full
      list is every `DEV:` value across the five `parameter.yml` files
- [ ] `spn-fabric-test-deploy` has **no** access grant on any Dev workspace — check each Dev
      workspace's **Manage access** list
- [ ] Each Dev workspace's git connection is still pointed at its original branch/folder,
      unaffected by anything above (Settings → Git integration, per Dev workspace)

## 5. Sign-off

| Date | Verified by | Sections passed | Notes |
|---|---|---|---|
| | | | |
