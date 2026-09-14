# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# ## nb_seed_metadata
#
# Populates this environment's metadata tables from `metadata/environments/<env>.yml`.
#
# **Deliberately has no attached lakehouse.** It resolves its own workspace from the runtime
# context and finds `lh_platform_metadata` by display name through the Fabric API, so this
# notebook contains no GUIDs and needs no `parameter.yml` rules. That is the same pattern the
# workload notebooks move to in Phase 2, so this is also the first real test of spike S2.
#
# **Ownership rule:** the merge below only ever updates rows where `owner = 'ci'`. A row an
# operator has pinned by setting `owner = 'ops'` survives every seed run. See
# `docs/metadata-driven-deployment-plan.md` Phase 1.

# PARAMETERS CELL ********************

# Overridden by the caller (the Fabric Job Scheduler API, via scripts/run_seed_metadata.py).
environment = "TEST"

# The whole <env>.yml, base64-encoded. Travels as a parameter rather than as a file in the
# lakehouse: a copy in Files/config/ would be a second source of truth that anyone with
# workspace access could edit, after which git no longer describes the environment.
environment_map_b64 = ""

# "true" seeds whatever resolves and reports the rest instead of refusing to write.
#
# A seed that runs as a tail step of EACH repo's deploy necessarily sees a half-built
# environment: seeding after ingestion, silver's lakehouse does not exist yet. Strict mode is
# still the default and is what a full-estate seed should use — see the block that consumes
# this for why a partial seed is otherwise a bad idea.
allow_unresolved = "false"

# Provenance, supplied by scripts/run_seed_metadata.py from whichever CI is running. Recorded in
# md_seed_run so the table can answer which commit and which run put a given GUID here — the
# question that matters after a bad deploy, and one "updated_by = ci" cannot answer.
seed_ci = "local"
seed_run_id = ""
seed_git_commit = ""
seed_source_ref = ""
seed_map_checksum = ""

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import base64
import json
from datetime import datetime, timezone

import requests
import yaml
from delta.tables import DeltaTable
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType, TimestampType

FABRIC_API = "https://api.fabric.microsoft.com/v1"
METADATA_LAKEHOUSE = "lh_platform_metadata"

# Audience shorthand for the Fabric API. If this ever returns a token the API rejects, the
# long form is the resource URI "https://api.fabric.microsoft.com".
_token = notebookutils.credentials.getToken("pbi")
SESSION = requests.Session()
SESSION.headers.update({"Authorization": f"Bearer {_token}"})

# The workspace this notebook is running in — never hardcoded, so the same definition is
# correct in every environment.
try:
    PLATFORM_WORKSPACE_ID = notebookutils.runtime.context["currentWorkspaceId"]
except Exception:
    PLATFORM_WORKSPACE_ID = spark.conf.get("trident.workspace.id")

SEEDED_AT = datetime.now(timezone.utc)
SEEDED_BY = "ci"

