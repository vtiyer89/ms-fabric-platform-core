# `spn-fabric-test-deploy` — Permissions Process Doc

Everything the deploy service principal needs granted, why each grant exists, and what to do
when one is missing. Companion to [fabric-test-workspace-sync.md](fabric-test-workspace-sync.md)
(how the sync works) and [fabric-test-parity-checklist.md](fabric-test-parity-checklist.md)
(verifying the result).

`spn-fabric-test-deploy` is a single Microsoft Entra app registration shared by all four item
repos' GitHub Actions workflows. It authenticates with a client secret
(`ClientSecretCredential`) and calls only the **Fabric REST API** — never Azure Resource
Manager, never Microsoft Graph.

## The five permission surfaces

Permissions live in five independent places. Granting one does **not** imply any other, and
each fails with a different error. This is the single most common source of confusion here —
in particular, surfaces 3 and 4 are routinely mistaken for each other (see §4).

| # | Surface | Granted where | Current status |
|---|---|---|---|
| 1 | Entra app registration + secret | Entra ID → App registrations | ✅ Done |
| 2 | Fabric tenant setting for SPN API access | Fabric Admin Portal → Tenant settings | ✅ Done (assumed — deploys work) |
| 3 | Workspace role (per Test workspace) | Workspace → Manage access | ⚠️ Partial — see §3 |
| 4 | Connection share (per connection) | Manage connections and gateways → Manage users | ⚠️ 1 of 4 — see §4 |
| 5 | Capacity assignment (per workspace) | Workspace settings → License info | ⚠️ Unverified for v2 workspaces |

---

## 1. Entra: the identity itself

| Needed | Not needed |
|---|---|
| App registration exists (single tenant, no redirect URI) | **Any Azure RBAC role** — this identity never touches ARM |
| A client **secret** (the *Value*, not the Secret ID) | **Any Microsoft Graph API permission** — no directory reads |
| Client ID + Tenant ID recorded | Admin consent, delegated scopes, certificates |
| An enterprise application (service principal) object in the tenant | |

The app registration and the service principal are two different objects. `az ad app create`
makes the first; `az ad sp create --id <appId>` makes the second. Fabric grants attach to the
**service principal**, so if workspace access can't find the identity by name, the SP object
is usually what's missing.

```bash
az ad app create --display-name spn-fabric-test-deploy
az ad sp create --id <appId>
az ad app credential reset --id <appId> --years 1   # prints the secret Value once
```

The secret Value is shown once and is unrecoverable. Rotating it means updating the
`AZURE_CLIENT_SECRET` org secret in GitHub **and** re-entering credentials on all four Fabric
connections (§4), since those store the same secret independently.

## 2. Fabric tenant setting

Fabric Admin Portal → **Tenant settings** → *Developer settings*:

- ☑ **"Service principals can call Fabric public APIs"** — required. Without it, every API call
  401s regardless of workspace role.

Scope it to a security group (e.g. `sg-fabric-deploy-spns`) rather than the whole
organization, and put `spn-fabric-test-deploy` in that group. Group membership changes can take
up to ~1 hour to propagate to token claims.

**Not required for this setup:** *"Service principals can create workspaces, connections, and
deployment pipelines."* All five Test workspaces and all four connections are created by hand
by a human. Only turn this on if you later automate workspace creation.

## 3. Workspace role — **Contributor**

Fabric workspace → **Manage access** → Add people or groups → search
`spn-fabric-test-deploy` → role **Contributor**.

Contributor is the correct level, and Microsoft's own fabric-cicd guidance says "at least the
Contributor role on target Fabric workspaces." Empirically confirmed here: the Ingestion
workspace deploy succeeds under Contributor.

**Why not less.** The deploy script calls `publish_all_items` *and*
`unpublish_all_orphan_items`, so it needs create, update, **and delete** on items. Per Fabric's
workspace role matrix, "Write or delete pipelines, notebooks, Spark job definitions… and
eventstreams" is Admin/Member/Contributor only — Viewer can read but not write, so Viewer
fails immediately.

**Why not more.** Admin adds only capabilities this identity should never have: deleting the
workspace, managing access, creating a workspace identity, and connecting the workspace to git.
That last one matters — Test workspaces are deliberately **not** git-connected, and an
over-privileged deploy identity could silently create the exact coupling this design avoids.
Member adds the ability to grant others access, also unnecessary.

> You may find blog posts claiming Contributor is insufficient and Admin is required for
> item-level APIs. That contradicts both Microsoft's documentation and this project's working
> Ingestion deploy. If a *specific item type* genuinely fails under Contributor, escalate that
> one workspace to Admin and record which item type forced it — don't escalate all five
> pre-emptively.

### Where it's needed

