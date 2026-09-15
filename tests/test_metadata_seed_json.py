"""Static checks on platform/metadata/*.json -- the version-controlled seed content for
metadata.SourceSystemConfig / metadata.TargetStoreConfig.

No live database needed: these just guard against the JSON being malformed or having a
duplicate/missing natural key, which scripts/seed_metadata_db.py's MERGE relies on.
"""

import json
from pathlib import Path

METADATA_DIR = Path(__file__).resolve().parent.parent / "platform" / "metadata"


def _source_systems():
    return json.loads((METADATA_DIR / "source_systems.json").read_text())["source_systems"]


def _targets():
    return json.loads((METADATA_DIR / "target_stores.json").read_text())["targets"]


def test_source_systems_json_parses():
    rows = _source_systems()
    assert rows, "source_systems.json has no rows"


def test_source_system_names_are_unique():
    names = [row["source_system_name"] for row in _source_systems()]
    assert len(names) == len(set(names)), f"duplicate SourceSystemName in source_systems.json: {names}"


def test_every_source_system_row_has_config_as_object():
    for row in _source_systems():
        assert isinstance(row["config"], dict), f"{row['source_system_name']}: config must be a JSON object"


def test_targets_json_parses():
    rows = _targets()
    assert rows, "target_stores.json has no rows"


def test_target_names_are_unique():
    names = [row["target_name"] for row in _targets()]
    assert len(names) == len(set(names)), f"duplicate TargetName in target_stores.json: {names}"


def test_every_target_row_has_dev_and_test_environments():
    for row in _targets():
        per_env = row["per_environment"]
        missing = {"DEV", "TEST"} - per_env.keys()
        assert not missing, f"{row['target_name']}: missing per_environment entries: {missing}"


def test_every_target_row_has_config_as_object():
    for row in _targets():
        assert isinstance(row["config"], dict), f"{row['target_name']}: config must be a JSON object"
