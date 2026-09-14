# Metadata-Driven Deployment — Implementation Plan

**Status (2026-09-14):** being implemented on `feat/metadata-driven`. This doc is kept as the
original design argument and risk register — its Phase 0 spike list is still accurate and S1/S3
remain unrun. Two things have since changed by decision, and where they conflict the newer
choice wins:

- `parameter.yml` does **not** survive as a bootstrap pointer. It is deleted; deploy-time
  substitution moves into `scripts/resolve_bindings.py`, driven by the same environment map.
- v1 converts **notebooks only** to runtime lookup. Pipelines, the CopyJob and orchestration keep
  deploy-time resolution through the new mechanism, pending spikes S1 and S3.

**What it replaces:** today every workspace ID, item GUID and connection GUID is rewritten into
the item JSON at deploy time by five `parameter.yml` files (see
`docs/fabric-test-workspace-sync.md`). That works — Test is deployed and verified end to end —
but it scales linearly with (GUIDs x environments), and every new environment means hand-editing
five files with a failure mode that is silent.

**What this proposes:** a metadata table, one per environment, living in a platform-core Fabric
workspace. CI/CD seeds it; workloads read it at run time. `parameter.yml` survives, shrunk to a
single bootstrap pointer per workload.

---

## The two layers, and what this plan covers

| | Layer 1 — identity resolution | Layer 2 — orchestration control |
|---|---|---|
| Table holds | workspace IDs, item GUIDs, connection IDs, config values | entities: source, target, load type, watermark, dependencies |
| Replaces | `parameter.yml` substitution | per-entity hand-built pipelines |
| In scope here | **yes** | **no — deliberately deferred** |

Layer 2 is where metadata-driven frameworks actually pay for themselves (200 tables served by one
generic pipeline instead of 200 pipelines). It is also a much larger rewrite of Dev-authored
content. This plan builds the substrate Layer 2 would sit on, and stops there. Revisit once
Layer 1 is running in two environments.

---

## Phase 0 — Spikes. Do these first; two of them can kill parts of the design

Each is a throwaway artifact in a Test workspace, deleted afterwards. None require changing a repo.

### S1. Does Invoke Pipeline accept dynamic `workspaceId` / `pipelineId`? ⛔ blocking

`pl_orch_trips` stores both as literal typeProperties. Build a scratch pipeline with a Lookup
activity feeding those two fields on an InvokePipeline activity.

- **Pass** → orchestration joins the framework; cross-repo deploy ordering disappears entirely.
- **Fail** → orchestration stays `parameter.yml`-driven permanently. The rest of the plan still
  stands, but `ms-fabric-orchestration` keeps its seven substitution rules and its place in the
  deploy order. **Know this before committing to the architecture.**

### S2. Can a notebook run with no default lakehouse attachment?

Both notebooks currently read via a three-part name (`spark.sql("SELECT * FROM
lh_bronze_nyc_taxi.dbo.…")`) which resolves through the attached-lakehouse METADATA block — a
platform mechanism, resolved at session start, not reachable from a table. A metadata-driven
notebook must read and write by `abfss://` path only.

Rewrite silver's first cell to read by path, strip the METADATA `dependencies.lakehouse` block,
confirm it runs. Also confirm what breaks in the Fabric UI without an attachment (the Lakehouse
explorer pane goes away; check nobody depends on it).

### S3. Can a Copy activity replace the CopyJob, audit columns included?

`landing_bronze_copy_job.CopyJob` stores flat static GUIDs in
`properties.source/destination.connectionSettings.typeProperties` and has no expression language.
It cannot consume a metadata table. It has to become a Copy activity inside a DataPipeline.

The catch: its four audit columns use CopyJob-native tokens — `$$COPYJOBID`, `$$COPYJOBRUNID`,
`$$COPYJOBNAME`, `$$NOW`. A pipeline Copy activity has no such tokens; the equivalents are
pipeline expressions (`@pipeline().RunId` and so on). **The bronze table's audit column values
will change shape.** Confirm with the data team that this is acceptable before converting —
it is a schema-contract change, not a refactor.

### S4. Can connections be resolved by display name via the REST API?

Connections are the only GUID class with no dynamic resolution today. The Fabric connections REST
API exists; confirm the deploy SPN can list connections it has access to and match on display
name. If yes, the last hand-maintained GUID class disappears into the seeder. If no, connection
IDs stay a static per-environment row that a human enters once.

### S5. Cross-workspace read on the metadata lakehouse

Every workload identity needs read access to platform-core's metadata table. Confirm the grant
model (workspace Viewer? OneLake data access role?) and that it works from a notebook running as
a pipeline's service principal, not just as your own user.

