"""Deploy one Fabric workspace's git-tracked items via fabric-cicd.

Reused for every workspace (ingestion, silver, gold, orchestration, reporting) — which
workspace and which items are decided entirely by the CLI arguments, so the same script
runs once per job in the GitHub Actions workflow.

Auth: service principal client-secret, read from AZURE_CLIENT_ID / AZURE_CLIENT_SECRET /
AZURE_TENANT_ID environment variables (set from GitHub Actions secrets).

Values injected into parameter.yml: any FABRIC_PARAM_<NAME> environment variable becomes a
`$ENV:<NAME>` token usable anywhere in parameter.yml — see inject_parameter_env_vars.
Used for connection IDs, which are the only values fabric-cicd can't resolve live (connections
are tenant objects, not Fabric items, so no $workspace/$items variable reaches them).
"""

import argparse
import os
import re
import sys
from pathlib import Path

import yaml
from azure.identity import ClientSecretCredential
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

# fabric-cicd scans os.environ for keys *literally* prefixed "$ENV:" and substitutes them into
# parameter.yml before parsing it. That prefix isn't a legal env var name in a GitHub Actions
# `env:` block, so workflows set conventional FABRIC_PARAM_<NAME> names and we re-export them
# under the name fabric-cicd expects.
WORKFLOW_PREFIX = "FABRIC_PARAM_"
FABRIC_CICD_PREFIX = "$ENV:"
TOKEN_PATTERN = r"\$ENV:([A-Za-z_][A-Za-z0-9_]*)"


def inject_parameter_env_vars() -> set[str]:
    """Re-export FABRIC_PARAM_<NAME> as '$ENV:<NAME>'. Returns the names made available.

    Blank values don't count as supplied: `${{ vars.FOO }}` interpolates to an empty string
    when the variable doesn't exist, so an unset GitHub variable arrives here as a defined-but-
    empty env var. Substituting that would blank the value out silently.
    """
    provided = set()
    for key, value in list(os.environ.items()):
        if key.startswith(WORKFLOW_PREFIX) and value.strip():
            name = key[len(WORKFLOW_PREFIX) :]
            os.environ[f"{FABRIC_CICD_PREFIX}{name}"] = value
            provided.add(name)
    return provided


def assert_all_tokens_resolvable(repository_directory: str, provided: set[str]) -> None:
    """Exit non-zero if parameter.yml references a $ENV: token nothing supplied.

    fabric-cicd leaves an unmatched token as literal text and still validates clean, so an
    unset variable would otherwise publish '$ENV:FOO' into the item definition as if it were
    a real value.
    """
    parameter_file = Path(repository_directory) / "parameter.yml"
    if not parameter_file.exists():
        return

    referenced = set(re.findall(TOKEN_PATTERN, parameter_file.read_text()))
    missing = sorted(referenced - provided)
    if missing:
        sys.exit(
            f"[error] {parameter_file} references {len(missing)} environment variable(s) that "
            f"were not supplied or were empty: {', '.join(missing)}\n"
            f"[error] Set them in the workflow as {WORKFLOW_PREFIX}<NAME> "
            f"(e.g. {WORKFLOW_PREFIX}{missing[0]}). An empty value usually means the GitHub "
            f"variable doesn't exist, or was created under Secrets instead of Variables."
        )


def assert_find_values_present(repository_directory: str) -> None:
    """Exit non-zero if a find_value string appears in no item file under the directory.

    fabric-cicd silently skips a rule whose find_value matches nothing — validation still
    returns True — so a stale or mistyped value ships the Dev GUID to the target environment
    on a green run. This is the only check that catches it, and it matters more now that
    find_value can come from an environment variable that may drift from what's in git.
    """
    root = Path(repository_directory)
    parameter_file = root / "parameter.yml"
    if not parameter_file.exists():
        return

    resolved = re.sub(
        TOKEN_PATTERN,
        lambda m: os.environ.get(f"{FABRIC_CICD_PREFIX}{m.group(1)}", m.group(0)),
        parameter_file.read_text(),
    )
    rules = (yaml.safe_load(resolved) or {}).get("find_replace") or []

    item_definitions = [
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*")
        if path.is_file() and path != parameter_file
    ]

    unmatched = sorted({
        rule["find_value"]
        for rule in rules
        if not any(rule["find_value"] in definition for definition in item_definitions)
    })
    if unmatched:
        sys.exit(
            f"[error] {len(unmatched)} find_value(s) in {parameter_file} match nothing under "
            f"{root}: {', '.join(unmatched)}\n"
            f"[error] fabric-cicd would skip these rules silently and publish the unreplaced "
            f"value. Check the GUID against the item definition, or against the environment "
            f"variable supplying it."
        )


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

    items_to_include = [i.strip() for i in args.items_to_include.split(",") if i.strip()] or None

    append_feature_flag("enable_environment_variable_replacement")
    if items_to_include:
        # Selective publish is experimental upstream: it can leave a dependency unpublished if
        # the list is incomplete. Safe here only because the split is along a real workspace
        # boundary and every item is published by exactly one job.
        append_feature_flag("enable_items_to_include")
    provided = inject_parameter_env_vars()
    assert_all_tokens_resolvable(args.repository_directory, provided)
    assert_find_values_present(args.repository_directory)

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