print(f"[info] environment={environment}")
print(f"[info] platform workspace={PLATFORM_WORKSPACE_ID}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

def api_get_all(path):
    """GET a Fabric collection endpoint, following continuationToken pagination.

    A workspace with more items than one page returns the rest behind a token; ignoring it
    silently truncates the resolution and produces "not found" for items that do exist.
    """
    results, url, params = [], f"{FABRIC_API}{path}", {}
    while True:
        response = SESSION.get(url, params=params, timeout=60)
        response.raise_for_status()
        body = response.json()
        results.extend(body.get("value", []))
        token = body.get("continuationToken")
        if not token:
            return results
        params = {"continuationToken": token}


def resolve_workspace_id(display_name, workspaces):
    """Display names are matched literally and case-sensitively, as fabric-cicd matches them."""
    matches = [w for w in workspaces if w.get("displayName") == display_name]
    if len(matches) > 1:
        raise ValueError(
            f"{len(matches)} workspaces are named {display_name!r}. Display names are the only "
            f"handle this framework has; rename one before seeding."
        )
    return matches[0]["id"] if matches else None


# Dev is mapped by GUID, not by display name.
#
# Every logical name in dev.yml was harvested from the DEV: keys of the parameter.yml files this
# framework replaces, which held GUIDs and never held names. Resolution runs in reverse: the GUID
# is the input, the display name is looked up for the row. Dev therefore does NOT get the
# self-healing property Test has — recreate a Dev workspace and its GUID changes, and dev.yml
# must be updated by hand. That is accepted: Dev is authored through Fabric's own git
# integration and is not deployed to.


def resolve_workspace_spec(spec, workspaces):
    """(workspace_id, display_name, error). Accepts either form of spec."""
    if "workspace_id" in spec:
        workspace_id = spec["workspace_id"]
        match = next((w for w in workspaces if w.get("id") == workspace_id), None)
        if match is None:
            return None, None, f"workspace_id {workspace_id!r} is not a workspace this identity can see"
        return workspace_id, match.get("displayName"), None

    display_name = spec["display_name"]
    workspace_id = resolve_workspace_id(display_name, workspaces)
    if workspace_id is None:
        return None, None, f"display name {display_name!r} matched no workspace"
    return workspace_id, display_name, None


def resolve_item_spec(spec, items):
    """(item_id, display_name, error). Accepts either form of spec."""
    if "item_id" in spec:
        item_id = spec["item_id"]
        match = next((i for i in items if i.get("id") == item_id), None)
        if match is None:
            return None, None, f"item_id {item_id!r} is not in that workspace"
        return item_id, match.get("displayName"), None

    match = next(
        (
            i
            for i in items
            if i.get("displayName") == spec["display_name"] and i.get("type") == spec["type"]
        ),
        None,
    )
    if match is None:
        return None, None, f"{spec['type']} {spec['display_name']!r} not found"
    return match["id"], spec["display_name"], None

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# The environment map is uploaded to the metadata lakehouse's Files/config/ by CI before this
# notebook is triggered. Read as text rather than through Spark — it is YAML, not a data format.
_all_workspaces = api_get_all("/workspaces")
_platform_items = api_get_all(f"/workspaces/{PLATFORM_WORKSPACE_ID}/items")

_lakehouse = next(
    (
        i
        for i in _platform_items
        if i.get("displayName") == METADATA_LAKEHOUSE and i.get("type") == "Lakehouse"
    ),
    None,
)
if _lakehouse is None:
    raise RuntimeError(
        f"{METADATA_LAKEHOUSE} not found in workspace {PLATFORM_WORKSPACE_ID}. "
        f"Deploy the platform/ directory before seeding."
    )

METADATA_LAKEHOUSE_ID = _lakehouse["id"]
LAKEHOUSE_ROOT = (
    f"abfss://{PLATFORM_WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{METADATA_LAKEHOUSE_ID}"
)
if not environment_map_b64.strip():
    raise ValueError(
        "environment_map_b64 is empty. This notebook is triggered by "
        "scripts/run_seed_metadata.py, which passes the environment map inline. Running it "
        "by hand requires pasting the base64 of metadata/environments/<env>.yml into the "
        "parameters cell."
    )

env_map = yaml.safe_load(base64.b64decode(environment_map_b64))

if env_map.get("environment") != environment:
    raise ValueError(
        f"the supplied environment map declares environment {env_map.get('environment')!r} "
        f"but this run was parameterised as {environment!r}. Refusing to seed the wrong "
        f"environment."
    )

print(f"[info] loaded environment map for {environment} ({len(environment_map_b64)} b64 chars)")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# --- Resolve workspaces -------------------------------------------------------------------
unresolved = []
workspace_rows, workspace_ids = [], {}

for logical_name, spec in (env_map.get("workspaces") or {}).items():
    workspace_id, display_name, error = resolve_workspace_spec(spec, _all_workspaces)
    if error:
        unresolved.append(f"workspace {logical_name!r} -> {error}")
        continue
    workspace_ids[logical_name] = workspace_id
    workspace_rows.append((logical_name, display_name, workspace_id, environment, "ci", SEEDED_AT, SEEDED_BY))

# --- Resolve items ------------------------------------------------------------------------
# One item listing per workspace, cached: a workspace with 12 items would otherwise be listed
# 12 times.
item_rows, _items_by_workspace = [], {}

for logical_name, spec in (env_map.get("items") or {}).items():
    ws_logical = spec["workspace"]
    if ws_logical not in workspace_ids:
        unresolved.append(f"item {logical_name!r} -> its workspace {ws_logical!r} did not resolve")
        continue

    ws_id = workspace_ids[ws_logical]
    if ws_id not in _items_by_workspace:
        _items_by_workspace[ws_id] = api_get_all(f"/workspaces/{ws_id}/items")

    item_id, display_name, error = resolve_item_spec(spec, _items_by_workspace[ws_id])
    if error:
        unresolved.append(f"item {logical_name!r} -> {error} in workspace {ws_logical!r}")
        continue

    item_rows.append(
        (logical_name, spec["type"], ws_logical, display_name, item_id, environment, "ci", SEEDED_AT, SEEDED_BY)
    )

# --- Connections and config: taken as given, not resolved ---------------------------------
# Connection resolution by display name is spike S4 and is not verified, so these are written
# through from the environment map as static values.
connection_rows = [
    (logical_name, spec["connection_id"], environment, "ci", SEEDED_AT, SEEDED_BY)
    for logical_name, spec in (env_map.get("connections") or {}).items()
]
config_rows = [
    (key, str(spec["value"]), environment, "ci", SEEDED_AT, SEEDED_BY)
    for key, spec in (env_map.get("config") or {}).items()
]

print(f"[info] resolved {len(workspace_rows)} workspaces, {len(item_rows)} items")
print(f"[info] passthrough {len(connection_rows)} connections, {len(config_rows)} config values")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# Fail before writing anything. A partial seed is worse than no seed: workloads would resolve
# some references and fail at run time on the rest, which is the silent-failure class this
# whole framework exists to remove.
if unresolved:
    detail = "\n  ".join(unresolved)
    if str(allow_unresolved).lower() == "true":
        # Deliberately a warning, not a silent skip: the rows that did resolve are still worth
        # writing, because the alternative during a phased rollout is a table that is stale
        # rather than merely incomplete. Every unresolved name is named here.
        print(
            f"[warn] {len(unresolved)} entries did not resolve and are NOT being written:\n  "
            f"{detail}\n"
            f"[warn] Expected while the estate is only partly deployed. Anything still listed "
            f"after every repo has deployed is a real failure — re-run the seed strictly."
        )
    else:
        raise RuntimeError(
            f"{len(unresolved)} entries in the environment map did not resolve:\n  "
            + detail
            + "\n\nDisplay names are matched literally and case-sensitively. A workspace "
              "recreated under a different name, or a workload not yet deployed, are the usual "
              "causes. Nothing has been written. Pass allow_unresolved=true if this is a "
              "per-repo seed against a partly-deployed environment."
        )

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

_AUDIT = [
    StructField("environment", StringType(), False),
    StructField("owner", StringType(), False),
    StructField("updated_at", TimestampType(), False),
    StructField("updated_by", StringType(), False),
]

TABLES = {
    "md_workspace": (
        StructType(
            [
                StructField("logical_name", StringType(), False),
                StructField("display_name", StringType(), False),
                StructField("workspace_id", StringType(), False),
            ]
            + _AUDIT
        ),
        workspace_rows,
        "logical_name",
    ),
    "md_item": (
        StructType(
            [
                StructField("logical_name", StringType(), False),
                StructField("item_type", StringType(), False),
                StructField("workspace_logical_name", StringType(), False),
                StructField("display_name", StringType(), False),
                StructField("item_id", StringType(), False),
            ]
            + _AUDIT
        ),
        item_rows,
        "logical_name",
    ),
    "md_connection": (
        StructType(
            [
                StructField("logical_name", StringType(), False),
                StructField("connection_id", StringType(), False),
            ]
            + _AUDIT
        ),
        connection_rows,
        "logical_name",
    ),
    "md_config": (
        StructType(
            [
                StructField("config_key", StringType(), False),
                StructField("config_value", StringType(), False),
            ]
            + _AUDIT
        ),
        config_rows,
        "config_key",
    ),
}


def seed(table_name, schema, rows, key_column):
    """Merge rows into a metadata table, never touching operator-pinned rows.

    Truncate-and-reload would be simpler and would silently discard every `ops` override on
    every deploy — the drift failure this design exists to prevent. Rows are matched on
    (key, environment) so one table can hold more than one environment if that is ever wanted.
    """
    path = f"{LAKEHOUSE_ROOT}/Tables/dbo/{table_name}"
    source = spark.createDataFrame(rows, schema)

    if not DeltaTable.isDeltaTable(spark, path):
        source.write.format("delta").mode("overwrite").save(path)
        print(f"[info] {table_name}: created with {source.count()} rows")
        return

    target = DeltaTable.forPath(spark, path)
    updatable = {c: f"s.{c}" for c in schema.fieldNames() if c != "owner"}

    (
        target.alias("t")
        .merge(source.alias("s"), f"t.{key_column} = s.{key_column} AND t.environment = s.environment")
        # The condition is the ownership rule. Without it this reverts every operator override.
        .whenMatchedUpdate(condition="t.owner = 'ci'", set=updatable)
        .whenNotMatchedInsert(values={c: f"s.{c}" for c in schema.fieldNames()})
        .execute()
    )

    pinned = (
        spark.read.format("delta").load(path).where((F.col("owner") != "ci") & (F.col("environment") == environment)).count()
    )
    print(f"[info] {table_name}: merged {len(rows)} rows ({pinned} operator-pinned rows left untouched)")


for _name, (_schema, _rows, _key) in TABLES.items():
    seed(_name, _schema, _rows, _key)

# --- md_seed_run: the log of seeds, appended not merged ------------------------------------
#
# Deliberately NOT merged and NOT owner-gated, unlike the four tables above. Those describe what
# the environment currently IS, so a row is replaced. This describes what HAPPENED, so every run
# adds a row and nothing is ever overwritten — a seed history you can read backwards after a bad
# deploy.
#
# unresolved_count is the column to watch. A partial seed is expected while the estate is only
# partly deployed; a non-zero count after every repo has deployed means something is genuinely
# missing, and this is where that shows up without re-reading CI logs.
_seed_run_schema = StructType([
    StructField("seeded_at", TimestampType(), False),
    StructField("environment", StringType(), False),
    StructField("ci", StringType(), False),
    StructField("run_id", StringType(), True),
    StructField("git_commit", StringType(), True),
    StructField("source_ref", StringType(), True),
    StructField("map_checksum", StringType(), True),
    StructField("workspaces_resolved", StringType(), False),
    StructField("items_resolved", StringType(), False),
    StructField("connections_written", StringType(), False),
    StructField("config_written", StringType(), False),
    StructField("unresolved_count", StringType(), False),
    StructField("unresolved", StringType(), True),
])

_seed_run_row = [(
    SEEDED_AT, environment, seed_ci, seed_run_id, seed_git_commit, seed_source_ref,
    seed_map_checksum,
    str(len(workspace_rows)), str(len(item_rows)), str(len(connection_rows)), str(len(config_rows)),
    str(len(unresolved)), "; ".join(unresolved) if unresolved else None,
)]

_seed_run_path = f"{LAKEHOUSE_ROOT}/Tables/dbo/md_seed_run"
(
    spark.createDataFrame(_seed_run_row, _seed_run_schema)
    .write.format("delta")
    .mode("append")
    .save(_seed_run_path)
)
print(
    f"[info] md_seed_run: recorded {seed_ci} run {seed_run_id or '-'} "
    f"commit {(seed_git_commit or '-')[:8]} map {seed_map_checksum or '-'} "
    f"({len(unresolved)} unresolved)"
)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# Drift report. Operator-pinned rows are legitimate but they are also the difference between
# what git says this environment is and what it actually is, so every seed run surfaces them.
for _name in TABLES:
    _pinned = (
        spark.read.format("delta")
        .load(f"{LAKEHOUSE_ROOT}/Tables/dbo/{_name}")
        .where((F.col("owner") != "ci") & (F.col("environment") == environment))
    )
    if _pinned.count():
        print(f"[warn] {_name} has operator-pinned rows not managed by CI:")
        _pinned.show(truncate=False)

print(f"[info] seed complete for {environment}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
