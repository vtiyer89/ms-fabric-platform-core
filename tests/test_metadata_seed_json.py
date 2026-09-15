"""Static checks on platform/metadata/*.json -- the version-controlled seed content for
metadata.SourceSystemConfig / metadata.TargetStoreConfig / metadata.SourceObjectConfig.

No live database needed: these just guard against the JSON being malformed, having a
duplicate/missing natural key, or (for source_objects.json) referencing a source system or
target that doesn't exist in the other two files -- all things scripts/seed_metadata_db.py's
MERGE statements rely on, either directly (duplicate natural key breaks the upsert) or via the
live subquery lookup (an unresolvable reference hits a NOT NULL constraint at seed time; this
catches the same class of typo offline, before a live run).
"""

import json
from pathlib import Path

METADATA_DIR = Path(__file__).resolve().parent.parent / "platform" / "metadata"


def _source_systems():
    return json.loads((METADATA_DIR / "source_systems.json").read_text())["source_systems"]


def _targets():
    return json.loads((METADATA_DIR / "target_stores.json").read_text())["targets"]


def _source_objects():
    return json.loads((METADATA_DIR / "source_objects.json").read_text())["source_objects"]


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


def test_source_objects_json_parses():
    rows = _source_objects()
    assert rows, "source_objects.json has no rows"


def test_source_object_natural_keys_are_unique():
    keys = [(row["source_system_name"], row["source_object_name"], row["layer_name"]) for row in _source_objects()]
    assert len(keys) == len(set(keys)), f"duplicate (source_system_name, source_object_name, layer_name) in source_objects.json: {keys}"


def test_every_source_object_row_has_config_as_object():
    for row in _source_objects():
        assert isinstance(row["config"], dict), (
            f"{row['source_object_name']}/{row['layer_name']}: config must be a JSON object"
        )


def test_every_source_object_references_a_known_source_system():
    known = {row["source_system_name"] for row in _source_systems()}
    for row in _source_objects():
        assert row["source_system_name"] in known, (
            f"{row['source_object_name']}/{row['layer_name']}: source_system_name "
            f"'{row['source_system_name']}' is not in source_systems.json -- seed_metadata_db.py's "
            f"live subquery would resolve this to NULL and fail the NOT NULL constraint"
        )


def test_every_source_object_references_a_known_target():
    known = {row["target_name"] for row in _targets()}
    for row in _source_objects():
        assert row["target_name"] in known, (
            f"{row['source_object_name']}/{row['layer_name']}: target_name "
            f"'{row['target_name']}' is not in target_stores.json -- seed_metadata_db.py's live "
            f"subquery would resolve this to NULL and fail the NOT NULL constraint"
        )