---

## Phase 1 — The metadata store

### Where it lives

The `Test platform` workspace (`96dd6579-15c0-488f-8fc8-9849cd702db3`) already exists and is
listed as unused precisely because platform-core has no Fabric items. It becomes the metadata
home. **This changes what `ms-fabric-platform-core` is** — today it is a pure tooling repo; it
becomes a tooling repo *and* an item repo with its own `repository_directory`, `parameter.yml`
and deploy job.

### Items to add to `ms-fabric-platform-core`

```
platform/
    parameter.yml                          # bootstrap only: this workspace's own IDs
    lh_platform_metadata.Lakehouse/        # holds the tables below
    nb_seed_metadata.Notebook/             # the seeder (Phase 1b)
    pl_seed_metadata.DataPipeline/         # runnable wrapper, so CI can trigger it
```

### Tables — split by lifecycle, not by convenience

| Table | Written by | Contents | Why separate |
|---|---|---|---|
| `md_workspace` | seeder (CI) | logical_name, display_name, workspace_id, env | Generated, disposable, rebuilt from API |
| `md_item` | seeder (CI) | logical_name, item_type, workspace_logical_name, item_id | Same — and only populatable *after* workloads deploy |
| `md_connection` | seeder or human | logical_name, connection_id, env | Depends on S4 |
| `md_config` | git-sourced | key, value, scope | Business config: source file name, write behaviour, timeouts (see `runtime-vs-deploytime-config.md` Part B) |
| `md_run_state` | workloads (runtime) | watermarks, last run, row counts | **High-write.** Never in the same table as config, or watermark writes contend with config reads |

Every row carries `owner` (`ci` | `ops`), `updated_at`, `updated_by`.

### The ownership rule — decide before writing a line of code

The stated goal is a table writable from both CI/CD and at run time. That is the feature and the
failure mode. Without a rule, either CI silently reverts an operator's 3am fix on the next
release, or CI stops overwriting and git no longer describes the environment.

**Rule:** the seeder performs a merge, never a truncate-reload, and touches only rows where
`owner = 'ci'`. An operator changing a value flips `owner` to `ops`, which pins it. A report of
`ops`-owned rows is part of the release checklist — pinned values are drift, and drift should be
visible and periodically reconciled, not permanent.

### Phase 1b — the seeder

A notebook (`nb_seed_metadata`) that:

1. reads a git-tracked environment map from platform-core —
   `metadata/environments/<env>.yml`, listing logical name -> Fabric display name;
2. calls the Fabric REST API to resolve each display name to a GUID;
3. merges the results into `md_workspace` / `md_item` per the ownership rule;
4. **fails loudly** listing any logical name that resolved to nothing.

This is the single most important design constraint in the plan:

> **The table must be generated, not authored.** `parameter.yml` today resolves by *name*
> against the live API (`$workspace.<name>.$items.Lakehouse.<name>.$id`), so a workspace
> recreated under the same name self-heals. A metadata table of hand-entered GUIDs would lose
> that property and be strictly worse than what we have now. If the seeder is ever bypassed with
> manual inserts, the framework has failed.

---

## Phase 2 — Consumption: notebooks first

Lowest risk, highest immediate payoff. Gated on S2.

1. **Add a OneLake shortcut** in each workload lakehouse pointing at `md_*`. Confirmed viable:
   `shortcuts.metadata.json` is git-tracked in all four lakehouses (currently `[]`) and
   `alm.settings.json` has `Shortcuts.OneLake` **Enabled**. This reduces the bootstrap pointer
   from *one per item* to *one per workspace* — notebooks then read a local relative path with no
   GUID in them at all.
2. **Rewrite the two notebooks:** replace the hardcoded `workspace_id` / `lakehouse_id` variables
   with lookups, replace three-part-name reads with path reads, drop the METADATA
   `dependencies.lakehouse` block.
3. **Retire the corresponding `parameter.yml` rules** — four of the six notebook rules across
   silver and gold go away.

> **Process constraint:** this edits Dev-authored notebook content, and Dev workspaces live-sync
> via Fabric's native git integration. These changes reach Dev the moment they land on `main`.
> Coordinate with whoever owns Dev; do not treat it as a repo-only change.

**Helper code placement** is an open decision. A Fabric Environment item with a wheel is the
clean enterprise answer but is heavier and version-managed separately; a bootstrap cell copied
into each notebook is simpler and duplicated. Recommend a copied cell for v1, Environment when
notebook count justifies it.

---

## Phase 3 — Consumption: pipelines

Gated on S1 and S3.

