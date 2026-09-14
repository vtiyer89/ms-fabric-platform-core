"""resolve_bindings: the substitution engine that replaces parameter.yml.

The old mechanism's dominant failure mode was a rule that matched nothing and was skipped in
silence, shipping a Dev GUID on a green deploy. assert_find_values_present caught that for rules
that were configured; it was structurally blind to a GUID no rule mentioned at all.

report_unknown_guids inverts the question — what is still here that nothing accounts for — and
these tests pin the classification rules it uses, because every one of them is an exclusion and
an over-broad exclusion silently restores the original bug.
"""

import json

import pytest

import resolve_bindings as rb

DEV_WS = "307d28b4-50b2-43c0-ba34-4f8e0e5c53a0"
DEV_ITEM = "e52acf23-60cb-480b-9256-9f75e3d424d2"
TARGET_WS = "d76dab0a-ec8b-445c-aa8d-4c20332c6be5"
TARGET_ITEM = "aaaa0006-0000-0000-0000-000000000006"


@pytest.fixture
def item_dir(tmp_path):
    """A repository directory holding one item, with a .platform carrying a logicalId."""

    def _build(content, filename="pipeline-content.json", logical_id=None):
        root = tmp_path / "ws"
        item = root / "some_item.DataPipeline"
        item.mkdir(parents=True)
        (item / filename).write_text(content if isinstance(content, str) else json.dumps(content))
        if logical_id:
            (item / ".platform").write_text(json.dumps({"config": {"logicalId": logical_id}}))
        return root

    return _build


# --- the substitution map ------------------------------------------------------------------


def test_global_substitution_false_is_kept_out_of_the_map():
    """The Dev pipelines workspace must never be swapped globally.

    One Dev GUID becomes two different Test workspaces there. Including it would rewrite
    master_landing and master_bronze identically and collapse the split — silently, because the
    result is still a valid GUID in a valid field.
    """
    dev_map = {
        "workspaces": {
            "silver": {"workspace_id": DEV_WS},
            "pipelines": {"workspace_id": "4b97f6ae-61d4-4547-98dc-81ed6a137c28",
                          "global_substitution": False},
        }
    }
    workspaces, _items, excluded = rb.dev_guids(dev_map)
    assert "silver" in workspaces
    assert "pipelines" not in workspaces
    assert excluded["pipelines"] == "4b97f6ae-61d4-4547-98dc-81ed6a137c28"


def test_items_without_a_dev_guid_are_not_substitutable():
    """Items nothing references carry no Dev GUID, and must not produce a half-built entry."""
    dev_map = {"items": {"nb": {"display_name": "nb_silver", "type": "Notebook"}}}
    _ws, items, _excluded = rb.dev_guids(dev_map)
    assert items == {}


# --- connections ---------------------------------------------------------------------------


def test_connection_reads_the_bare_variable(monkeypatch):
    monkeypatch.setenv("DEV_SILVER_CONNECTION_ID", "from-bare")
    values, missing = rb.connection_values({"connections": {"s": {"from_env": "DEV_SILVER_CONNECTION_ID"}}}, "DEV")
    assert values == {"s": "from-bare"} and not missing


def test_connection_falls_back_to_the_fabric_param_prefix(monkeypatch):
    """CI already sets FABRIC_PARAM_<NAME>; accepting it means no variable gets renamed."""
    monkeypatch.setenv("FABRIC_PARAM_DEV_SILVER_CONNECTION_ID", "from-prefixed")
    values, missing = rb.connection_values({"connections": {"s": {"from_env": "DEV_SILVER_CONNECTION_ID"}}}, "DEV")
    assert values == {"s": "from-prefixed"} and not missing


