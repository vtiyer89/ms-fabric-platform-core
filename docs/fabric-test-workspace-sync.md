# Syncing to Fabric Test Workspaces via GitHub Actions

This describes how to stand up a **Test** deployment path alongside the existing Dev flow.
Dev keeps working exactly as it does today (Fabric's native git integration, live-syncing
each workspace to its connected git folder/branch). Test is a separate, explicit path: GitHub
Actions workflows that use the [`fabric-cicd`](https://microsoft.github.io/fabric-cicd/)
Python library to publish the same git-tracked items into a second set of workspaces, with
environment-specific IDs swapped in via `parameter.yml`.

Nothing here touches the Dev git connections. Test deploys run only when you trigger them.

Once you've deployed, use
[fabric-test-parity-checklist.md](fabric-test-parity-checklist.md) to verify Test actually
behaves like Dev — this doc only covers getting the deploy mechanics working, not confirming
the result is correct.

## Repo layout

Five separate GitHub repos under `vtiyer89`, one per Fabric workspace grouping:

| Repo | Fabric workspace(s) it deploys | Deploy trigger lives at |
|---|---|---|
| `ms-fabric-ingestion` | Ingestion (landing + bronze) | `.github/workflows/deploy-test.yml` |
| `ms-fabric-dd-trip-data` | Silver **and** Gold (two workspaces, two jobs) | `.github/workflows/deploy-test.yml` |
| `ms-fabric-orchestration` | Orchestration | `.github/workflows/deploy-test.yml` |
| `ms-fabric-dp-trip-report` | Reporting | `.github/workflows/deploy-test.yml` |
| `ms-fabric-platform-core` | — (no items of its own yet) | hosts the *shared* deploy script + reusable workflow |

The shared deploy logic lives once, in `ms-fabric-platform-core`:

```
ms-fabric-platform-core/
    scripts/deploy_fabric_item.py       # fabric-cicd wrapper, generic across all workspaces
    scripts/requirements.txt
    .github/workflows/deploy-fabric-item.yml   # reusable workflow (workflow_call)
```

Each item repo's own workflow is a thin caller — it just supplies its workspace ID,
`repository-directory`, and item scope:

```
ms-fabric-ingestion/.github/workflows/deploy-test.yml
ms-fabric-dd-trip-data/.github/workflows/deploy-test.yml      # 2 jobs: silver, gold
ms-fabric-orchestration/.github/workflows/deploy-test.yml
ms-fabric-dp-trip-report/.github/workflows/deploy-test.yml
```

`parameter.yml` still lives inside each item repo, at the root of the folder fabric-cicd
treats as one workspace:

```
ms-fabric-ingestion/datasource_nyc_taxi/parameter.yml
ms-fabric-dd-trip-data/silver/parameter.yml
ms-fabric-dd-trip-data/gold/parameter.yml
ms-fabric-orchestration/orchestration/parameter.yml
ms-fabric-dp-trip-report/semantic_models/taxi_trip/parameter.yml
```

## How a deploy runs, mechanically

1. You trigger an item repo's `deploy-test.yml` (manually, for now).
2. It calls `vtiyer89/ms-fabric-platform-core/.github/workflows/deploy-fabric-item.yml@main`
   with its inputs.
3. That reusable workflow checks out **two** repos onto the runner: the calling repo (into
   `item-repo/`, so its `parameter.yml` and item folders are present) and
   `ms-fabric-platform-core` (into `platform-core/`, for the shared script).
4. It runs `platform-core/scripts/deploy_fabric_item.py` against
   `item-repo/<repository-directory>`, authenticating as the shared service principal and
   publishing to the workspace ID passed in.

**One-time setting required in `ms-fabric-platform-core`**: Settings → Actions → General →
*Access* → allow "Accessible from repositories in the `vtiyer89` organization" (or list the
four caller repos explicitly). Without this, the other repos can't call its reusable workflow.

## Cross-repo ordering isn't automated

Within `ms-fabric-dd-trip-data`, gold's job `needs: deploy-silver` inside the same workflow
file, so that ordering is enforced. But *across* repos there's no `needs:` — GitHub Actions
can't express "wait for a workflow in a different repo" without extra plumbing
(`repository_dispatch` + a cross-repo PAT, or polling the other repo's run status via the API).

Deploy in this order manually for now:

1. `ms-fabric-ingestion`
2. `ms-fabric-dd-trip-data` (silver, then gold — handled automatically within the one workflow)
3. `ms-fabric-orchestration` (needs 1 and 2 already deployed — it invokes their pipelines)
4. `ms-fabric-dp-trip-report` (needs gold from step 2 — its semantic model reads the gold lakehouse)

If this becomes a frequent enough operation to be worth automating, the cleanest option is a
`repository_dispatch` fired from the end of each upstream workflow, or a small "deploy all"
workflow in `ms-fabric-platform-core` that uses the GitHub CLI/API to trigger each repo's
workflow in order and poll for completion.

## Topology: Test workspaces actually in use

Confirmed against the live tenant (this superseded an earlier guess built only from reading
the exported Dev JSON — see git history of this file if you want the original reasoning):

| Logical workspace | Test display name | Deployed from |
|---|---|---|
| Landing + Ingestion | `ws-test-landing-rjoose` | `ms-fabric-ingestion` / `datasource_nyc_taxi` |
| Silver | `ws-test-dd-sustainability-silver` | `ms-fabric-dd-trip-data` / `silver` |
| Gold | `ws-test-dd-sustainability-gold` | `ms-fabric-dd-trip-data` / `gold` |

**Landing and Bronze are genuinely separate workspaces in the live tenant** (`Test-bronze` and
`Test ingestion ws` both exist as their own workspace IDs), but by deliberate choice we deploy
`ms-fabric-ingestion`'s entire `datasource_nyc_taxi` folder — landing lakehouse, bronze
lakehouse, both pipelines, the copy job — as one unit into the Landing workspace
(`ws-test-landing-rjoose`) only. `Test-bronze` and `Test ingestion ws` sit unused. This keeps
the deploy mechanics simple (one job, one `repository_directory`, one `workspace-id`) at the
cost of Test's Ingestion layout not matching Dev's 1:1. If that divergence ever becomes a
problem, splitting `ms-fabric-ingestion`'s deploy into two jobs (mirroring how
`ms-fabric-dd-trip-data` already does Silver/Gold) is the fix — but it also requires either
physically splitting `datasource_nyc_taxi`'s folder tree by workspace, or fabric-cicd's
experimental `enable_items_to_include` selective-publish feature, since right now landing and
bronze items live intermixed under one `repository_directory`.

Every cross-workspace `parameter.yml` value now resolves **live** against this table via
fabric-cicd's `$workspace.<name>.$id` / `$workspace.<name>.$items.<Type>.<Name>.$id`
variables, instead of a manually pasted GUID — see "How the TEST values resolve" (step 4)
below.

## 1. Prerequisites

- **Fabric admin setting**: service principals must be allowed to call the Fabric APIs.
  Fabric Admin Portal → Tenant settings → *Developer settings* → enable
  "Service principals can use Fabric APIs". Ask your Fabric admin if you don't have access to
  this yourself.
- **One Microsoft Entra app registration (service principal)** for this deploy pipeline,
  shared across all four repos, e.g. `spn-fabric-test-deploy`. Note its **Application
  (client) ID**, **Directory (tenant) ID**, and create a **client secret** (note the secret
  *value* — it's only shown once).

## 2. Create the Test workspaces

Create five new Fabric workspaces (name them however fits your convention, e.g. prefixing
with `Test - `):

| Test workspace | Deployed from (repo / folder) |
|---|---|
| Test Ingestion | `ms-fabric-ingestion` / `datasource_nyc_taxi` |
| Test Silver | `ms-fabric-dd-trip-data` / `silver` |
| Test Gold | `ms-fabric-dd-trip-data` / `gold` |
| Test Orchestration | `ms-fabric-orchestration` / `orchestration` |
| Test Reporting | `ms-fabric-dp-trip-report` / `semantic_models/taxi_trip` |

For each one: Workspace settings → **Manage access** → add the service principal
(`spn-fabric-test-deploy`) as **Contributor**. Do **not** connect these workspaces to git —
fabric-cicd publishes to them directly over the API.

Assign each workspace to a Fabric capacity (Trial/F-SKU) — items won't deploy to a
capacity-less workspace.

## 3. Create the Fabric connections

Three pipelines call other items (`InvokeCopyJob`, `TridentNotebook`,
`InvokeFabricPipeline`) using a Fabric **connection** resource for auth — these are
tenant/workspace objects, not git items, so they must be created manually.

| Connection needed for | Repo / workspace |
|---|---|
| `pl_bronze_nyc_taxi` → InvokeCopyJob | `ms-fabric-ingestion` / Test Ingestion |
| `invoke_silver_transform` → TridentNotebook | `ms-fabric-dd-trip-data` / Test Silver |
| `invoke_gold_aggregate` → TridentNotebook | `ms-fabric-dd-trip-data` / Test Gold |
| `pl_orch_trips` → InvokeFabricPipeline (all 4 activities, shared) | `ms-fabric-orchestration` / Test Orchestration |

These aren't data-source connections — they're the **"run as" identity** a pipeline activity
uses when it calls another item, configurable with one of three auth kinds:
**Organizational account**, **Service principal**, or **Workspace identity**.

**Pick an auth kind.** Organizational account ties the connection to a real signed-in
person's delegated token — not appropriate for an automated Test pipeline (expires, tied to
a human). Use one of:

- **Service principal** — reuse `spn-fabric-test-deploy` from step 1. It already has
  Contributor on every Test workspace (step 2), so no new grants needed. Simplest path,
  used below.
- **Workspace identity** — Fabric's own managed identity per workspace, no secrets to manage.
  Cleaner long-term, but needs enabling per workspace plus a Contributor grant from each
  *calling* workspace's identity into each *target* workspace (3 separate grants for
  Orchestration alone, since it calls into Ingestion/Silver/Gold). More setup up front.

