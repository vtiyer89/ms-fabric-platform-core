"""Locally validate a workspace's parameter.yml — no Fabric API calls, no credentials.

Confirms the file's structure is valid, that item_type/item_name filters match real files, and
that every find_value actually appears in an item definition. Does NOT resolve
$workspace./$items. dynamic variables — that needs a live, credentialed run (debug_live_test.py).

Runs the same guards the real deploy runs, so a pass here means the deploy won't be refused
for a missing variable or an orphaned find_value.

Usage, from the repo whose items you're checking:
    pip install -r requirements.txt
    python debug_parameterization.py --repository-directory silver \
        --items-in-scope Lakehouse,DataPipeline,Notebook

Supply connection IDs the same way CI does, as FABRIC_PARAM_<NAME>:
    FABRIC_PARAM_DEV_SILVER_CONNECTION_ID=... FABRIC_PARAM_TEST_SILVER_CONNECTION_ID=... \
        python debug_parameterization.py --repository-directory silver ...

Look for "[warn] Found the reserved environment key '_ALL_'" — its presence proves the
installed fabric-cicd actually understands _ALL_ (see bug 7.1 in the context doc).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_fabric_item import (  # noqa: E402
    assert_all_tokens_resolvable,
    assert_find_values_present,
    inject_parameter_env_vars,
)
from fabric_cicd import append_feature_flag, change_log_level  # noqa: E402
from fabric_cicd._parameter._utils import validate_parameter_file  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-directory", required=True)
    parser.add_argument("--items-in-scope", required=True, help="Comma-separated Fabric item types")
    parser.add_argument("--environment", default="TEST")
    parser.add_argument("--quiet", action="store_true", help="Skip the per-rule DEBUG log")
    args = parser.parse_args()

    if not args.quiet:
        # See every match/skip decision, not just pass/fail
        change_log_level("DEBUG")

    append_feature_flag("enable_environment_variable_replacement")
    provided = inject_parameter_env_vars()
    assert_all_tokens_resolvable(args.repository_directory, provided)
    assert_find_values_present(args.repository_directory)

    result = validate_parameter_file(
        repository_directory=args.repository_directory,
        item_type_in_scope=args.items_in_scope.split(","),
        environment=args.environment,
    )
    print(f"\nvalidate_parameter_file({args.repository_directory}) returned: {result}")
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