| Test workspace | Why | Status |
|---|---|---|
| `ws-test-landing-rjoose-v2` | deploy target | ✅ granted, proven in CI |
| `ws-test-dd-sustainability-silver-v2` | deploy target | ❓ **re-grant needed** — workspace was recreated; v1's grant did not carry over |
| `ws-test-dd-sustainability-gold-v2` | deploy target | ❓ **re-grant needed** — same |
| Orchestration (`860d3864-…`) | deploy target **and** runtime invoke source | ❓ unverified |
| Reporting | deploy target | ⛔ workspace doesn't exist yet |

Roles do not survive workspace deletion. Any time a workspace is recreated, re-add the SPN.

### Cross-workspace read access

Deploying Silver runs a live lookup into the *Ingestion* workspace to resolve
`$workspace.ws-test-landing-rjoose-v2.$items.Lakehouse.lh_bronze_nyc_taxi.$id`. That lookup
requires the SPN to have a role on the workspace being **read**, not just the one being
written. Contributor on all five satisfies this — no extra grant — but it's the reason a
lookup can fail with a permissions error rather than a "not found" error.

| Deploying… | Also reads from | Needs a role there |
|---|---|---|
| Silver | Ingestion | ✅ |
| Gold | Silver | ✅ |
| Orchestration | Ingestion, Silver, Gold | ✅ (all three) |
| Reporting | Gold | ✅ |

## 4. Connection permissions — the two-hat distinction

**This is the grant people miss.** A Fabric connection's *Authentication kind* and its *access
list* are unrelated settings that both mention the service principal, which is exactly why
they get conflated.

| | What it controls | Where |
|---|---|---|
| **Authentication kind = Service principal** | Which identity the connection *presents downstream* when the activity runs | Inside the connection's own credential config |
| **Access list (share)** | Who is allowed to *reference the connection object* when publishing or editing an item | Manage connections and gateways → select connection → **Manage users** |

Setting the first does **not** grant the second. A connection configured to authenticate *as*
`spn-fabric-test-deploy` is still invisible to `spn-fabric-test-deploy` until it is explicitly
shared with it — a connection only appears to identities on its access list.

### Granting it

Settings (gear) → **Manage connections and gateways** → **Connections** tab → find the
connection → **Manage users** → add `spn-fabric-test-deploy` → role **User** → **Share**.

Connection roles are **User**, **User with resharing**, and **Owner**. **User** is sufficient —
it permits referencing the connection in an item. `User with resharing` and `Owner` add
sharing and lifecycle rights this identity doesn't need.

Each connection has its own access list. There is no inheritance from the workspace, so this
is four separate grants.

| Connection | ID | Workspace | Shared to SPN? |
|---|---|---|---|
| Copy job invoke | `070f434a-cac1-40a0-97e4-c85a7a288233` | Landing/Ingestion | ✅ done, proven in CI |
| Silver notebook invoke | `408c5625-557d-4df3-b5e0-4c21fc962a5d` | Silver | ❌ **not yet** |
| Gold notebook invoke | `5824ee14-160d-4783-8f4d-04f5efb24bcc` | Gold | ❌ **not yet** |
| Pipeline invoke (all 4 activities) | `d191c2b5-0b39-4854-ade9-4ad2b5f876ca` | Orchestration | ❌ **not yet** |

Expect `User does not have access to the connection used in the Pipeline` on the first Silver,
Gold, and Orchestration deploy until these three are shared.

> Connections are **tenant** objects, not workspace items — they survive deletion of the
> workspace they were created from. The silver/gold connections were created inside the v1
> workspaces; the IDs above should still be valid, but confirm with
> `GET https://api.fabric.microsoft.com/v1/connections` before assuming a failure is a
> parameterization bug.

## 5. Capacity

Every Test workspace must be assigned to a Fabric capacity (Trial or F-SKU) — items won't
deploy into a capacity-less workspace. This is a property of the workspace, not a grant to the
SPN: **the service principal needs no capacity-level permission** for the current scope, since
it never creates or reassigns workspaces.

Newly recreated workspaces (`-silver-v2`, `-gold-v2`) may have landed on a different capacity
than their predecessors, or none. Worth checking alongside the role re-grant.

## 6. Deploy-time vs runtime — the SPN wears two hats

Because all four connections use **Service principal** auth with these same credentials, the
SPN is not only the identity that *publishes* items — it's also the identity that *executes*
pipeline activities at runtime.

| Hat | When | Needs |
|---|---|---|
| Deployer | GitHub Actions run | Workspace Contributor + connection share |
| Runtime executor | A pipeline actually runs in Fabric | Permission on the **target item's** workspace |

Contributor covers "Execute or cancel execution of pipelines" and the notebook equivalent, so
Contributor across all five workspaces satisfies both hats. The case to watch is
`pl_orch_trips`: its four `InvokePipeline` activities reach from Orchestration into Ingestion,
Silver, and Gold. It can deploy green and still fail at *run* time if the SPN's role is missing
in a target workspace — a class of failure the parity checklist's structural checks won't
catch, only the end-to-end functional run will.