def test_unset_connection_is_reported_not_silently_skipped(monkeypatch):
    monkeypatch.delenv("DEV_SILVER_CONNECTION_ID", raising=False)
    monkeypatch.delenv("FABRIC_PARAM_DEV_SILVER_CONNECTION_ID", raising=False)
    values, missing = rb.connection_values({"connections": {"s": {"from_env": "DEV_SILVER_CONNECTION_ID"}}}, "DEV")
    assert values == {}
    assert "DEV_SILVER_CONNECTION_ID" in missing[0]


# --- applying ------------------------------------------------------------------------------


def test_substitution_rewrites_every_occurrence(item_dir):
    root = item_dir(f'{{"a": "{DEV_ITEM}", "b": "{DEV_ITEM}"}}')
    subs = {DEV_ITEM: (TARGET_ITEM, "item lh_silver")}
    changed, applied = rb.apply_substitutions(root, subs, dry_run=False)
    assert applied[DEV_ITEM] == 2
    assert DEV_ITEM not in (root / "some_item.DataPipeline" / "pipeline-content.json").read_text()


def test_dry_run_changes_nothing_on_disk(item_dir):
    root = item_dir(f'{{"a": "{DEV_ITEM}"}}')
    path = root / "some_item.DataPipeline" / "pipeline-content.json"
    before = path.read_text()
    changed, applied = rb.apply_substitutions(root, {DEV_ITEM: (TARGET_ITEM, "x")}, dry_run=True)
    assert applied[DEV_ITEM] == 1
    assert changed and path.read_text() == before


def test_platform_files_are_never_rewritten(item_dir):
    """fabric-cicd reads logicalId out of .platform to build its own mapping.

    Rewriting it would break _replace_logical_ids, which is what resolves every pipeline's
    notebookId and copyJobId. Those references have never had a substitution rule and must not
    acquire one.
    """
    root = item_dir(f'{{"a": "{DEV_ITEM}"}}', logical_id=DEV_ITEM)
    rb.apply_substitutions(root, {DEV_ITEM: (TARGET_ITEM, "x")}, dry_run=False)
    assert DEV_ITEM in (root / "some_item.DataPipeline" / ".platform").read_text()


# --- the unknown-GUID guard ----------------------------------------------------------------


def test_unsubstituted_dev_guid_is_reported(item_dir):
    """The core case: a GUID nothing accounts for must fail the deploy, not ship."""
    root = item_dir(f'{{"workspaceId": "{DEV_WS}"}}')
    unknown = rb.report_unknown_guids(root, substitutions={}, excluded={})
    assert DEV_WS in unknown


def test_substituted_target_guid_is_not_reported(item_dir):
    root = item_dir(f'{{"workspaceId": "{TARGET_WS}"}}')
    unknown = rb.report_unknown_guids(root, {DEV_WS: (TARGET_WS, "workspace silver")}, excluded={})
    assert unknown == {}


def test_logical_ids_are_not_reported(item_dir):
    """A logicalId is fabric-cicd's business, not an environment value."""
    logical = "e3d424d2-9f75-9256-480b-60cbe52acf23"
    root = item_dir(f'{{"notebookId": "{logical}"}}', logical_id=logical)
    assert rb.report_unknown_guids(root, {}, {}) == {}


def test_bare_id_key_is_treated_as_internal(item_dir):
    """Fabric names cross-references '<thing>Id' and self-identity plainly 'id'."""
    internal = "e31c22fc-06df-40c8-800d-de9e0146cd20"
    root = item_dir({"activities": [{"id": internal}]})
    assert rb.report_unknown_guids(root, {}, {}) == {}


def test_suffixed_id_key_is_not_treated_as_internal(item_dir):
    """The mirror of the rule above — this is what stops the exclusion being over-broad."""
    root = item_dir({"activities": [{"workspaceId": DEV_WS}]})
    assert DEV_WS in rb.report_unknown_guids(root, {}, {})


