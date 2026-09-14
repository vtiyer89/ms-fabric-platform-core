"""Trigger nb_seed_metadata and wait for it, as the deploy service principal.

    python scripts/run_seed_metadata.py \
        --workspace-id <Test platform workspace> \
        --environment TEST

Resolves the notebook by display name, starts it through the Fabric Job Scheduler API with the
environment map passed inline as a base64 notebook parameter, then polls to completion and
exits non-zero if the run failed.

Why the map travels as a parameter rather than a file: a copy of <env>.yml sitting in the
lakehouse's Files/config/ is a second source of truth that anyone with workspace access can
edit, after which git no longer describes the environment. Passing it inline means there is no
upload step to forget and the run is self-describing in its own parameters.

Why this is a mandatory tail step of every deploy, not a runbook step: parameter.yml re-resolves
on every deploy automatically, but a metadata table does not. Deploy a workload and skip the
seed and the table still holds the previous run's GUIDs — pointing at items that may have been
deleted and recreated — with nothing anywhere saying so.
"""

import argparse
import base64
import hashlib
import os
import pathlib
import sys
import time

import requests
import yaml
from azure.identity import ClientSecretCredential

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
SEED_NOTEBOOK = "nb_seed_metadata"
ENV_MAP_DIRECTORY = pathlib.Path(__file__).resolve().parent.parent / "metadata" / "environments"

# Deduped means an identical run was already in flight; treat it as terminal rather than
# polling forever on a job this invocation does not own.
TERMINAL_STATES = {"Completed", "Failed", "Cancelled", "Deduped"}


def run_provenance():
    """Who/what/which-code produced this seed, from whichever CI is running.

    Without this the table records only that "ci" wrote a row at a timestamp, which cannot answer
    the question that actually matters after a bad deploy: which commit and which run put this
    GUID here. It is also the gap noted in the plan's risk table — reverting code does not revert
    the table, so the table has to say what code it came from.
    """
    github = os.environ.get("GITHUB_RUN_ID")
    ado = os.environ.get("BUILD_BUILDID")
    if github:
        return {
            "ci": "github-actions",
            "run_id": github,
            "git_commit": os.environ.get("GITHUB_SHA", ""),
            "source_ref": os.environ.get("GITHUB_REF_NAME", ""),
        }
    if ado:
        return {
            "ci": "azure-devops",
            "run_id": ado,
            "git_commit": os.environ.get("BUILD_SOURCEVERSION", ""),
            "source_ref": os.environ.get("BUILD_SOURCEBRANCHNAME", ""),
        }
    return {"ci": "local", "run_id": "", "git_commit": "", "source_ref": ""}


