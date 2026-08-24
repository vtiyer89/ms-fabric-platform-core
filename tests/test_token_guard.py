"""assert_all_tokens_resolvable: refuse to deploy with an unsupplied $ENV: token.

fabric-cicd leaves an unmatched token as literal text and still validates clean, so without
this guard an unset variable publishes the string "$ENV:FOO" into the item definition as if
it were a real connection ID.
"""

import pytest

RULE = {
    "find_value": "dev-guid",
    "replace_value": {"DEV": "dev-guid", "TEST": "$ENV:TEST_CONNECTION_ID"},
}


def test_passes_when_every_token_is_supplied(deploy, workspace):
    root = workspace([RULE])

    deploy.assert_all_tokens_resolvable(str(root), {"TEST_CONNECTION_ID"})


def test_passes_when_the_file_uses_no_tokens(deploy, workspace):
    root = workspace([{"find_value": "dev-guid", "replace_value": {"TEST": "test-guid"}}])

    deploy.assert_all_tokens_resolvable(str(root), set())


def test_exits_when_a_token_is_unsupplied(deploy, workspace):
    root = workspace([RULE])

    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_all_tokens_resolvable(str(root), set())

    assert "TEST_CONNECTION_ID" in str(exit_info.value)


def test_error_names_every_missing_variable(deploy, workspace):
    root = workspace([
        {
            "find_value": "$ENV:DEV_CONNECTION_ID",
            "replace_value": {"DEV": "$ENV:DEV_CONNECTION_ID", "TEST": "$ENV:TEST_CONNECTION_ID"},
        }
    ])

    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_all_tokens_resolvable(str(root), set())

    message = str(exit_info.value)
    assert "DEV_CONNECTION_ID" in message
    assert "TEST_CONNECTION_ID" in message


def test_error_points_at_variables_rather_than_secrets(deploy, workspace):
    """The likeliest cause of an empty value, and the cause of bug 7.3."""
    root = workspace([RULE])

    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_all_tokens_resolvable(str(root), set())

    assert "Secrets" in str(exit_info.value)


def test_tolerates_a_directory_without_a_parameter_file(deploy, tmp_path):
    """Not every repository_directory has a parameter.yml; that isn't an error."""
    (tmp_path / "empty").mkdir()

    deploy.assert_all_tokens_resolvable(str(tmp_path / "empty"), set())
