"""inject_parameter_env_vars: FABRIC_PARAM_<NAME> -> $ENV:<NAME>.

fabric-cicd scans os.environ for keys literally prefixed "$ENV:", which isn't a legal name in
a GitHub Actions `env:` block. The workflow therefore sets conventional names and the deploy
script re-exports them.
"""

import os


def test_translates_workflow_prefix_to_fabric_cicd_prefix(deploy, isolated_env):
    isolated_env.setenv("FABRIC_PARAM_TEST_GOLD_CONNECTION_ID", "5824ee14-abcd")

    provided = deploy.inject_parameter_env_vars()

    assert provided == {"TEST_GOLD_CONNECTION_ID"}
    assert os.environ["$ENV:TEST_GOLD_CONNECTION_ID"] == "5824ee14-abcd"


def test_translates_every_matching_variable(deploy, isolated_env):
    isolated_env.setenv("FABRIC_PARAM_DEV_GOLD_CONNECTION_ID", "a63021b8")
    isolated_env.setenv("FABRIC_PARAM_TEST_GOLD_CONNECTION_ID", "5824ee14")

    assert deploy.inject_parameter_env_vars() == {
        "DEV_GOLD_CONNECTION_ID",
        "TEST_GOLD_CONNECTION_ID",
    }


def test_ignores_variables_without_the_prefix(deploy, isolated_env):
    isolated_env.setenv("AZURE_CLIENT_ID", "not-a-parameter")
    isolated_env.setenv("TEST_GOLD_CONNECTION_ID", "missing-the-prefix")

    assert deploy.inject_parameter_env_vars() == set()
    assert "$ENV:TEST_GOLD_CONNECTION_ID" not in os.environ


def test_blank_value_does_not_count_as_supplied(deploy, isolated_env):
    """`${{ vars.FOO }}` interpolates to "" when the GitHub variable doesn't exist.

    Treating that as supplied would substitute an empty string into parameter.yml and blank
    the value out silently — the shape of bug 7.3.
    """
    isolated_env.setenv("FABRIC_PARAM_TEST_GOLD_CONNECTION_ID", "")

    assert deploy.inject_parameter_env_vars() == set()
    assert "$ENV:TEST_GOLD_CONNECTION_ID" not in os.environ


def test_whitespace_only_value_does_not_count_as_supplied(deploy, isolated_env):
    isolated_env.setenv("FABRIC_PARAM_TEST_GOLD_CONNECTION_ID", "   ")

    assert deploy.inject_parameter_env_vars() == set()
