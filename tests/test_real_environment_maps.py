"""Checks against the real environment maps and the real item repos.

The replacement for test_real_parameter_files.py. Those were the strongest checks in the suite
because they ran against what actually ships rather than against a fixture, and the same is true
here: a map that is internally consistent but disagrees with the item definitions produces a
deploy that is green and wrong.

Skipped automatically when the item repos aren't cloned alongside platform-core, so a checkout
of platform-core on its own still passes — which also means a green run HERE is weaker than a
green run with the siblings present. Check for skips.
"""

import pathlib

import pytest
import yaml

import resolve_bindings as rb

PLATFORM_CORE = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = PLATFORM_CORE.parent

REPOSITORY_DIRECTORIES = [
    ("ms-fabric-ingestion", "datasource_nyc_taxi"),
    ("ms-fabric-dd-trip-data", "silver"),
    ("ms-fabric-dd-trip-data", "gold"),
    ("ms-fabric-orchestration", "orchestration"),
    ("ms-fabric-dp-trip-report", "semantic_models/taxi_trip"),
]


def _present():
    return [REPO_ROOT / repo / directory for repo, directory in REPOSITORY_DIRECTORIES
            if (REPO_ROOT / repo / directory).is_dir()]


pytestmark = pytest.mark.skipif(
    len(_present()) != len(REPOSITORY_DIRECTORIES),
    reason="item repos not cloned alongside platform-core",
)


@pytest.fixture(scope="module")
def dev_map():
    return yaml.safe_load((PLATFORM_CORE / "metadata" / "environments" / "dev.yml").read_text())


@pytest.fixture(scope="module")
def test_map():
    return yaml.safe_load((PLATFORM_CORE / "metadata" / "environments" / "test.yml").read_text())


@pytest.fixture(scope="module")
def all_item_text():
    """Every item definition across all five directories, as one corpus."""
    chunks = []
    for root in _present():
        for path in rb.candidate_files(root):
            try:
                chunks.append(path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                continue
    return "\n".join(chunks)


def test_every_dev_guid_actually_appears_somewhere(dev_map, all_item_text):
    """A Dev GUID matching nothing is a binding that silently does nothing.

    This is the direct descendant of assert_find_values_present and of bug 7.2: a stale or
    mistyped value on the find side means the substitution is skipped and the Dev value ships on
    a green deploy. Placeholders are exempt — they stand in for values that do not exist yet.
    """
    workspaces, items, _excluded = rb.dev_guids(dev_map)
    placeholders = rb.placeholder_guids(dev_map)

    orphaned = [
        f"{label} ({guid})"
        for label, guid in [(f"workspace {n}", g) for n, g in workspaces.items()]
        + [(f"item {n}", g) for n, g in items.items()]
        if guid not in all_item_text and guid not in placeholders
    ]
    assert not orphaned, f"dev.yml bindings that match nothing in any item repo: {orphaned}"


def test_excluded_guids_also_appear(dev_map, all_item_text):
    """global_substitution: false still has to describe something real.

    The Dev pipelines workspace is excluded from the global map but is very much present in
    pl_orch_trips — if it stopped appearing, the jsonpath overrides would be rewriting nothing.
    """
    _ws, _items, excluded = rb.dev_guids(dev_map)
    missing = [f"{name} ({guid})" for name, guid in excluded.items() if guid not in all_item_text]
    assert not missing, f"excluded bindings that match nothing: {missing}"


def test_every_dev_logical_name_exists_in_the_target_map(dev_map, test_map):
    """A name on the find side with no replace side substitutes to nothing.

    Names marked global_substitution: false are exempt, and must be. The Dev "pipelines"
    workspace has no Test counterpart on purpose: Test splits it across landing and bronze, and
    the jsonpath overrides — not a logical-name pairing — are what express that.
    """
    _ws, _items, excluded = rb.dev_guids(dev_map)
    for section in ("workspaces", "items", "connections"):
        dev_names = set(dev_map.get(section) or {}) - set(excluded)
        missing = dev_names - set(test_map.get(section) or {})
        assert not missing, f"{section} in dev.yml but not test.yml: {sorted(missing)}"


def test_target_map_items_reference_declared_workspaces(test_map):
    declared = set(test_map.get("workspaces") or {})
    for name, spec in (test_map.get("items") or {}).items():
        assert spec["workspace"] in declared, f"item {name!r} references undeclared workspace"


def test_jsonpath_overrides_reference_declared_workspaces(test_map):
    declared = set(test_map.get("workspaces") or {})
    for override in test_map.get("jsonpath_overrides") or []:
        assert override["workspace"] in declared, (
            f"override {override.get('description')!r} targets an undeclared workspace"
        )


def test_jsonpath_overrides_still_match_the_real_pipeline(test_map):
    """An override that matches nothing is a silent no-op.

    This is the check that would catch someone renaming an activity in pl_orch_trips: the
    override keeps referring to the old name, matches nothing, and the shared Dev workspace GUID
    ships to both activities unchanged.
    """
    from jsonpath_ng.ext import parse

    import json

    root = REPO_ROOT / "ms-fabric-orchestration" / "orchestration"
    for override in test_map.get("jsonpath_overrides") or []:
        matched = False
        for path in rb.candidate_files(root):
            if path.suffix != ".json" or override["item_name"] not in str(path):
                continue
            if parse(override["path"]).find(json.loads(path.read_text())):
                matched = True
        assert matched, f"override {override.get('description')!r} matches nothing in {root}"


def test_target_workspaces_are_all_named_by_display_name(test_map):
    """A deploy target must resolve by NAME, or it loses the self-healing property."""
    for name, spec in (test_map.get("workspaces") or {}).items():
        assert spec.get("display_name"), f"workspace {name!r} in test.yml has no display_name"


def test_connections_are_declared_the_same_way_in_both_maps(dev_map, test_map):
    """A connection with connection_id on one side and from_env on the other is a trap."""
    for name, dev_spec in (dev_map.get("connections") or {}).items():
        test_spec = (test_map.get("connections") or {})[name]
        assert set(dev_spec) == set(test_spec), (
            f"connection {name!r} is declared differently in dev.yml and test.yml"
        )


def test_no_parameter_yml_survives():
    """The whole point. A stray parameter.yml would be silently ignored by the new deploy path."""
    strays = [str(p.relative_to(REPO_ROOT)) for root in _present() for p in root.rglob("parameter*.yml")]
    assert not strays, f"parameter.yml files still present: {strays}"


def test_notebooks_have_no_cross_workspace_deploy_time_bindings(dev_map):
    """Silver and gold must depend only on themselves and on the platform workspace.

    This is the headline property of the whole change: their upstream references resolve at RUN
    time from md_item, so gold can deploy before silver and silver before bronze. If a
    cross-workspace GUID reappears in either directory, deploy ordering is silently back and the
    only symptom is a failure much later, when someone finally deploys them out of order.
    """
    workspaces, items, _excluded = rb.dev_guids(dev_map)
    placeholders = rb.placeholder_guids(dev_map)

    allowed = {
        "silver": {"silver", "lh_silver"},
        "gold": {"gold", "lh_gold"},
    }

    for layer, own in allowed.items():
        root = REPO_ROOT / "ms-fabric-dd-trip-data" / layer
        text = "\n".join(
            p.read_text(encoding="utf-8", errors="ignore") for p in rb.candidate_files(root)
        )
        found = {
            name
            for name, guid in {**workspaces, **items}.items()
            if guid in text and guid not in placeholders
        }
        assert found == own, (
            f"{layer} should bind only {sorted(own)} at deploy time (plus the platform "
            f"placeholders), but binds {sorted(found)}"
        )