- Add a Lookup activity against `md_*` at the head of each pipeline; feed downstream fields via
  dynamic content.
- Convert `landing_bronze_copy_job.CopyJob` to a Copy activity inside `pl_bronze_nyc_taxi`
  (S3), removing the one item type that cannot participate.
- If S1 passed, `pl_orch_trips` resolves its four targets from the table and
  `ms-fabric-orchestration/orchestration/parameter.yml` — currently the largest of the five,
  including two `key_value_replace` jsonpath rules — reduces to the bootstrap pointer.

---

## Phase 4 — Collapse `parameter.yml` into platform-core

Only now, once the rules have actually shrunk. Doing this first would move five large files rather
than five small ones.

- Move to `ms-fabric-platform-core/parameters/<workload>.yml` — **one file per workload, not one
  merged file.** A merged file would leave most `find_value`s matching nothing in any given repo's
  directory, and `assert_find_values_present` would fail the deploy, correctly. That guard is the
  most valuable check in the estate; do not relax it for a cosmetic consolidation.
- Add a step to `deploy-fabric-item.yml` copying the right file into
  `item-repo/<repository-directory>/parameter.yml` before the script runs. Both repos are already
  checked out onto the runner, so this is one `cp` and one existence check.
- Each surviving file should contain roughly one rule: the metadata shortcut target or lakehouse
  pointer, plus anything S1/S3 proved cannot move.

---

## Phase 5 — Replace the guard you are giving up

Today a bad reference fails in CI, before anything ships — that is
`assert_find_values_present` in `scripts/deploy_fabric_item.py`, and it is the check that catches
the failure mode behind nearly every bug in this project. Runtime resolution moves that failure
to 3am inside a pipeline run, where the deployed JSON no longer says what it points at.

Build the replacement in the same PR as the framework, not later:

- **`scripts/verify_metadata_coverage.py`** — extract every logical key referenced in notebook and
  pipeline source, assert each has a row in the target environment's table. Runs in
  `validate-fabric-item.yml` (offline, against a git-tracked expected-keys manifest) and again
  post-seed against the live table.
- Extend `tests/` to cover the seeder's merge semantics — specifically that an `ops`-owned row
  survives a seed run.
- Keep `assert_find_values_present` for the bootstrap rules that remain.

---

## Phase 6 — The new-environment runbook

The whole point. Standing up PROD should be:

```
1. Create workspaces, assign capacity, grant the SPN
2. Add a <env>.yml environment map to platform-core          # the only file edited
3. Deploy platform-core                                       # metadata lakehouse exists
4. Deploy every workload repo                                 # ANY ORDER
5. Run the seeder                                             # item GUIDs resolved and merged
6. Run verify_metadata_coverage + the parity checklist
```

**Step 4 is the structural win.** Today `docs/fabric-test-workspace-sync.md` documents a
load-bearing deploy order because `$workspace.<name>.$items` resolves at *deploy* time — silver
cannot deploy before bronze exists. With runtime resolution, silver only needs bronze to exist
when it *runs*. At five repos that is a convenience; at fifty it is the difference between viable
and not.

---

## Sequencing against work already in flight

`feat/delete-gate-and-pr-ci` (platform-core) and `feat/pr-checks-and-pin` (all four item repos)
are committed but unpushed, and **no `v1` tag exists yet** — locally or on the remote — while
those branches pin `@v1`. They touch the same workflow files Phase 4 edits.

**Land that stream and cut the tag first.** Starting this plan on top of unpushed branches that
pin a nonexistent ref will produce conflicts in `deploy-fabric-item.yml` and a deploy path that
resolves to nothing.

---

## Risks

| Risk | Mitigation |
|---|---|
| S1 fails; orchestration cannot be metadata-driven | Plan survives, scope shrinks. Run S1 before anything else. |
| Metadata lakehouse becomes a single point of failure | Every pipeline in every workspace depends on it. Accept consciously; consider a cached fallback for Prod. |
| Config drift between `ci` and `ops` rows | Ownership rule + drift report in the release checklist. |
| Rollback no longer restores state | Reverting code does not revert the table. Version `md_config` alongside the commit that seeded it. |
| Lookup latency at entity scale | Negligible at current size; broadcast/cache once Layer 2 lands. |
| Dev notebooks change under the data team | Phase 2 process constraint. Coordinate before merging. |

## Explicitly out of scope for v1

Layer 2 (entity control table, generic looping pipelines), watermark-based incremental load,
replacing the Variable library conversation in `runtime-vs-deploytime-config.md` (that doc's
Part B decisions remain open and are complementary to this), and anything touching the Reporting
workspace, which still does not exist.
