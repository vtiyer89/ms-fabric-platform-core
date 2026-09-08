# `parameter.yml` placeholder reference

Every GUID and connection ID that has to change when this project is pointed at a different
tenant, and where each one comes from.

Each of the five `repository_directory` folders now has a `parameter.template.yml` beside its
working `parameter.yml`. The template is the same file with every tenant-specific value
replaced by a `<PLACEHOLDER>`. Copy it to `parameter.yml` and fill it in.

| Template | Workspace |
|---|---|
| `ms-fabric-ingestion/datasource_nyc_taxi/parameter.template.yml` | Landing + Bronze (shared) |
| `ms-fabric-dd-trip-data/silver/parameter.template.yml` | Silver |
| `ms-fabric-dd-trip-data/gold/parameter.template.yml` | Gold |
| `ms-fabric-orchestration/orchestration/parameter.template.yml` | Orchestration |
| `ms-fabric-dp-trip-report/semantic_models/taxi_trip/parameter.template.yml` | Reporting |

There are three classes of value, and they are filled in three different ways.

---

## Class 1 — Connection IDs (variable groups, never edited in the file)

Connections are **tenant objects, not Fabric items**, so no `$workspace`/`$items` variable
reaches them. They are the only class fabric-cicd cannot resolve live, and they stay as
`$ENV:` tokens fed from the per-repo variable group — there is nothing to edit in the template.

| `$ENV:` token | Variable group | Appears in |
|---|---|---|
| `DEV_COPY_JOB_CONNECTION_ID` | `fabric-ingestion` | ingestion |
| `TEST_COPY_JOB_CONNECTION_ID` | `fabric-ingestion` | ingestion |
| `DEV_SILVER_CONNECTION_ID` | `fabric-dd-trip-data` | silver |
| `TEST_SILVER_CONNECTION_ID` | `fabric-dd-trip-data` | silver |
| `DEV_GOLD_CONNECTION_ID` | `fabric-dd-trip-data` | gold |
| `TEST_GOLD_CONNECTION_ID` | `fabric-dd-trip-data` | gold |
| `DEV_PIPELINE_INVOKE_CONNECTION_ID` | `fabric-orchestration` | orchestration |
| `TEST_PIPELINE_INVOKE_CONNECTION_ID` | `fabric-orchestration` | orchestration |

Four connections, each needing a DEV and a TEST value — 8 variables.

**Where the DEV value comes from:** the GUID literally present in the git-exported item JSON.
Grep for it rather than reading it out of the portal:

```bash
grep -o '"connection":"[^"]*"' ms-fabric-orchestration/orchestration/**/pipeline-content.json
```

It must match exactly, or the rule matches nothing and the Dev connection ships to Test. The
deploy script's `assert_find_values_present` fails the run when that happens.

**Where the TEST value comes from:** create the connection in the Test workspace, then copy its
ID. Bootstrap trick — make a throwaway pipeline, add the relevant activity, create the
connection from its Settings tab, copy the ID, delete the pipeline; the connection survives.

> **Then share each connection back to the SPN.** Setting a connection's Authentication kind to
> Service principal controls what identity it *presents downstream* — it does not let the SPN
> *reference the connection* when publishing. That is a separate share: Manage connections and
> gateways → the connection → permissions → add the SPN with at least **User**. This is bug
> 7.5, and it is confirmed fixed only for the copy-job connection.

---

## Class 2 — Dev GUIDs (`find_value`, edited in the template)

These are the left-hand side of each rule: the value baked into your Dev item definitions. They
must match the exported JSON **literally**.

