# Metadata store

Environment maps for the metadata-driven deployment framework. See
`docs/metadata-driven-deployment-plan.md` for why this exists and what it replaces.

## What is here

```
metadata/environments/<env>.yml     the ONLY hand-edited input per environment
platform/                           the Fabric items that hold and populate the tables
```

`<env>.yml` maps **logical names** to Fabric **display names**. It contains no workspace or item
GUIDs by design: `nb_seed_metadata` resolves display names through the Fabric REST API at seed
time. This preserves the property `parameter.yml` has today — a workspace recreated under the
same display name self-heals on the next seed, with no file to edit.

> If anyone is ever tempted to paste a GUID into `md_workspace` or `md_item` by hand, the
> framework has failed. The tables are generated, not authored. Connections are the one
> exception, and only until spike S4 settles.

## Logical names are the contract

Workload code references logical names (`lh_bronze`, `pl_orch_trips`), never display names and
never GUIDs. They must be identical across environments and stable over time — renaming one is a
breaking change to every workload that reads it, with no compiler to catch it. Phase 5's
`verify_metadata_coverage` is what will catch it; until that exists, treat renames as carefully
as a schema change.

## Tables

| Table | Written by | Key |
|---|---|---|
| `md_workspace` | seeder (CI) | `logical_name` + `environment` |
| `md_item` | seeder (CI) | `logical_name` + `environment` |
| `md_connection` | seeder, passthrough from the env map | `logical_name` + `environment` |
| `md_config` | seeder, passthrough from the env map | `config_key` + `environment` |

`md_run_state` (watermarks, run history) is deliberately **not** here. It is high-write and must
not share a table with config that is read on every pipeline start.

## The ownership rule

Every row carries `owner`, either `ci` or `ops`.

- The seeder merges; it never truncates. It updates only rows where `owner = 'ci'`.
- An operator changing a value in place should set `owner = 'ops'`, which pins it — the next
  seed run leaves it alone.
- Every seed run prints a drift report of `ops`-owned rows.

Pinned rows are legitimate but they are the gap between what git says the environment is and what
it is. Reconcile them; do not let them become permanent.

## Adding an environment

1. Create the workspaces and grant the deploy SPN Contributor on each.
2. Copy `environments/test.yml` to `environments/<env>.yml` and update the display names.
3. Deploy `platform/` to that environment's platform workspace.
4. Deploy the workload repos — **in any order**.
5. Run the seeder with `environment=<ENV>`.

Step 4 is the point of the exercise. Deploy ordering is load-bearing today only because
`parameter.yml` resolves cross-workspace references at deploy time; runtime resolution removes
the constraint.

## Not built yet

- CI upload of `<env>.yml` into `Files/config/` and the job that triggers the seeder (increment 2)
- `pl_seed_metadata` pipeline wrapper
- The reader helper workloads use to consume the tables (Phase 2)
- `verify_metadata_coverage` (Phase 5)
- A `dev.yml` — Dev's workspace display names are not captured anywhere in these repos, and Dev
  needs its own platform workspace once Phase 2 lands, since the workload notebooks will read
  the table at run time in Dev too. Open question for whoever owns Dev.