The **Reporting** workspace adds one more runtime wrinkle: a Direct Lake semantic model needs
its own read path into the Gold lakehouse, and Microsoft notes that the first deploy of a
semantic model with data sources requires configuring data source credentials manually in the
portal (workspace → semantic model → Settings → Data source credentials). Neither has been
exercised here — treat Reporting's permission story as unproven.

## 7. What the SPN must NOT have

- **No access on any Dev workspace.** Dev syncs through Fabric's native git integration and
  must stay untouched. Check each Dev workspace's Manage access list — this is step 4 of the
  parity checklist.
- **No Azure RBAC role assignment.** It never calls ARM. A role here is pure blast radius.
- **No tenant admin / Fabric administrator role.**
- **No Admin on Test workspaces** unless a specific item type provably requires it (§3).

## 8. Error → missing grant

| Error | Missing |
|---|---|
| `401` / `AADSTS700016` / invalid client on token acquisition | Bad or expired client secret, or wrong tenant ID (§1) |
| Auth succeeds, every Fabric call 401s | Tenant setting not enabled, or SPN not in the scoped security group (§2) |
| `The executing identity is not authorized to call GET on .../workspaces/<id>/folders` | No workspace role — **or the role is on the wrong workspace**; check the ID in the repo variable matches the intended workspace (§3) |
| `User does not have access to the connection used in the Pipeline` | Connection not shared to the SPN (§4) — *not* an auth-kind problem |
| Unresolvable `$workspace.<name>...` reference | Either deploy order (referenced item doesn't exist yet), a workspace-name typo, or no role on the workspace being **read** (§3) |
| Publish fails only on Lakehouse/warehouse-adjacent items | Workspace not on a capacity (§5) |
| Deploy green, pipeline fails at run time | Runtime hat — role missing in the *target* workspace (§6) |
| Semantic model deploys but won't open / no data | Data source credentials not configured, or Direct Lake read path into Gold (§6) |

## 9. Verifying a grant without a full deploy

Token acquisition and workspace visibility, checked directly:

```bash
TOKEN=$(curl -s -X POST \
  "https://login.microsoftonline.com/$AZURE_TENANT_ID/oauth2/v2.0/token" \
  -d "client_id=$AZURE_CLIENT_ID" \
  -d "client_secret=$AZURE_CLIENT_SECRET" \
  -d "scope=https://api.fabric.microsoft.com/.default" \
  -d "grant_type=client_credentials" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Surfaces 1 + 2: does it get a token and can it list anything?
curl -s -H "Authorization: Bearer $TOKEN" \
  https://api.fabric.microsoft.com/v1/workspaces | python3 -m json.tool

# Surface 3: is it on this specific workspace, and as what role?
curl -s -H "Authorization: Bearer $TOKEN" \
  https://api.fabric.microsoft.com/v1/workspaces/<workspace-id>/roleAssignments | python3 -m json.tool

# Surface 4: which connections can it actually see?
curl -s -H "Authorization: Bearer $TOKEN" \
  https://api.fabric.microsoft.com/v1/connections | python3 -m json.tool
```

The connections call is the useful one: a connection the SPN can't see **will not appear** in
that list at all. If one of the four IDs from §4 is missing from the output, that connection
isn't shared — confirmed before wasting a CI run on it.

Run these from a shell with the same three env vars the workflow uses. Don't paste the secret
into a command that lands in shell history.

## 10. Maintenance

- **Secret expiry.** The client secret expires with no warning and no auto-renewal — the first
  symptom is a deploy failing on auth. Put the expiry date in a calendar reminder. Rotation
  touches two places: the GitHub org secret and all four connections' stored credentials.
- **Workspace recreated.** Re-add the SPN as Contributor and re-check capacity assignment.
  Neither carries over. (The *name*-based `parameter.yml` lookups self-heal only if the display
  name is unchanged — see bug 7.6 in the context doc.)
- **Connection recreated.** Re-share to the SPN and update the static ID in the relevant
  `parameter.yml` — connection IDs are the only values in this system that can't resolve live.
- **New Test workspace.** Contributor for the SPN, capacity assignment, and *no* git connection.
- **Eliminating the secret entirely.** GitHub OIDC federated credentials would remove the
  stored secret and its expiry, but require swapping `ClientSecretCredential` in
  `scripts/deploy_fabric_item.py` for a federated-token flow. The four Fabric connections would
  still need their own credentials regardless — those are configured in Fabric, not in CI.

## References

- [Roles in workspaces in Microsoft Fabric](https://learn.microsoft.com/en-us/fabric/fundamentals/roles-workspaces) — the Admin/Member/Contributor/Viewer capability matrix
- [Data source management — Manage users](https://learn.microsoft.com/en-us/fabric/data-factory/data-source-management#manage-users) — connection roles (User / User with resharing / Owner)
- [Deploy Power BI projects (PBIP) using fabric-cicd](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-deploy-fabric-cicd) — "a service principal with at least the Contributor role on target Fabric workspaces"
- [Service principals can call Fabric public APIs](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer#service-principals-can-call-fabric-public-apis) — the tenant setting
- [Fabric Connections REST API](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections)
