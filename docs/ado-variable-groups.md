# Azure DevOps variable groups

Every variable group you need to create in **Pipelines → Library**, and what goes in each.

Five groups: one shared (`fabric-cicd-common`) linked by all four pipelines, plus one per item
repo. Each pipeline links exactly two — the common one and its own.

| Pipeline | Links |
|---|---|
| `ms-fabric-ingestion` | `fabric-cicd-common` + `fabric-ingestion` |
| `ms-fabric-dd-trip-data` | `fabric-cicd-common` + `fabric-dd-trip-data` |
| `ms-fabric-orchestration` | `fabric-cicd-common` + `fabric-orchestration` |
| `ms-fabric-dp-trip-report` | `fabric-cicd-common` + `fabric-dp-trip-report` |

---

## 1. `fabric-cicd-common`

Service principal credentials, plus the Test workspace display names that appear in more than
one repo's `parameter.yml`.

| Variable | Secret | Value | What it is |
|---|:--:|---|---|
| `AZURE_CLIENT_ID` | no | `<YOUR-SPN-APP-ID>` | Application (client) ID of `spn-fabric-test-deploy` |
| `AZURE_CLIENT_SECRET` | **yes** | `<YOUR-SPN-CLIENT-SECRET>` | The secret's **Value**, not the Secret ID. Shown once at creation and unrecoverable after. |
| `AZURE_TENANT_ID` | no | `<YOUR-TENANT-ID>` | Directory (tenant) ID |
| `TEST_LANDING_WS_NAME` | no | `<YOUR-TEST-LANDING-WS-NAME>` | Landing workspace **display name** |
| `TEST_BRONZE_WS_NAME` | no | `<YOUR-TEST-BRONZE-WS-NAME>` | Bronze workspace display name |
| `TEST_SILVER_WS_NAME` | no | `<YOUR-TEST-SILVER-WS-NAME>` | Silver workspace display name |
| `TEST_GOLD_WS_NAME` | no | `<YOUR-TEST-GOLD-WS-NAME>` | Gold workspace display name |

### About the four `*_WS_NAME` variables

These are the **single source of truth for the rename fan-out**, not something the pipeline
injects on its own. Each name is written literally into one or more `parameter.yml` files as a
`$workspace.<name>...` lookup, and a renamed workspace means editing every one of them — that
is bug 7.6, which has already happened twice. Keeping the canonical spelling here means the
rename is a one-line diff plus a known list of files to update (see
[ado-parameter-templating.md](ado-parameter-templating.md) for that list).

The names are matched **literally and case-sensitively** by the Fabric API.

> **Wiring them in automatically is possible but unverified.** You could reference them as
> `$workspace.$ENV:TEST_LANDING_WS_NAME.$id` and pass them through `parameterEnvVars`, which
> would remove the hand-editing entirely. Whether fabric-cicd substitutes an `$ENV:` token
> *embedded inside* a `$workspace.` expression — rather than as a whole value — is not
> something this project has tested. Given that every failure mode in this codebase has been
> silent, do not adopt it until you have confirmed it with
> `scripts/debug_parameterization.py` and seen the `Replacing ...` lines in a live run.

Neither the orchestration nor the reporting workspace name appears here, because nothing looks
*into* those workspaces — they are only ever deploy targets, addressed by ID.

---

## 2. `fabric-ingestion`

| Variable | Secret | Value | What it is |
|---|:--:|---|---|
| `TEST_LANDING_WORKSPACE_ID` | no | `<YOUR-TEST-LANDING-WORKSPACE-ID>` | GUID of the Landing workspace |
| `TEST_BRONZE_WORKSPACE_ID` | no | `<YOUR-TEST-BRONZE-WORKSPACE-ID>` | GUID of the Bronze workspace |
| `DEV_COPY_JOB_CONNECTION_ID` | no | `<YOUR-DEV-COPY-JOB-CONNECTION-ID>` | Dev connection GUID as it appears in `pl_bronze_nyc_taxi`'s JSON |
| `TEST_COPY_JOB_CONNECTION_ID` | no | `<YOUR-TEST-COPY-JOB-CONNECTION-ID>` | Test connection it is rewritten to |

## 3. `fabric-dd-trip-data`

| Variable | Secret | Value | What it is |
|---|:--:|---|---|
| `TEST_SILVER_WORKSPACE_ID` | no | `<YOUR-TEST-SILVER-WORKSPACE-ID>` | GUID of the Silver workspace |
| `TEST_GOLD_WORKSPACE_ID` | no | `<YOUR-TEST-GOLD-WORKSPACE-ID>` | GUID of the Gold workspace |
| `DEV_SILVER_CONNECTION_ID` | no | `<YOUR-DEV-SILVER-CONNECTION-ID>` | Dev connection in `invoke_silver_transform` |
| `TEST_SILVER_CONNECTION_ID` | no | `<YOUR-TEST-SILVER-CONNECTION-ID>` | Test replacement |
| `DEV_GOLD_CONNECTION_ID` | no | `<YOUR-DEV-GOLD-CONNECTION-ID>` | Dev connection in `invoke_gold_aggregate` |
| `TEST_GOLD_CONNECTION_ID` | no | `<YOUR-TEST-GOLD-CONNECTION-ID>` | Test replacement |

## 4. `fabric-orchestration`

