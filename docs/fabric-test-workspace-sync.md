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
    scripts/debug_parameterization.py   # offline parameter.yml check, no credentials
    scripts/debug_live_test.py          # real publish as your own user, not the SPN
    scripts/requirements.txt
    scripts/requirements-dev.txt        # pytest, for tests/
    tests/                              # guards + checks over the real parameter.yml files
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

### Two separate things must be permitted

Calling the reusable workflow and reading this repo's files are different operations with
different failures. Both are needed, and fixing one doesn't fix the other.

**1. Resolving the workflow** — `ms-fabric-platform-core` → Settings → Actions → General →
*Access* → allow "Accessible from repositories in the `vtiyer89` organization" (or list the
four caller repos). Without it, callers fail with **"workflow was not found"** — the same
message GitHub gives for a typo'd path, so it's ambiguous by design.

**2. Checking this repo out onto the runner** — the reusable workflow runs
`actions/checkout` against this repo to get `scripts/deploy_fabric_item.py`. Actions loads the
workflow *YAML* for you but not the repo *contents*, so this checkout is a real clone. It
defaults to `GITHUB_TOKEN`, which is scoped to the **calling** repository and cannot read
another private repo — even in the same org. That fails with **"Repository not found"** at the
fetch step, after the workflow has already parsed and started.

Pick one fix for #2:

| Option | What to do | Trade-off |
|---|---|---|
| **Token** (default) | Create a fine-grained PAT with *Contents: Read* on `ms-fabric-platform-core`. Add it as an **org secret** named `PLATFORM_CORE_TOKEN`, scoped to the four caller repos. Callers already use `secrets: inherit`, so nothing else changes. | A second credential to rotate; PATs expire like the SPN secret |
| **Public repo** | Make `ms-fabric-platform-core` public. `GITHUB_TOKEN` reads public repos, and `PLATFORM_CORE_TOKEN` becomes unnecessary. | The docs here name the service principal and contain workspace, connection and capacity GUIDs. Those are identifiers, not credentials — but they describe the deployment's security posture, so scrub the docs first if that matters |

The workflow takes `PLATFORM_CORE_TOKEN` as an *optional* secret and falls back to
`github.token`, so the public route needs no workflow edit.

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
| Landing | `ws-test-landing-rjoose-v2` | `ms-fabric-ingestion` / `datasource_nyc_taxi` (landing items) |
| Bronze | `Test-bronze` | `ms-fabric-ingestion` / `datasource_nyc_taxi` (bronze items) |
| Silver | `ws-test-dd-sustainability-silver-v2` | `ms-fabric-dd-trip-data` / `silver` |
| Gold | `ws-test-dd-sustainability-gold-v2` | `ms-fabric-dd-trip-data` / `gold` |

**Landing and Bronze are separate workspaces**, and `ms-fabric-ingestion` deploys to both —
two jobs, `deploy-landing` then `deploy-bronze`. Both jobs point at the *same*
`repository-directory` (`datasource_nyc_taxi`) and use the `items-to-include` input to select
which items each publishes:

| Job | Workspace | Publishes |
|---|---|---|
| `deploy-landing` | `ws-test-landing-rjoose-v2` | `lh_landing_nyc_taxi`, `pl_landing_nyc_ingest` |
| `deploy-bronze` | `Test-bronze` | `lh_bronze_nyc_taxi`, `pl_bronze_nyc_taxi`, `landing_bronze_copy_job` |

Sharing one directory avoids physically splitting the folder tree, which would have disturbed
Dev's git integration. fabric-cicd resolves parameter rules only for items it actually
publishes, so the landing job never evaluates bronze's cross-workspace lookups.

The catch: fabric-cicd doesn't check that the two lists cover everything. An item named by no
job is silently never deployed, and one named by both is published to both workspaces. The
test suite in `tests/` asserts exact coverage — run it after adding any item to this repo.

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

