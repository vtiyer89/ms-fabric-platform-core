"""The contract between the environment maps, run_seed_metadata and the seeder notebook.

These three drifted apart once already and it was not caught by anything: the maps were changed
to declare connections as `from_env`, while nb_seed_metadata still read `spec["connection_id"]`.
Every seed run would have died with a KeyError on the first connection — meaning the very first
deploy of the estate would have failed, at the stage that creates the md_* tables.

Nothing structural could have caught it, because the seeder is a Fabric notebook and is never
imported. These tests assert the contract from the outside instead.
"""

import pathlib
import re

import pytest
import yaml

import run_seed_metadata as rsm

PLATFORM_CORE = pathlib.Path(__file__).resolve().parent.parent
SEEDER = PLATFORM_CORE / "platform" / "nb_seed_metadata.Notebook" / "notebook-content.py"
MAPS = sorted((PLATFORM_CORE / "metadata" / "environments").glob("*.yml"))


@pytest.mark.parametrize("path", MAPS, ids=lambda p: p.stem)
def test_connections_declare_one_of_the_two_supported_forms(path):
    for name, spec in (yaml.safe_load(path.read_text()).get("connections") or {}).items():
        assert ("from_env" in spec) ^ ("connection_id" in spec), (
            f"connection {name!r} in {path.name} must declare exactly one of "
            f"from_env / connection_id, got {sorted(spec)}"
        )


@pytest.mark.parametrize("path", MAPS, ids=lambda p: p.stem)
def test_every_connection_resolves_to_what_the_seeder_reads(path, monkeypatch):
    """The seeder reads spec["connection_id"]. After resolution, every connection must have one.

    This is the exact assertion that would have caught the KeyError.
    """
    env_map = yaml.safe_load(path.read_text())
    for spec in (env_map.get("connections") or {}).values():
        if "from_env" in spec:
            monkeypatch.setenv(spec["from_env"], "11111111-1111-1111-1111-111111111111")

    resolved = rsm.resolve_from_env(env_map)
    for name, spec in (resolved.get("connections") or {}).items():
        assert "connection_id" in spec, f"connection {name!r} has no connection_id after resolution"
        assert "from_env" not in spec, f"connection {name!r} still carries from_env after resolution"


def test_unresolvable_connection_exits_rather_than_seeding(monkeypatch):
    for variable in list(rsm.os.environ):
        if "CONNECTION_ID" in variable:
            monkeypatch.delenv(variable, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        rsm.resolve_from_env({"connections": {"copy_job": {"from_env": "NOT_SET_ANYWHERE"}}})
    assert "NOT_SET_ANYWHERE" in str(exit_info.value)


def test_fabric_param_prefix_is_accepted(monkeypatch):
    """CI sets FABRIC_PARAM_<NAME>; accepting it is why no CI variable had to be renamed."""
    monkeypatch.delenv("SOME_CONNECTION_ID", raising=False)
    monkeypatch.setenv("FABRIC_PARAM_SOME_CONNECTION_ID", "abc")
    resolved = rsm.resolve_from_env({"connections": {"c": {"from_env": "SOME_CONNECTION_ID"}}})
    assert resolved["connections"]["c"]["connection_id"] == "abc"


def test_every_notebook_parameter_the_trigger_sends_exists_in_the_seeder():
    """A parameter the notebook does not declare is silently ignored by Fabric.

    The seed would then run with a default — seeding the wrong environment, or recording empty
    provenance — and report success.
    """
    sent = set(re.findall(r'"(\w+)": \{"value"', (PLATFORM_CORE / "scripts" / "run_seed_metadata.py").read_text()))
    assert sent, "no notebook parameters found in run_seed_metadata.py"

    seeder_source = SEEDER.read_text()
    declared = set(re.findall(r"^(\w+) = ", seeder_source, re.MULTILINE))
    missing = sorted(sent - declared)
    assert not missing, f"run_seed_metadata sends parameters the seeder never declares: {missing}"


def test_seeder_reads_connection_id_not_from_env():
    """Pins the direction of the contract: resolution happens in CI, never in the notebook.

    The notebook runs inside Fabric, where the CI runner's environment does not exist, so a
    from_env reference could never be resolved there.
    """
    source = SEEDER.read_text()
    assert 'spec["connection_id"]' in source
    assert 'spec["from_env"]' not in source, (
        "the seeder must not read from_env — it runs in Fabric, where CI variables do not exist"
    )


def test_partial_connections_are_dropped_not_fatal_when_allowed(monkeypatch):
    """A per-repo seed only ever carries its OWN connection variables.

    ingestion's deploy passes the copy-job connection and nothing else, so requiring all four
    would have failed the seed step in every item repo — at the tail of every deploy.
    """
    for variable in ("TEST_COPY_JOB_CONNECTION_ID", "TEST_SILVER_CONNECTION_ID"):
        monkeypatch.delenv(variable, raising=False)
        monkeypatch.delenv(f"FABRIC_PARAM_{variable}", raising=False)
    monkeypatch.setenv("FABRIC_PARAM_TEST_COPY_JOB_CONNECTION_ID", "aaa")

    env_map = {
        "connections": {
            "copy_job": {"from_env": "TEST_COPY_JOB_CONNECTION_ID"},
            "silver_notebook": {"from_env": "TEST_SILVER_CONNECTION_ID"},
        }
    }
    resolved = rsm.resolve_from_env(env_map, allow_unresolved=True)

    assert resolved["connections"]["copy_job"]["connection_id"] == "aaa"
    assert "silver_notebook" not in resolved["connections"], (
        "an unsupplied connection must be dropped, not written with a placeholder value"
    )


def test_partial_connections_are_still_fatal_when_strict(monkeypatch):
    """The standalone seed workflow runs strict precisely to prove the set is complete."""
    monkeypatch.delenv("TEST_SILVER_CONNECTION_ID", raising=False)
    monkeypatch.delenv("FABRIC_PARAM_TEST_SILVER_CONNECTION_ID", raising=False)
    with pytest.raises(SystemExit):
        rsm.resolve_from_env(
            {"connections": {"silver_notebook": {"from_env": "TEST_SILVER_CONNECTION_ID"}}},
            allow_unresolved=False,
        )