| Variable | Secret | Value | What it is |
|---|:--:|---|---|
| `TEST_WORKSPACE_ID` | no | `<YOUR-TEST-ORCHESTRATION-WORKSPACE-ID>` | GUID of the Orchestration workspace |
| `DEV_PIPELINE_INVOKE_CONNECTION_ID` | no | `<YOUR-DEV-PIPELINE-INVOKE-CONNECTION-ID>` | Dev connection shared by all 4 InvokePipeline activities |
| `TEST_PIPELINE_INVOKE_CONNECTION_ID` | no | `<YOUR-TEST-PIPELINE-INVOKE-CONNECTION-ID>` | Test replacement |

## 5. `fabric-dp-trip-report`

| Variable | Secret | Value | What it is |
|---|:--:|---|---|
| `TEST_WORKSPACE_ID` | no | `<YOUR-TEST-REPORTING-WORKSPACE-ID>` | GUID of the Reporting workspace |

No connection variables — this repo's `parameter.yml` has no `$ENV:` tokens. Its only two rules
are cross-workspace lookups into Gold, which resolve live.

`fabric-orchestration` and `fabric-dp-trip-report` both use the name `TEST_WORKSPACE_ID`. That
is safe because each pipeline links only its own group; nothing merges them.

---

## Creating them

Portal: **Pipelines → Library → + Variable group**. Name it, add the variables, click the lock
icon on `AZURE_CLIENT_SECRET`, Save.

CLI:

```bash
az pipelines variable-group create \
  --name fabric-cicd-common \
  --authorize true \
  --variables \
      AZURE_CLIENT_ID='<YOUR-SPN-APP-ID>' \
      AZURE_TENANT_ID='<YOUR-TENANT-ID>' \
      TEST_LANDING_WS_NAME='<YOUR-TEST-LANDING-WS-NAME>' \
      TEST_BRONZE_WS_NAME='<YOUR-TEST-BRONZE-WS-NAME>' \
      TEST_SILVER_WS_NAME='<YOUR-TEST-SILVER-WS-NAME>' \
      TEST_GOLD_WS_NAME='<YOUR-TEST-GOLD-WS-NAME>'

# Secrets are added separately so the value never sits in --variables (and therefore in
# your shell history).
az pipelines variable-group variable create \
  --group-id <ID-FROM-ABOVE> \
  --name AZURE_CLIENT_SECRET --secret true --value '<YOUR-SPN-CLIENT-SECRET>'
```

## Three things that bite

**Secret variables are not automatically in the environment.** ADO maps normal variables into
each step's environment but deliberately withholds secrets. `AZURE_CLIENT_SECRET` is passed
explicitly in `pipelines/templates/deploy-fabric-item.yml`:

```yaml
env:
  AZURE_CLIENT_SECRET: $(AZURE_CLIENT_SECRET)
```

Delete that line and the deploy fails at authentication, not at parse time.

**Authorize the group for the pipeline.** A group created without `--authorize true`, or one
created before the pipeline existed, produces a run that fails immediately with a permission
error on the group. Fix it under the group's **Pipeline permissions**.

**An undefined variable stays a literal.** ADO leaves `$(FOO)` as the seven characters `$(FOO)`
when `FOO` is undefined — it does not blank it out the way GitHub Actions does. So a typo in a
workspace-ID variable reaches the deploy script as the string `$(TEST_SILVER_WORKSPACE_ID)`,
which `assert_workspace_id_supplied` rejects as "not a GUID". That guard is what turns a typo
into a clear failure instead of a confusing one.

---

## Appendix — values from the existing Test environment

Fill the placeholders above with these if you are pointing at the environment this project
already built, rather than standing up a fresh one. Connection IDs and the SPN secret are not
listed; get those from the Fabric portal and Entra respectively.

| Variable | Value |
|---|---|
| `TEST_LANDING_WS_NAME` | `ws-test-landing-rjoose-v2` |
| `TEST_BRONZE_WS_NAME` | `ws-test-bronze-rjoose-v2` |
| `TEST_SILVER_WS_NAME` | `ws-test-dd-sustainability-silver-v2` |
| `TEST_GOLD_WS_NAME` | `ws-test-dd-sustainability-gold-v2` |
| `TEST_LANDING_WORKSPACE_ID` | `e9472408-b9e1-45ae-8c2a-e22911c8b109` |
| `TEST_BRONZE_WORKSPACE_ID` | `95c0dc3f-a90a-48a6-b00d-c4b502521c2d` |
| `TEST_SILVER_WORKSPACE_ID` | `d76dab0a-ec8b-445c-aa8d-4c20332c6be5` |
| `TEST_GOLD_WORKSPACE_ID` | `4520a3ae-e2fa-4fe3-8108-6018274664ea` |
| `TEST_WORKSPACE_ID` (orchestration) | `27577d41-c3b4-473c-8ee4-0df4f9e05456` |
| `TEST_WORKSPACE_ID` (reporting) | workspace does not exist yet |
| `TEST_COPY_JOB_CONNECTION_ID` | `070f434a-cac1-40a0-97e4-c85a7a288233` |
| `TEST_SILVER_CONNECTION_ID` | `408c5625-557d-4df3-b5e0-4c21fc962a5d` |
| `TEST_GOLD_CONNECTION_ID` | `5824ee14-160d-4783-8f4d-04f5efb24bcc` |
| `TEST_PIPELINE_INVOKE_CONNECTION_ID` | `d191c2b5-0b39-4854-ade9-4ad2b5f876ca` |