def test_lineage_tags_are_not_reported(item_dir):
    """A semantic model carries tens of these; reporting them makes the guard unusable."""
    tag = "01ffe7b5-4daa-4f97-83bb-7e71b9cd2a59"
    root = item_dir(f"table x\n\tlineageTag: {tag}\n", filename="model.tmdl")
    assert rb.report_unknown_guids(root, {}, {}) == {}


def test_a_real_guid_in_tmdl_is_still_reported(item_dir):
    """The Direct Lake URL lives in TMDL too — the lineageTag exclusion must not swallow it."""
    root = item_dir(
        f'let Source = AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/{DEV_WS}/x")',
        filename="expressions.tmdl",
    )
    assert DEV_WS in rb.report_unknown_guids(root, {}, {})


def test_null_guid_is_not_reported(item_dir):
    """Fabric's 'current workspace' sentinel."""
    root = item_dir(f'{{"workspaceId": "{rb.NULL_GUID}"}}')
    assert rb.report_unknown_guids(root, {}, {}) == {}


# --- jsonpath overrides --------------------------------------------------------------------


def test_override_skips_a_directory_without_the_item(item_dir, capsys):
    """Overrides are declared per environment but applied per directory."""
    root = item_dir('{"properties": {"activities": []}}')
    override = [{"description": "d", "item_type": "DataPipeline", "item_name": "pl_orch_trips",
                 "path": "$.properties.activities[?(@.name=='x')].typeProperties.workspaceId",
                 "workspace": "landing"}]
    rb.apply_jsonpath_overrides(root, override, {"landing": TARGET_WS}, dry_run=False)
    assert "not in this directory" in capsys.readouterr().out


def test_override_present_but_matching_nothing_is_fatal(item_dir):
    """The silent-no-op case. Present item, path matches nothing: must never pass quietly."""
    root = item_dir('{"properties": {"activities": [{"name": "other"}]}}')
    (root / "pl_orch_trips.DataPipeline").mkdir()
    (root / "pl_orch_trips.DataPipeline" / "pipeline-content.json").write_text(
        '{"properties": {"activities": [{"name": "other"}]}}'
    )
    override = [{"description": "d", "item_type": "DataPipeline", "item_name": "pl_orch_trips",
                 "path": "$.properties.activities[?(@.name=='master_landing')].typeProperties.workspaceId",
                 "workspace": "landing"}]
    with pytest.raises(SystemExit) as exit_info:
        rb.apply_jsonpath_overrides(root, override, {"landing": TARGET_WS}, dry_run=False)
    assert "matched nothing" in str(exit_info.value)


def test_override_splits_one_dev_guid_into_two_targets(item_dir):
    """The case the whole override mechanism exists for."""
    shared = "4b97f6ae-61d4-4547-98dc-81ed6a137c28"
    root = item_dir("{}")
    item = root / "pl_orch_trips.DataPipeline"
    item.mkdir()
    content = {"properties": {"activities": [
        {"name": "master_landing", "typeProperties": {"workspaceId": shared}},
        {"name": "master_bronze", "typeProperties": {"workspaceId": shared}},
    ]}}
    path = item / "pipeline-content.json"
    path.write_text(json.dumps(content))

    overrides = [
        {"description": "landing", "item_type": "DataPipeline", "item_name": "pl_orch_trips",
         "path": "$.properties.activities[?(@.name=='master_landing')].typeProperties.workspaceId",
         "workspace": "landing"},
        {"description": "bronze", "item_type": "DataPipeline", "item_name": "pl_orch_trips",
         "path": "$.properties.activities[?(@.name=='master_bronze')].typeProperties.workspaceId",
         "workspace": "bronze"},
    ]
    rb.apply_jsonpath_overrides(root, overrides, {"landing": "LANDING-WS", "bronze": "BRONZE-WS"}, dry_run=False)

    result = json.loads(path.read_text())["properties"]["activities"]
    assert result[0]["typeProperties"]["workspaceId"] == "LANDING-WS"
    assert result[1]["typeProperties"]["workspaceId"] == "BRONZE-WS"