Every grant that identity needs — tenant setting, workspace role, connection share — is
covered in [spn-permissions-process-doc.md](spn-permissions-process-doc.md), including an
error-to-missing-grant lookup table. Read it before debugging any permission failure below.

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

**Connections come from GitHub Variables.** They're tenant/workspace objects, not deployable
Fabric items, so there's no live-lookup variable for them — they're the only values here that
can't resolve themselves. Rather than hardcoding them, each `parameter.yml` references them as
`$ENV:<NAME>` tokens:

```yaml
- find_value: "$ENV:DEV_GOLD_CONNECTION_ID"
  replace_value:
      DEV: "$ENV:DEV_GOLD_CONNECTION_ID"
      TEST: "$ENV:TEST_GOLD_CONNECTION_ID"
```

The caller workflow passes them through the reusable workflow's `parameter-env-vars` input as
`FABRIC_PARAM_<NAME>=...`; the deploy script re-exports each one under the `$ENV:`-prefixed
name fabric-cicd scans for. Recreating a connection is then a Variables edit, not a commit.

Note the **DEV** side is a variable too, including in `find_value` — which is the string
matched against the Dev GUID baked into the item JSON. If that variable ever disagrees with
what's actually in git, fabric-cicd skips the rule silently and ships the Dev GUID to Test, so
the deploy script refuses to run when a `find_value` matches no item definition.

**Everything else (workspace IDs, lakehouse IDs, pipeline IDs) resolves live**, via
fabric-cicd's `$workspace.<name>.$id` and `$workspace.<name>.$items.<Type>.<Name>.$id`
variables, e.g.:

```yaml
TEST: "$workspace.ws-test-dd-sustainability-silver-v2.$items.Lakehouse.lh_silver_dd_trip_records.$id"
```

At deploy time, fabric-cicd looks up that item by name in that workspace via the Fabric API
and substitutes its current ID — nothing is pasted into git. This is why no manual ID-hunting
table lives here anymore: a GUID never needs to be copied out of the Fabric portal for these.

**The tradeoff**: the referenced item has to actually exist when the lookup runs, which makes
the deploy order load-bearing rather than just a good idea. Deploy in this sequence the first
time (and any time a referenced workspace/item was deleted and recreated):

1. `ms-fabric-ingestion` landing job (no cross-workspace lookups — self-contained)
2. `ms-fabric-ingestion` bronze job (the copy job's source looks up Landing's lakehouse —
   `needs: deploy-landing` already enforces this within the one workflow)
