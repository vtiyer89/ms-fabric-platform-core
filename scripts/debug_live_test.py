"""Run a real deploy against Test authenticated as *you*, not the service principal.

For fast iteration without a GitHub Actions round-trip. Requires `az login` first, and at
least Contributor on the target workspace yourself.

CAVEAT: succeeding here does not prove the SPN can do the same. Workspace roles and connection
shares are granted separately — always confirm through the real workflow before calling
something done. See docs/spn-permissions-process-doc.md.

Usage:
    az login
    pip install -r requirements.txt
    FABRIC_PARAM_DEV_SILVER_CONNECTION_ID=... FABRIC_PARAM_TEST_SILVER_CONNECTION_ID=... \
        python debug_live_test.py \
            --workspace-id d76dab0a-ec8b-445c-aa8d-4c20332c6be5 \
            --repository-directory silver \
            --items-in-scope Lakehouse,DataPipeline,Notebook

Deliberately does not call unpublish_all_orphan_items — this is for iterating on find_replace
resolution, not full deploy parity. Use the real workflow for that.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure.identity import AzureCliCredential  # noqa: E402
from deploy_fabric_item import (  # noqa: E402
    assert_all_tokens_resolvable,
    assert_find_values_present,
    inject_parameter_env_vars,
)
from fabric_cicd import (  # noqa: E402
    FabricWorkspace,
    append_feature_flag,
    change_log_level,
    publish_all_items,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--repository-directory", required=True)
    parser.add_argument("--items-in-scope", required=True, help="Comma-separated Fabric item types")
    parser.add_argument("--environment", default="TEST")
    args = parser.parse_args()

    change_log_level("DEBUG")

    append_feature_flag("enable_environment_variable_replacement")
    provided = inject_parameter_env_vars()
    assert_all_tokens_resolvable(args.repository_directory, provided)
    assert_find_values_present(args.repository_directory)

    target_workspace = FabricWorkspace(
        workspace_id=args.workspace_id,
        environment=args.environment,
        repository_directory=args.repository_directory,
        item_type_in_scope=args.items_in_scope.split(","),
        token_credential=AzureCliCredential(),
    )

    publish_all_items(target_workspace)


if __name__ == "__main__":
    main()
