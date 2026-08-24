"""assert_find_values_present: refuse to deploy a rule that matches nothing.

fabric-cicd skips a find_replace rule whose find_value appears in no file, and its own
validate_parameter_file still returns True — so the Dev GUID ships to the target environment
on a green run. That's bug 7.2, and nothing upstream catches it.

The check matters more now that find_value can come from an environment variable, which can
drift from the item definitions in git without anyone touching the repo.
"""

import pytest

PRESENT = "abc-123"
ABSENT = "deadbeef-1111-2222-3333-444444444444"


def test_passes_when_find_value_appears_in_an_item_file(deploy, workspace):
    root = workspace(
        [{"find_value": PRESENT, "replace_value": {"TEST": "test-guid"}}],
        item_content=f'{{"connection": "{PRESENT}"}}',
    )

    deploy.assert_find_values_present(str(root))


def test_exits_when_find_value_appears_nowhere(deploy, workspace):
    root = workspace(
        [{"find_value": ABSENT, "replace_value": {"TEST": "test-guid"}}],
        item_content=f'{{"connection": "{PRESENT}"}}',
    )

    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_find_values_present(str(root))

    assert ABSENT in str(exit_info.value)


def test_error_explains_the_silent_skip(deploy, workspace):
    root = workspace([{"find_value": ABSENT, "replace_value": {"TEST": "t"}}])

    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_find_values_present(str(root))

    assert "silently" in str(exit_info.value)


def test_reports_every_unmatched_value_at_once(deploy, workspace):
    other_absent = "cafebabe-0000-0000-0000-000000000000"
    root = workspace(
        [
            {"find_value": ABSENT, "replace_value": {"TEST": "t"}},
            {"find_value": other_absent, "replace_value": {"TEST": "t"}},
            {"find_value": PRESENT, "replace_value": {"TEST": "t"}},
        ],
        item_content=f'{{"connection": "{PRESENT}"}}',
    )

    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_find_values_present(str(root))

    message = str(exit_info.value)
    assert ABSENT in message
    assert other_absent in message
    assert PRESENT not in message


def test_resolves_env_tokens_before_matching(deploy, workspace, isolated_env):
    """A dynamic find_value is checked against its resolved value, not the literal token."""
    isolated_env.setenv("FABRIC_PARAM_DEV_CONNECTION_ID", PRESENT)
    deploy.inject_parameter_env_vars()

    root = workspace(
        [{"find_value": "$ENV:DEV_CONNECTION_ID", "replace_value": {"TEST": "t"}}],
        item_content=f'{{"connection": "{PRESENT}"}}',
    )

    deploy.assert_find_values_present(str(root))


def test_catches_an_env_token_that_drifted_from_the_item_json(deploy, workspace, isolated_env):
    """The failure mode dynamic find_value introduces: variable edited, repo untouched."""
    isolated_env.setenv("FABRIC_PARAM_DEV_CONNECTION_ID", ABSENT)
    deploy.inject_parameter_env_vars()

    root = workspace(
        [{"find_value": "$ENV:DEV_CONNECTION_ID", "replace_value": {"TEST": "t"}}],
        item_content=f'{{"connection": "{PRESENT}"}}',
    )

    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_find_values_present(str(root))

    assert ABSENT in str(exit_info.value)


def test_does_not_match_against_parameter_yml_itself(deploy, workspace):
    """A find_value only present in parameter.yml is still unmatched — it must be in an item."""
    root = workspace(
        [{"find_value": ABSENT, "replace_value": {"TEST": ABSENT}}],
        item_content='{"connection": "unrelated"}',
    )

    with pytest.raises(SystemExit):
        deploy.assert_find_values_present(str(root))


def test_tolerates_a_directory_without_a_parameter_file(deploy, tmp_path):
    (tmp_path / "empty").mkdir()

    deploy.assert_find_values_present(str(tmp_path / "empty"))
