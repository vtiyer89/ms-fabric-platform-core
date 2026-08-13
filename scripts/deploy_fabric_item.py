"""Deploy one Fabric workspace's git-tracked items via fabric-cicd.

Reused for every workspace (ingestion, silver, gold, orchestration, reporting) — which
workspace and which items are decided entirely by the CLI arguments, so the same script
runs once per job in the GitHub Actions workflow.

Auth: service principal client-secret, read from AZURE_CLIENT_ID / AZURE_CLIENT_SECRET /
AZURE_TENANT_ID environment variables (set from GitHub Actions secrets).
"""

import argparse
import os
import sys

from azure.identity import ClientSecretCredential
from fabric_cicd import FabricWorkspace, change_log_level, publish_all_items, unpublish_all_orphan_items

sys.stdout.reconfigure(line_buffering=True, write_through=True)
sys.stderr.reconfigure(line_buffering=True, write_through=True)

if os.getenv("RUNNER_DEBUG") == "1":
    change_log_level("DEBUG")


def main():
    parser = argparse.ArgumentParser(description="Deploy a Fabric workspace's items via fabric-cicd.")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--environment", required=True, help="Must match a key in parameter.yml, e.g. TEST")
    parser.add_argument("--repository-directory", required=True)
    parser.add_argument("--items-in-scope", required=True, help="Comma-separated Fabric item types")
    args = parser.parse_args()

    token_credential = ClientSecretCredential(
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        tenant_id=os.environ["AZURE_TENANT_ID"],
    )

    target_workspace = FabricWorkspace(
        workspace_id=args.workspace_id,
        environment=args.environment,
        repository_directory=args.repository_directory,
        item_type_in_scope=args.items_in_scope.split(","),
        token_credential=token_credential,
    )

    publish_all_items(target_workspace)
    unpublish_all_orphan_items(target_workspace)


if __name__ == "__main__":
    main()
