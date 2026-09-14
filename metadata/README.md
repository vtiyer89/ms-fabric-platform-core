# Metadata store

Environment maps for the metadata-driven deployment framework. See
`docs/metadata-driven-deployment-plan.md` for why this exists and what it replaces.

## What is here

```
metadata/environments/<env>.yml     the ONLY hand-edited input per environment
platform/                           the Fabric items that hold and populate the tables
```

`<env>.yml` maps **logical names** to Fabric identity. A deploy-target environment maps by
**display name**, which `nb_seed_metadata` resolves through the Fabric REST API at seed time.
This preserves the property `parameter.yml` had — a workspace recreated under the same display
name self-heals on the next seed, with no file to edit.

**`dev.yml` is the exception and maps by GUID.** Dev display names were never recorded in these
repos; every Dev value here was harvested from the `DEV:` keys of the `parameter.yml` files this
framework replaces, and those only ever held GUIDs. Dev therefore does not self-heal — recreate a
Dev workspace and the file needs a hand edit. That is accepted because Dev is authored through
Fabric's own git integration and is never a deploy target.

`dev.yml` has a second job that no other map has: it is the **find-side of every substitution**.
`scripts/resolve_bindings.py` reads it to learn which Dev GUID each logical name currently carries
in the item definitions, then rewrites each to the target environment's GUID.

> If anyone is ever tempted to paste a GUID into `md_workspace` or `md_item` by hand, the
> framework has failed. The tables are generated, not authored. Connections are the one
> exception, and only until spike S4 settles — they carry `from_env`, naming the CI variable that
> supplies the value, which keeps the existing `FABRIC_PARAM_<NAME>` plumbing working unchanged.

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
| `md_seed_run` | seeder, **appended every run** | none — it is a log, not a state table |

### `md_seed_run` — what happened, not what is

The four tables above describe what the environment **is**, so the seeder merges and a row is
replaced. `md_seed_run` describes what **happened**, so every run appends and nothing is ever
overwritten. It records the CI system, run id, git commit, source ref, a checksum of the resolved
environment map, the counts written, and any names that did not resolve.

It exists because `updated_by = 'ci'` cannot answer the question that matters after a bad deploy:
*which commit and which run put this GUID here.* It is also the answer to the plan's standing
risk that reverting code does not revert the table — the table now says what code it came from.

The **checksum is of the resolved map**, after `from_env` connections have been substituted, so
it identifies the exact configuration used including injected connection values, which the git
SHA alone cannot.

`unresolved_count` is the column to watch. A partial seed is expected while the estate is only
partly deployed; a non-zero count after every repo has deployed means something is genuinely
missing, and this surfaces it without re-reading CI logs.

```sql
SELECT seeded_at, run_id, LEFT(git_commit, 8) AS commit, items_resolved, unresolved_count
FROM   lh_platform_metadata.dbo.md_seed_run
WHERE  environment = 'TEST'
ORDER  BY seeded_at DESC
```

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
4. Deploy the workload repos.
5. Run the seeder with `environment=<ENV>`.

**Ordering in step 4, accurately:** in v1 only the notebooks resolve at run time, so only their
cross-workspace reads are order-free — gold can deploy before silver. Pipelines, the CopyJob and
the semantic model still resolve at deploy time through `resolve_bindings.py`, which looks names
up live against the target environment, so those still need their upstream to exist. Deploy order
stops mattering entirely when the remaining item types move to runtime lookup, which is gated on
spikes S1 and S3.

## How the map reaches the seeder

`scripts/run_seed_metadata.py` passes the whole `<env>.yml` **inline, base64-encoded, as a
notebook parameter**. It is deliberately not uploaded to the lakehouse's `Files/config/`: a copy
there is a second source of truth that anyone with workspace access can edit, after which git no
longer describes the environment. Inline also means there is no upload step to forget, and a run
is self-describing in its own parameters.

The platform workspace ID is **not** in this file. It comes from `FABRIC_PLATFORM_WORKSPACE_ID`
(a repo Variable for GitHub Actions, a `fabric-cicd-common` variable for Azure DevOps).
`run_seed_metadata.py` fails with instructions when it is unset rather than guessing a default.

## Not built yet

- `verify_metadata_coverage` (Phase 5)
- **A Dev platform workspace.** None is known to exist. Dev needs one once the workload notebooks
  read the table at run time in Dev too, since Fabric git integration syncs those notebooks into
  Dev the moment they merge. Create it and supply its GUID, or Dev seeding stays unavailable and
  `dev.yml` serves only as the substitution find-side. Open question for whoever owns Dev.
- Connections resolved by display name (spike S4). Until then they stay `from_env`.