def session_for_service_principal():
    """Authenticated session, same three variables the deploy script uses."""
    missing = [
        name
        for name in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        sys.exit(f"[error] missing credential variable(s): {', '.join(missing)}")

    credential = ClientSecretCredential(
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        tenant_id=os.environ["AZURE_TENANT_ID"],
    )
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {credential.get_token(FABRIC_SCOPE).token}"})
    return session


def resolve_from_env(env_map):
    """Turn every connection's `from_env` into a literal before the map leaves this machine.

    The seeder runs inside Fabric, where the CI runner's environment does not exist — so a
    `from_env` reference is unresolvable by the time the notebook sees it. Resolving here keeps
    connection GUIDs out of git while still handing the notebook a fully-resolved map.

    Both spellings are accepted, matching resolve_bindings: the bare name and the
    FABRIC_PARAM_<NAME> form CI already sets for the old $ENV: tokens.
    """
    missing = []
    for name, spec in (env_map.get("connections") or {}).items():
        if "connection_id" in spec:
            continue
        variable = spec.get("from_env")
        if not variable:
            missing.append(f"connection {name!r}: neither connection_id nor from_env")
            continue
        value = (os.environ.get(variable) or os.environ.get(f"FABRIC_PARAM_{variable}") or "").strip()
        if not value:
            missing.append(f"connection {name!r}: neither {variable} nor FABRIC_PARAM_{variable} is set")
            continue
        spec.pop("from_env")
        spec["connection_id"] = value

    if missing:
        sys.exit(
            f"[error] {len(missing)} connection(s) could not be resolved:\n  "
            + "\n  ".join(missing)
            + "\n\n[error] These are the one class with no live resolution. Nothing has been "
              "seeded."
        )
    return env_map


def read_environment_map_b64(environment):
    """The whole <env>.yml, connections resolved, base64-encoded as one notebook parameter."""
    path = ENV_MAP_DIRECTORY / f"{environment.lower()}.yml"
    if not path.exists():
        sys.exit(
            f"[error] no environment map at {path}.\n"
            f"[error] Every environment needs one; it is the only file edited per environment."
        )
    env_map = resolve_from_env(yaml.safe_load(path.read_text()))
    rendered = yaml.safe_dump(env_map, sort_keys=False)
    return base64.b64encode(rendered.encode("utf-8")).decode("ascii"), rendered


def resolve_notebook_id(session, workspace_id):
    response = session.get(f"{FABRIC_API}/workspaces/{workspace_id}/items", timeout=60)
    if response.status_code == 403:
        sys.exit(
            f"[error] the deploy service principal cannot list items in workspace "
            f"{workspace_id}. Grant it Contributor on the platform workspace."
        )
    response.raise_for_status()

    match = next(
        (
            i
            for i in response.json().get("value", [])
            if i.get("displayName") == SEED_NOTEBOOK and i.get("type") == "Notebook"
        ),
        None,
    )
    if match is None:
        sys.exit(
            f"[error] {SEED_NOTEBOOK} is not in workspace {workspace_id}.\n"
            f"[error] Deploy ms-fabric-platform-core's platform/ directory before seeding."
        )
    return match["id"]


def start_run(session, workspace_id, notebook_id, environment, env_map_b64, allow_unresolved,
              provenance, map_checksum):
    """Start the notebook and return the run-instance URL from the Location header."""
    response = session.post(
        f"{FABRIC_API}/workspaces/{workspace_id}/items/{notebook_id}/jobs/instances",
        params={"jobType": "RunNotebook"},
        json={
            "executionData": {
                "parameters": {
                    "environment": {"value": environment, "type": "string"},
                    "environment_map_b64": {"value": env_map_b64, "type": "string"},
                    "allow_unresolved": {"value": str(allow_unresolved).lower(), "type": "string"},
                    "seed_ci": {"value": provenance["ci"], "type": "string"},
                    "seed_run_id": {"value": provenance["run_id"], "type": "string"},
                    "seed_git_commit": {"value": provenance["git_commit"], "type": "string"},
                    "seed_source_ref": {"value": provenance["source_ref"], "type": "string"},
                    "seed_map_checksum": {"value": map_checksum, "type": "string"},
                }
            }
        },
        timeout=60,
    )
    response.raise_for_status()

    location = response.headers.get("Location")
    if not location:
        sys.exit("[error] Fabric accepted the job but returned no Location header to poll.")
    return location


def wait_for(session, run_url, timeout_seconds, poll_seconds):
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        response = session.get(run_url, timeout=60)
        response.raise_for_status()
        body = response.json()
        status = body.get("status")

        if status != last:
            print(f"[info] {status}")
            last = status

        if status in TERMINAL_STATES:
            return body
        time.sleep(poll_seconds)

    sys.exit(
        f"[error] seed run did not reach a terminal state within {timeout_seconds}s. "
        f"Last status: {last}. The run may still be going; check it in the Fabric portal "
        f"before re-triggering."
    )


def main():
    parser = argparse.ArgumentParser(description="Trigger nb_seed_metadata and wait for it.")
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("FABRIC_PLATFORM_WORKSPACE_ID", ""),
        help="Platform workspace holding nb_seed_metadata. Defaults to FABRIC_PLATFORM_WORKSPACE_ID.",
    )
    parser.add_argument("--environment", required=True, help="Must match an environment map, e.g. TEST")
    parser.add_argument(
        "--allow-unresolved",
        action="store_true",
        help=(
            "Seed whatever resolves and report the rest, instead of refusing to write. Required "
            "for a per-repo seed against a partly-deployed environment; a full-estate seed "
            "should run without it."
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args()

    if not args.workspace_id.strip():
        sys.exit(
            "[error] no platform workspace ID.\n"
            "[error] This project assumes a platform workspace already exists — it is where\n"
            "[error] lh_platform_metadata and nb_seed_metadata live. Add its GUID as\n"
            "[error] FABRIC_PLATFORM_WORKSPACE_ID: a repo Variable for GitHub Actions, or a\n"
            "[error] variable in the fabric-cicd-common group for Azure DevOps. Pass\n"
            "[error] --workspace-id to override it for a local run."
        )

    env_map_b64, rendered_map = read_environment_map_b64(args.environment)
    session = session_for_service_principal()

    notebook_id = resolve_notebook_id(session, args.workspace_id)
    print(f"[info] seeding {args.environment} via {SEED_NOTEBOOK} ({notebook_id})")

    provenance = run_provenance()
    # Checksum of the RESOLVED map, so the row identifies the exact configuration that produced
    # it — including which connection values were injected, which the git SHA alone cannot say.
    map_checksum = hashlib.sha256(rendered_map.encode("utf-8")).hexdigest()[:16]
    print(f"[info] {provenance['ci']} run={provenance['run_id'] or '-'} "
          f"commit={provenance['git_commit'][:8] or '-'} map={map_checksum}")

    run_url = start_run(
        session, args.workspace_id, notebook_id, args.environment, env_map_b64,
        args.allow_unresolved, provenance, map_checksum,
    )
    result = wait_for(session, run_url, args.timeout_seconds, args.poll_seconds)

    if result.get("status") != "Completed":
        sys.exit(
            f"[error] seed run finished as {result.get('status')}. "
            f"{result.get('failureReason') or ''}\n"
            f"[error] The metadata table may be partially updated or unchanged — the seeder "
            f"validates every reference before writing, so an early failure writes nothing."
        )
    print(f"[info] seed complete for {args.environment}")


if __name__ == "__main__":
    main()