**Mind the bootstrap order.** These connections are created *inline from an activity's
Settings tab* — there's no populated pipeline to open one from until fabric-cicd has
deployed it, but fabric-cicd will reject the literal placeholder text
(`<TEST-connection-id-...>`) in `parameter.yml` since it isn't a valid GUID. So create all
four connections **before** the first real Test deploy, using a throwaway pipeline per
workspace, then delete it — the connection itself is a separate tenant object and survives.

### Worked example: the Copy Job connection (Test Ingestion)

1. Open the **Test Ingestion** workspace → **+ New item** → **Data pipeline** → name it
   `scratch-connection-setup`.
2. In the pipeline canvas, search the **Activities** pane for **Copy job** and add it.
3. Select the activity → **Settings** tab → under **Connection**, choose **Browse all** →
   **Get data** → select **Copy job** as the connector.
4. In the connection setup dialog: name it (e.g. `spn-fabric-test-deploy — copy job`), set
   **Authentication kind** to **Service principal**, and fill in the SPN's client ID / client
   secret / tenant ID (same values as the `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` /
   `AZURE_TENANT_ID` GitHub secrets from step 5).
5. Create it. Its ID appears in the connection's settings page URL, or via **Manage
   connections and gateways** (gear icon → find it by the name from step 4).
