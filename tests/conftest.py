"""Shared fixtures for the deploy-script tests.

The module under test is a standalone script, not an installed package, so it's loaded by
path rather than imported by name.

Every guard in it reads and writes the process environment, so `isolated_env` is applied
automatically to keep one test's variables from leaking into the next.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest
import yaml

PLATFORM_CORE = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT = PLATFORM_CORE / "scripts" / "deploy_fabric_item.py"

# The five item repos are siblings of platform-core when all are cloned side by side.
# Integration tests skip themselves when they aren't.
REPO_ROOT = PLATFORM_CORE.parent


def _load_deploy_module():
    spec = importlib.util.spec_from_file_location("deploy_fabric_item", DEPLOY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["deploy_fabric_item"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def deploy():
    """The deploy script, loaded as a module."""
    return _load_deploy_module()


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Drop every FABRIC_PARAM_* and $ENV:* variable so tests can't contaminate each other."""
    for key in list(os.environ):
        if key.startswith(("FABRIC_PARAM_", "$ENV:")):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture
def workspace(tmp_path):
    """Build a throwaway repository_directory: a parameter.yml plus one item definition.

    Returns a callable so each test can describe the exact shape it needs:

        workspace(rules=[{"find_value": "abc", "replace_value": {"TEST": "xyz"}}],
                  item_content='{"connection": "abc"}')
    """

    def _build(rules, item_content='{"connection": "abc-123"}', directory_name="ws"):
        root = tmp_path / directory_name
        item_dir = root / "some_item.DataPipeline"
        item_dir.mkdir(parents=True)
        (item_dir / "pipeline-content.json").write_text(item_content)

        # Written as raw text, not yaml.dump, so $ENV: tokens survive verbatim the way they
        # appear in the real files.
        lines = ["find_replace:"]
        for rule in rules:
            lines.append(f'    - find_value: "{rule["find_value"]}"')
            lines.append("      replace_value:")
            for env_name, value in rule["replace_value"].items():
                lines.append(f'          {env_name}: "{value}"')
            lines.append('      item_type: "DataPipeline"')
            lines.append('      item_name: "some_item"')
        (root / "parameter.yml").write_text("\n".join(lines) + "\n")
        return root

    return _build


def parameter_files():
    """(repo_name, repository_directory) for every real parameter.yml, if the repos are here."""
    known = [
        ("ms-fabric-ingestion", "datasource_nyc_taxi"),
        ("ms-fabric-dd-trip-data", "silver"),
        ("ms-fabric-dd-trip-data", "gold"),
        ("ms-fabric-orchestration", "orchestration"),
        ("ms-fabric-dp-trip-report", "semantic_models/taxi_trip"),
    ]
    found = []
    for repo, directory in known:
        path = REPO_ROOT / repo / directory
        if (path / "parameter.yml").exists():
            found.append(pytest.param(path, id=f"{repo}/{directory}"))
    return found


def read_rules(repository_directory: Path):
    """find_replace rules from a real parameter.yml, with $ENV: tokens left unresolved."""
    content = (repository_directory / "parameter.yml").read_text()
    # Tokens aren't valid YAML values on their own but they're quoted in the real files,
    # so a plain safe_load is fine here.
    return (yaml.safe_load(content) or {}).get("find_replace") or []
