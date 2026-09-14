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
import os
import pathlib
import sys
import time

import requests
from azure.identity import ClientSecretCredential

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
SEED_NOTEBOOK = "nb_seed_metadata"
ENV_MAP_DIRECTORY = pathlib.Path(__file__).resolve().parent.parent / "metadata" / "environments"

# Deduped means an identical run was already in flight; treat it as terminal rather than
# polling forever on a job this invocation does not own.
TERMINAL_STATES = {"Completed", "Failed", "Cancelled", "Deduped"}


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


def read_environment_map_b64(environment):
    """The whole <env>.yml, base64-encoded, to travel as one notebook parameter."""
    path = ENV_MAP_DIRECTORY / f"{environment.lower()}.yml"
    if not path.exists():
        sys.exit(
            f"[error] no environment map at {path}.\n"
            f"[error] Every environment needs one; it is the only file edited per environment."
        )
    return base64.b64encode(path.read_bytes()).decode("ascii")


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


def start_run(session, workspace_id, notebook_id, environment, env_map_b64):
    """Start the notebook and return the run-instance URL from the Location header."""
    response = session.post(
        f"{FABRIC_API}/workspaces/{workspace_id}/items/{notebook_id}/jobs/instances",
        params={"jobType": "RunNotebook"},
        json={
            "executionData": {
                "parameters": {
                    "environment": {"value": environment, "type": "string"},
                    "environment_map_b64": {"value": env_map_b64, "type": "string"},
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

    env_map_b64 = read_environment_map_b64(args.environment)
    session = session_for_service_principal()

    notebook_id = resolve_notebook_id(session, args.workspace_id)
    print(f"[info] seeding {args.environment} via {SEED_NOTEBOOK} ({notebook_id})")

    run_url = start_run(session, args.workspace_id, notebook_id, args.environment, env_map_b64)
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
