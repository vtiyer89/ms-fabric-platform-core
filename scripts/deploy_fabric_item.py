"""Deploy one Fabric workspace's git-tracked items via fabric-cicd.

Reused for every workspace (ingestion, silver, gold, orchestration, reporting) — which
workspace and which items are decided entirely by the CLI arguments, so the same script
runs once per job in the GitHub Actions workflow.

Auth: service principal client-secret, read from AZURE_CLIENT_ID / AZURE_CLIENT_SECRET /
AZURE_TENANT_ID environment variables (set from GitHub Actions secrets).

GUID substitution: handled by scripts/resolve_bindings.py before publish, driven by
metadata/environments/<env>.yml. There is no parameter.yml any more.
"""

import argparse
import os
import re
import sys

from azure.identity import ClientSecretCredential
import resolve_bindings
from fabric_cicd import (
    FabricWorkspace,
    append_feature_flag,
    change_log_level,
    publish_all_items,
    unpublish_all_orphan_items,
)

for _stream in (sys.stdout, sys.stderr):
    # Unbuffered output so CI logs interleave correctly. Guarded because a replaced stdout
    # (pytest capture, some CI wrappers) may not expose reconfigure.
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(line_buffering=True, write_through=True)

if os.getenv("RUNNER_DEBUG") == "1":
    change_log_level("DEBUG")

GUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def assert_workspace_id_supplied(workspace_id: str) -> None:
    """Exit non-zero if --workspace-id is blank or isn't a GUID.

    `${{ vars.FOO }}` interpolates to an empty string when the GitHub variable doesn't exist,
    and argparse's required=True is satisfied by "". fabric-cicd then fails with a generic
    "Either workspace_name or workspace_id must be specified", which doesn't say which variable
    is missing — that's bug 7.3, and renaming a workspace variable is the easiest way to
    recreate it.
    """
    if not workspace_id.strip():
        sys.exit(
            "[error] --workspace-id is empty. The GitHub variable behind it doesn't exist, is "
            "empty, or was created under Secrets instead of Variables.\n"
            "[error] Check the vars.* name in the caller workflow matches a repo Variable — a "
            "renamed workspace variable is the usual cause."
        )
    if not GUID_PATTERN.match(workspace_id.strip()):
        sys.exit(
            f"[error] --workspace-id {workspace_id!r} is not a GUID. Expected a workspace ID, "
            f"not a display name."
        )


def enable_selective_publish_flags() -> None:
    """Turn on both flags items_to_include needs.

    fabric-cicd gates selective publishing behind a specific flag AND the general experimental
    one, and rejects the run if either is missing:
    "Feature flags 'enable_experimental_features' and 'enable_items_to_include' must be set."

    It's experimental because an incomplete list can leave a dependency unpublished. Safe here
    only because the split follows a real workspace boundary and every item in the directory is
    published by exactly one caller job — which the test suite checks.
    """
    append_feature_flag("enable_experimental_features")
    append_feature_flag("enable_items_to_include")


def main():
    parser = argparse.ArgumentParser(description="Deploy a Fabric workspace's items via fabric-cicd.")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--environment", required=True, help="Must match a key in parameter.yml, e.g. TEST")
    parser.add_argument("--repository-directory", required=True)
    parser.add_argument("--items-in-scope", required=True, help="Comma-separated Fabric item types")
    parser.add_argument(
        "--items-to-include",
        default="",
        help=(
            "Optional comma-separated 'item_name.ItemType' list. When set, only these items are "
            "published, letting one repository_directory feed two workspaces (e.g. landing and "
            "bronze). Parameter rules are resolved only for published items, so the other "
            "layer's cross-workspace lookups aren't evaluated."
        ),
    )
    args = parser.parse_args()

    assert_workspace_id_supplied(args.workspace_id)
    items_to_include = [i.strip() for i in args.items_to_include.split(",") if i.strip()] or None

    if items_to_include:
        enable_selective_publish_flags()

    # Lakehouse shortcuts carry the md_* metadata tables into each workload lakehouse, and
    # fabric-cicd only opens shortcuts.metadata.json when this flag is set (_items/_lakehouse.py,
    # post_publish_all). Without it there is no shortcut, no log line and a green deploy — the
    # workload then runs against a metadata table it cannot see. Unlike enable_items_to_include
    # this does NOT require enable_experimental_features.
    append_feature_flag("enable_shortcut_publish")

    # Substitution happens HERE, before publish, and writes the item files in place on the
    # runner. This is what parameter.yml used to do; see scripts/resolve_bindings.py for why it
    # needs no per-rule configuration. It exits non-zero on an unresolved binding or an
    # unaccounted-for GUID, so nothing unparameterised reaches the API.
    resolve_bindings.resolve(args.repository_directory, args.environment)

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

    publish_all_items(target_workspace, items_to_include=items_to_include)
    # Orphan cleanup deliberately compares against every item in repository_directory, not just
    # the published subset — otherwise each job would treat the other layer's items as orphans
    # and delete them.
    unpublish_all_orphan_items(target_workspace)


if __name__ == "__main__":
    main()