6. Paste that ID into `ms-fabric-ingestion/datasource_nyc_taxi/parameter.yml`'s connection
   rule, as the `TEST:` value (already done for the current connection — this is the
   procedure to repeat if it's ever deleted and recreated).
7. Delete `scratch-connection-setup`.

### The other three

Same steps, different workspace and activity type:

| # | Test workspace | Activity to add | Search term in Activities pane | `parameter.yml` rule to update |
|---|---|---|---|---|
| 2 | Test Silver | Notebook activity | `Notebook` | `ms-fabric-dd-trip-data/silver/parameter.yml` — connection for `invoke_silver_transform` |
| 3 | Test Gold | Notebook activity | `Notebook` | `ms-fabric-dd-trip-data/gold/parameter.yml` — connection for `invoke_gold_aggregate` |
| 4 | Test Orchestration | Invoke pipeline activity | `Invoke pipeline` | `ms-fabric-orchestration/orchestration/parameter.yml` — connection for `pl_orch_trips` (used by all 4 rules in that file — same ID everywhere) |

For #4, in the activity's Settings tab: **Type** → **Fabric**, then **Connection** →
**New connection**, same **Service principal** setup as above. You don't need to pick a
target pipeline/workspace on this scratch activity — you're only here to create the
connection object.

(Connection IDs are also retrievable via `GET /v1/connections` in the [Fabric REST
API](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections), if you'd rather
script this than click through the portal each time.)

## 4. How the TEST values resolve

All five `parameter.yml` files are filled in — no more `<TEST-...>` placeholders. Two
different mechanisms are in play, depending on whether the value is a Fabric item or not:

**Connections are static.** They're tenant/workspace objects, not deployable Fabric items, so
there's no live-lookup variable for them. The four IDs from step 3 are pasted directly into
each file's `TEST:` key and only need updating again if a connection is deleted/recreated.

**Everything else (workspace IDs, lakehouse IDs, pipeline IDs) resolves live**, via
fabric-cicd's `$workspace.<name>.$id` and `$workspace.<name>.$items.<Type>.<Name>.$id`
variables, e.g.:

```yaml
TEST: "$workspace.ws-test-dd-sustainability-silver.$items.Lakehouse.lh_silver_dd_trip_records.$id"
```

At deploy time, fabric-cicd looks up that item by name in that workspace via the Fabric API
and substitutes its current ID — nothing is pasted into git. This is why no manual ID-hunting
table lives here anymore: a GUID never needs to be copied out of the Fabric portal for these.

**The tradeoff**: the referenced item has to actually exist when the lookup runs, which makes
the deploy order load-bearing rather than just a good idea. Deploy in this sequence the first
time (and any time a referenced workspace/item was deleted and recreated):

1. `ms-fabric-ingestion` (no cross-workspace lookups — self-contained, deploys first)
2. `ms-fabric-dd-trip-data` silver job (looks up Ingestion's bronze lakehouse)
3. `ms-fabric-dd-trip-data` gold job (looks up Silver's lakehouse — `needs: deploy-silver`
   already enforces this within the one workflow)
4. `ms-fabric-orchestration` (looks up pipelines in Ingestion, Silver, and Gold)
5. `ms-fabric-dp-trip-report` (looks up Gold's lakehouse)

Get the order wrong and the failure is loud, not silent — fabric-cicd errors out on an
unresolvable `$workspace`/`$items` reference rather than deploying something broken.

Before running the real workflow, fabric-cicd ships a local validation script for exactly
this — checking a `parameter.yml` file's structure without deploying anything. It lives at
`devtools/debug_parameterization.py` in the [fabric-cicd
repo](https://github.com/microsoft/fabric-cicd); pull that file (matching the version pinned
in `ms-fabric-platform-core/scripts/requirements.txt`) and run it against each `parameter.yml`
before the first real deploy.

## 5. Set up GitHub

The service principal credentials are identical across all four caller repos, so set them
**once at the organization level** rather than duplicating them per repo. Workspace ID
variables differ per repo (per workflow, actually — `ms-fabric-dd-trip-data` needs two), so
those stay at the repo level.

### Org-level secrets (shared identity)

GitHub → your org (`vtiyer89`) → **Settings → Secrets and variables → Actions → New
organization secret**. Set **Repository access** to the four caller repos (`ms-fabric-ingestion`,
`ms-fabric-dd-trip-data`, `ms-fabric-orchestration`, `ms-fabric-dp-trip-report`) — no need to
grant `ms-fabric-platform-core` access itself, since secrets flow through the *calling* repo's
`secrets: inherit`, not the reusable workflow's own repo.

| Org secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | the service principal's Application (client) ID |
| `AZURE_CLIENT_SECRET` | the client secret value from step 1 |
| `AZURE_TENANT_ID` | your Entra tenant ID |

### Repo-level variables (per workspace)

In each item repo — **Settings → Secrets and variables → Actions → Variables** (repo-level,
not environment-scoped, since each caller workflow reads `vars.*` directly without declaring
a GitHub Environment):

| Repo | Variable | Value |
|---|---|---|
| `ms-fabric-ingestion` | `TEST_WORKSPACE_ID` | Test Ingestion workspace ID |
| `ms-fabric-dd-trip-data` | `TEST_SILVER_WORKSPACE_ID` | Test Silver workspace ID |
| ″ | `TEST_GOLD_WORKSPACE_ID` | Test Gold workspace ID |
| `ms-fabric-orchestration` | `TEST_WORKSPACE_ID` | Test Orchestration workspace ID |
| `ms-fabric-dp-trip-report` | `TEST_WORKSPACE_ID` | Test Reporting workspace ID |

If you'd rather gate Test deploys behind manual approval, use a GitHub **Environment** named
`test` in each caller repo instead (Settings → Environments), move these variables and
`secrets: inherit`'s source there, and add required reviewers under *Deployment protection
rules*. The workflows as written don't declare `environment:`, so add
`environment: test` to each caller job if you go this route.

## 6. Run it

In each repo's **Actions** tab, run the corresponding workflow manually
(`workflow_dispatch`), in the order from "Cross-repo ordering isn't automated" above.

If a job fails on a specific item, the fabric-cicd log (visible in the called reusable
workflow's run, nested under the caller repo's Actions run) names the item and the error —
most first-run failures are one of: an unresolvable `$workspace`/`$items` reference (deployed
out of order, or a workspace/item name typo'd relative to the "Topology" table above), a
connection ID that's stale or wasn't actually saved, or a permissions gap (the service
principal isn't a Contributor on that workspace yet).

## 7. Ongoing maintenance

- **New notebook/pipeline that references another workspace's item**: add a `find_replace`
  entry for it in the relevant `parameter.yml`, following the existing pattern — same-workspace
  references use `$workspace.$id` / `$items.<Type>.<Name>.$id` under `_ALL_`; cross-workspace
  references use `$workspace.<name>.$id` / `$workspace.<name>.$items.<Type>.<Name>.$id` under
  `TEST:`. Neither ever needs a hand-pasted GUID — only a new *connection* does (see step 3).
- **A Test workspace gets deleted and recreated**: mostly self-heals. As long as the
  recreated workspace keeps the **same display name**, every `$workspace.<name>...` lookup
  in the "Topology" table above resolves to the new GUID automatically on the next deploy —
  nothing to edit. Only two things break this: a connection tied to that workspace (connections
  are static — recreate it per step 3, and re-paste its ID) or the workspace getting a
  **different** display name (update the "Topology" table and every `$workspace.<old-name>...`
  reference that pointed at it).
- **Changing the shared deploy script**: edits to
  `ms-fabric-platform-core/scripts/deploy_fabric_item.py` on `main` take effect for every
  caller repo on their next run (they check out `platform-core-ref: main` by default). Pin a
  caller to a specific `ms-fabric-platform-core` tag via the `platform-core-ref` input if you
  need to decouple a repo from `main`'s latest changes.
- **This currently deploys manually via `workflow_dispatch`.** Once you're comfortable with
  it, add a `push: branches: [test]` trigger to each caller workflow to auto-deploy on merge
  to a `test` branch — at that point you'd also want Dev's git connection scoped to a
  `dev`/`main` branch so the two don't fight over the same commits, and you'd want to solve
  the cross-repo ordering problem from the "Cross-repo ordering" section above so a push to
  `ms-fabric-ingestion`'s `test` branch doesn't race a stale `ms-fabric-orchestration` deploy.