3. `ms-fabric-dd-trip-data` silver job (looks up **Bronze's** lakehouse)
4. `ms-fabric-dd-trip-data` gold job (looks up Silver's lakehouse — `needs: deploy-silver`
   already enforces this within the one workflow)
5. `ms-fabric-orchestration` (looks up pipelines in Landing, Bronze, Silver, and Gold)
6. `ms-fabric-dp-trip-report` (looks up Gold's lakehouse)

Get the order wrong and the failure is loud, not silent — fabric-cicd errors out on an
unresolvable `$workspace`/`$items` reference rather than deploying something broken.

Before running the real workflow, check each `parameter.yml` locally with
`scripts/debug_parameterization.py` — no credentials needed. It runs the same guards the
deploy runs, so a pass means CI won't refuse the run for a missing variable or a `find_value`
that matches nothing:

```bash
cd ms-fabric-platform-core
uv venv --python 3.11 .venv && source .venv/bin/activate   # fabric-cicd needs 3.10+
uv pip install -r scripts/requirements.txt

FABRIC_PARAM_DEV_SILVER_CONNECTION_ID=... FABRIC_PARAM_TEST_SILVER_CONNECTION_ID=... \
python scripts/debug_parameterization.py \
    --repository-directory ../ms-fabric-dd-trip-data/silver \
    --items-in-scope Lakehouse,DataPipeline,Notebook
```

It does **not** resolve `$workspace`/`$items` — those need a live, credentialed run. For that,
`scripts/debug_live_test.py` publishes for real, authenticated as you rather than the SPN.

## 5. Set up GitHub

The service principal credentials are identical across all four caller repos, so set them
**once at the organization level** rather than duplicating them per repo. Workspace ID
variables differ per repo (per workflow, actually — `ms-fabric-dd-trip-data` needs two), so
those stay at the repo level.

### Secrets (the shared identity)

> **`vtiyer89` is a personal account, not a GitHub organization.** Organization secrets are an
> org-only feature, so there is no account-level secret store here — every secret must be added
> to **each caller repo individually**, and rotated in each. Any instruction elsewhere to create
> an "organization secret scoped to the four repos" does not apply to this account.

In each of the four caller repos: **Settings → Secrets and variables → Actions → New repository
secret**.

| Secret | Value | Where |
|---|---|---|
| `AZURE_CLIENT_ID` | the service principal's Application (client) ID | all 4 caller repos |
| `AZURE_CLIENT_SECRET` | the client secret value from step 1 | all 4 caller repos |
| `AZURE_TENANT_ID` | your Entra tenant ID | all 4 caller repos |
| `PLATFORM_CORE_TOKEN` | PAT with *Contents: Read* on `ms-fabric-platform-core` | all 4 caller repos — **only if platform-core stays private** |

That last row is the argument for making `ms-fabric-platform-core` public: it removes a
credential that would otherwise be duplicated across four repos and expire on its own schedule.

`ms-fabric-platform-core` itself needs no secrets — they flow from the *calling* repo through
`secrets: inherit`, not from the repo that defines the reusable workflow.

### Repo-level variables (per workspace)

In each item repo — **Settings → Secrets and variables → Actions → Variables** (repo-level,
not environment-scoped, since each caller workflow reads `vars.*` directly without declaring
a GitHub Environment):

| Repo | Variable | Value |
|---|---|---|
| `ms-fabric-ingestion` | `TEST_LANDING_WORKSPACE_ID` | Test Landing workspace ID |
| ″ | `TEST_BRONZE_WORKSPACE_ID` | Test Bronze workspace ID |
| `ms-fabric-dd-trip-data` | `TEST_SILVER_WORKSPACE_ID` | Test Silver workspace ID |
| ″ | `TEST_GOLD_WORKSPACE_ID` | Test Gold workspace ID |
| `ms-fabric-orchestration` | `TEST_WORKSPACE_ID` | Test Orchestration workspace ID |
| `ms-fabric-dp-trip-report` | `TEST_WORKSPACE_ID` | Test Reporting workspace ID |

Plus the connection IDs from step 3, one Dev and one Test per connection. These are
**Variables, not Secrets** — they're interpolated into a workflow input and appear in run logs:

| Repo | Variable | Value |
|---|---|---|
| `ms-fabric-ingestion` | `DEV_COPY_JOB_CONNECTION_ID` | Dev copy-job connection ID |
| ″ | `TEST_COPY_JOB_CONNECTION_ID` | Test copy-job connection ID |
| `ms-fabric-dd-trip-data` | `DEV_SILVER_CONNECTION_ID` | Dev silver notebook connection ID |
| ″ | `TEST_SILVER_CONNECTION_ID` | Test silver notebook connection ID |
| ″ | `DEV_GOLD_CONNECTION_ID` | Dev gold notebook connection ID |
| ″ | `TEST_GOLD_CONNECTION_ID` | Test gold notebook connection ID |
| `ms-fabric-orchestration` | `DEV_PIPELINE_INVOKE_CONNECTION_ID` | Dev pipeline-invoke connection ID |
| ″ | `TEST_PIPELINE_INVOKE_CONNECTION_ID` | Test pipeline-invoke connection ID |

`ms-fabric-dp-trip-report` needs none — the semantic model uses no connection.

A missing or empty one stops the deploy with a named error rather than publishing a broken
value, so a forgotten variable fails loudly instead of silently.

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