| Placeholder | What it is | In which templates |
|---|---|---|
| `<YOUR-DEV-LANDING-WORKSPACE-ID>` | Dev workspace holding `lh_landing_nyc_taxi` | ingestion |
| `<YOUR-DEV-LH-LANDING-ID>` | Dev `lh_landing_nyc_taxi` lakehouse | ingestion |
| `<YOUR-DEV-BRONZE-WORKSPACE-ID>` | Dev workspace holding `lh_bronze_nyc_taxi` | ingestion, silver |
| `<YOUR-DEV-LH-BRONZE-ID>` | Dev `lh_bronze_nyc_taxi` lakehouse | ingestion, silver |
| `<YOUR-DEV-SILVER-WORKSPACE-ID>` | Dev Silver workspace | silver, gold, orchestration |
| `<YOUR-DEV-LH-SILVER-ID>` | Dev `lh_silver_dd_trip_records` lakehouse | silver, gold |
| `<YOUR-DEV-GOLD-WORKSPACE-ID>` | Dev Gold workspace | gold, orchestration, reporting |
| `<YOUR-DEV-LH-GOLD-ID>` | Dev `lh_gold_dd_trip_records` lakehouse | gold, reporting |
| `<YOUR-DEV-PL-LANDING-INGEST-ID>` | Dev `pl_landing_nyc_ingest` pipeline | orchestration |
| `<YOUR-DEV-PL-BRONZE-ID>` | Dev `pl_bronze_nyc_taxi` pipeline | orchestration |
| `<YOUR-DEV-INVOKE-SILVER-ID>` | Dev `invoke_silver_transform` pipeline | orchestration |
| `<YOUR-DEV-INVOKE-GOLD-ID>` | Dev `invoke_gold_aggregate` pipeline | orchestration |
| `<YOUR-DEV-PIPELINES-WORKSPACE-ID>` | Dev workspace holding **both** the landing and bronze pipelines, per `pl_orch_trips` | orchestration |

13 distinct Dev GUIDs. Several appear in more than one template and **must be filled with the
same value in each** — `<YOUR-DEV-GOLD-WORKSPACE-ID>` appears in three.

### The last row is the odd one

`<YOUR-DEV-PIPELINES-WORKSPACE-ID>` is a genuine quirk of Dev's topology, not a mistake.
`pl_orch_trips`'s `master_landing` and `master_bronze` activities carry the *same* workspace ID
because Dev keeps both pipelines in one workspace, while the lakehouses sit in two others.
Test deliberately splits them.

`find_replace` rewrites every occurrence of a value within a matched item, so it cannot send one
activity to Landing and the other to Bronze. That is why the orchestration template uses
`key_value_replace` with a jsonpath filter on the activity name for those two — the only place
in the project that needs it.

---

## Class 3 — Test workspace display names (edited in the template)

The right-hand side of every cross-workspace lookup. Matched **literally and case-sensitively**
by the Fabric API.

| Placeholder | In which templates | Also in variable group |
|---|---|---|
| `<YOUR-TEST-LANDING-WS-NAME>` | ingestion, orchestration | `fabric-cicd-common` → `TEST_LANDING_WS_NAME` |
| `<YOUR-TEST-BRONZE-WS-NAME>` | silver, orchestration | `fabric-cicd-common` → `TEST_BRONZE_WS_NAME` |
| `<YOUR-TEST-SILVER-WS-NAME>` | gold, orchestration | `fabric-cicd-common` → `TEST_SILVER_WS_NAME` |
| `<YOUR-TEST-GOLD-WS-NAME>` | orchestration, reporting | `fabric-cicd-common` → `TEST_GOLD_WS_NAME` |

The Orchestration and Reporting workspace names appear nowhere — nothing looks into them.

**On rename, this table is the fan-out list.** A workspace recreated under the *same* name
self-heals, because IDs resolve live. A *renamed* one requires editing every file above plus
the variable group. That asymmetry is bug 7.6 and it has already bitten twice.

---

## Filling a template

```bash
cd ms-fabric-dd-trip-data/silver
cp parameter.template.yml parameter.yml
$EDITOR parameter.yml

# Nothing left unfilled?
grep -n '<YOUR-' parameter.yml || echo "all placeholders replaced"
```

Then verify offline, before spending a pipeline run:

```bash
cd ms-fabric-platform-core
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -r scripts/requirements.txt

FABRIC_PARAM_DEV_SILVER_CONNECTION_ID=<dev-guid> \
FABRIC_PARAM_TEST_SILVER_CONNECTION_ID=<test-guid> \
python scripts/debug_parameterization.py \
    --repository-directory ../ms-fabric-dd-trip-data/silver \
    --items-in-scope Lakehouse,DataPipeline,Notebook
```

That checks structure and confirms every `find_value` actually appears in an item definition —
the check that catches a mistyped Dev GUID. It does **not** resolve `$workspace`/`$items`;
those need a live credentialed run.

## What "correct" looks like in the log

```
[debug] Item type match found: True
[debug] Replacing '<dev-guid>' with '<test-guid>' in landing_bronze_copy_job.CopyJob
[info]  Publishing CopyJob 'landing_bronze_copy_job'
```

**Absence of `Replacing ...` lines means the rule did not match and the Dev value shipped.** A
green run proves the JSON was accepted, not that it was correct — nearly every bug in this
project's history failed exactly this way.
