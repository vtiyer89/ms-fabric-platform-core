# Runtime vs Deploy-Time Config — Decisions Needed From the Data Team

**Purpose:** every configurable value in the NYC Taxi pipeline is currently frozen into the
git-exported item JSON. Deploying to Test rewrites some of them; the rest ship as-is. This doc
splits them into *already settled* and *needs your call*, so the data team can decide which
values they want to change without a redeploy.

**Who decides what:** the platform side owns *how* a value gets substituted. The data team owns
*whether a value is business config at all* — nobody else can answer that.

---

## The two mechanisms, and what each is good at

| | `parameter.yml` (fabric-cicd) | Variable Library (Fabric-native) |
|---|---|---|
| Resolves | At **deploy time**, in CI, before the API call | At **run time**, inside Fabric |
| Changing a value needs | A git commit + a pipeline run | An edit in the Fabric UI (or an API call) |
| Good for | Identity — workspace/item/connection GUIDs | Business config — file names, dates, limits, modes |
| Audit trail | Full git history | Item history only, outside the repo |
| Can resolve IDs live | ✅ `$workspace.<name>.$items…` looks up by name | ❌ stores static GUID pairs per value set |

They are not competitors. The question for each value is only: *should changing this require a
deploy?*

Rule of thumb we're proposing: **if changing it is a code change, it belongs in
`parameter.yml`. If changing it is an operational decision, it belongs in a Variable Library.**

---

## Part A — Settled, no decision needed

14 GUID substitutions across five `parameter.yml` files: workspace IDs, lakehouse IDs, pipeline
IDs, and 4 connection IDs. These are *identity*, not config — they must resolve at deploy time,
and Variable Libraries are a worse fit for them (they'd store static GUIDs needing hand-edits
per environment, where the current setup resolves them live by name).

**No action from the data team.** Listed here only so the inventory is complete.

---

## Part B — Needs a decision

Each row: what it is today, why it might want to move, and our recommendation. The question in
every case is the same — *do you want to change this without a redeploy?*

### B1. Source file name ⭐ strongest candidate

| | |
|---|---|
| **Value** | `yellow_tripdata_2026-01.parquet` |
| **Where** | `landing_bronze_copy_job.CopyJob` → `activities[0].source.datasetSettings.location.fileName` |
| **Today** | Hardcoded. Processing February means editing git and redeploying every environment. |
| **Recommendation** | **Move to a Variable Library.** This is the clearest case in the whole system — a data value, changing on a data cadence, currently gated behind a CI run. |

**Question:** how do you expect the processed month to advance — manually per run, on a
schedule, or derived from the trigger? That changes whether this is a variable, a pipeline
parameter, or an expression.

### B2. Write behaviour / refresh semantics

| | |
|---|---|
| **Value** | Copy job `writeBehavior: "Overwrite"`; both notebooks `.mode("overwrite")` |
| **Where** | Copy job JSON; `nb_silver_yellow_cab_transform`, `nb_gold_yellow_cab_aggregate` |
| **Today** | Every run is a full replace of the target table, in all environments. |
| **Recommendation** | Decide first, then place. |

**Question:** is full-overwrite the intended long-term semantic, or a placeholder until
incremental load exists? And should Test differ from Prod (e.g. Test overwrites a sample, Prod
appends)? If the answer is "same everywhere, permanently", leave it in code. If it varies by
environment, it's a Variable Library value.

### B3. Table and schema names

| | |
|---|---|
| **Values** | `dbo.yellow_cab_trip_data_bronze`, `dbo.yellow_cab_trip_silver`, `dbo.yellow_cab_trip_gold` |
| **Where** | Copy job destination JSON; notebook `spark.sql(...)` strings; notebook `abfss://` paths |
| **Today** | Hardcoded in several places per table — note the silver/gold names appear in *both* a SQL string and a path string in the same notebook. |
| **Recommendation** | **Leave as-is.** Table names are schema contracts; changing one is a code change and should go through review. |

**Question:** confirm you never want these to differ between environments. If Test needs
differently-named tables for any reason, say so now — it affects the notebooks, not just config.

### B4. Operational policy on the copy job

| | |
|---|---|
| **Values** | `timeout: "0.12:00:00"` (12h), `retry: 0` |
| **Where** | `landing_bronze_copy_job.CopyJob` → `properties.policy` |
| **Today** | Identical in every environment. A 12-hour timeout with zero retries. |
| **Recommendation** | **Variable Library**, if you want Test to fail fast. |

**Question:** should Test have a shorter timeout so a hung run doesn't burn half a day of
capacity? And is `retry: 0` deliberate?

### B5. Notebook lakehouse IDs — mechanism choice only

| | |
|---|---|
| **Values** | `workspace_id` / `lakehouse_id` Python variables building the `abfss://` write path |
| **Where** | Both notebooks, cell 2 |
| **Today** | Hardcoded GUIDs, rewritten at deploy time by `parameter.yml`. **Works today.** |
| **Recommendation** | **Leave as-is for now.** |

`notebookutils.variableLibrary.get()` could read these at runtime instead, which would take
GUIDs out of notebook source entirely — cleaner to read. But it changes Dev-authored notebook
code, and the Test GUIDs would then be hand-maintained in a value set rather than resolved live
by name. Net: a lateral move. Worth doing only if the data team dislikes GUIDs in notebooks on
readability grounds.

**Question:** do you find the hardcoded GUIDs in the notebooks a problem to work with?

---

## Part C — Blockers and open questions

These aren't config decisions — they're things only the data team can answer, and two of them
block Test end-to-end.

### C1. ⛔ `pl_landing_nyc_ingest` doesn't ingest anything

The pipeline contains exactly one activity — a `SetVariable` returning
`trigger_value: "success"`. It's a stub. Nothing in any repo puts
`yellow_tripdata_2026-01.parquet` into the landing lakehouse.

**Consequences:**
- The copy job reads a file that must have been placed in `lh_landing_nyc_taxi/Files` by some
  process outside git — presumably a manual upload.
- **Test's landing lakehouse is empty**, so the end-to-end functional run cannot pass until
  someone puts a file there.
- Existing docs describing this pipeline as "lands source parquet" are wrong and are being
  corrected.

**Question:** how does the parquet actually get into landing today — manual upload, or
something outside these five repos? Is `pl_landing_nyc_ingest` meant to be built out, or is it
permanently a trigger stub?

### C2. Copy job source/destination workspace mismatch

In the Dev export the copy job's **source** `workspaceId` (`37f9f143-…`) differs from its
**destination** `workspaceId` (`3ab4eb52-…`), even though both lakehouses live under the same
repo folder. The Test deploy currently collapses both to one workspace.

**Question:** was that split intentional in Dev — are landing and bronze genuinely meant to be
separate workspaces? If yes, Test's topology doesn't match Dev's and we should split the
deploy.

### C3. Gold does no aggregation

`nb_gold_yellow_cab_aggregate` reads silver, adds two columns, and writes — no `GROUP BY`, no
aggregation of any kind. It's a pass-through copy of silver.

**Question:** is this intentional (gold as a serving-layer copy), or is the aggregation logic
still to be written? It affects what "correct" looks like when verifying Test.

---

## What we need back

1. **B1** — a decision, plus how the month should advance. This one we'd like to action.
2. **B2, B4** — should Test behave differently from Prod?
3. **B3, B5** — confirm "leave as-is" is fine.
4. **C1** — the blocking one. How does data get into landing?
5. **C2, C3** — context, so the Test verification checks the right things.

Nothing here changes Dev. Dev keeps using Fabric's native git integration exactly as it does
today; all of this concerns how Test (and later Prod) gets its values.
