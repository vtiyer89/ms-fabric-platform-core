"""Checks against the real parameter.yml files in the sibling item repos.

Skipped automatically when those repos aren't cloned alongside platform-core, so the suite
still passes in a checkout of platform-core on its own.

These are the checks a reviewer would otherwise have to do by eye, and several of them encode
bugs this project actually hit — see Part 7 of the context doc.
"""

import re

import pytest

from conftest import parameter_files, read_rules

REAL_FILES = parameter_files()

pytestmark = pytest.mark.skipif(
    not REAL_FILES, reason="sibling item repos not cloned next to ms-fabric-platform-core"
)

# Every workspace display name a $workspace.<name> lookup is allowed to reference.
# A typo here fails loudly at deploy time, but catching it now is cheaper. Bug 7.6.
KNOWN_WORKSPACES = {
    "ws-test-landing-rjoose-v2",
    "ws-test-bronze-rjoose-v2",
    "ws-test-dd-sustainability-silver-v2",
    "ws-test-dd-sustainability-gold-v2",
}


@pytest.mark.parametrize("repository_directory", REAL_FILES)
def test_no_placeholders_remain(repository_directory):
    content = (repository_directory / "parameter.yml").read_text()

    assert "<TEST-" not in content, "unfilled placeholder still present"


@pytest.mark.parametrize("repository_directory", REAL_FILES)
def test_every_rule_has_a_find_value_and_replace_value(repository_directory):
    for rule in read_rules(repository_directory):
        assert rule.get("find_value"), f"rule missing find_value: {rule}"
        assert rule.get("replace_value"), f"rule missing replace_value: {rule}"


@pytest.mark.parametrize("repository_directory", REAL_FILES)
def test_no_file_path_filters(repository_directory):
    """file_path is relative to repository_directory, and getting it wrong skips silently.

    Bug 7.2. item_type + item_name already identify the file uniquely here, so the project
    dropped file_path entirely; reintroducing one needs the path verified by hand.
    """
    for rule in read_rules(repository_directory):
        assert "file_path" not in rule, f"unexpected file_path filter: {rule}"


@pytest.mark.parametrize("repository_directory", REAL_FILES)
def test_workspace_lookups_use_known_display_names(repository_directory):
    """$workspace.<name> is matched literally and case-sensitively against the live tenant."""
    content = (repository_directory / "parameter.yml").read_text()

    for name in re.findall(r"\$workspace\.([A-Za-z0-9][\w .-]*?)\.\$", content):
        assert name in KNOWN_WORKSPACES, f"unknown workspace display name: {name}"


@pytest.mark.parametrize("repository_directory", REAL_FILES)
def test_static_dev_values_match_their_find_value(repository_directory):
    """A literal DEV: value is documentation, and must restate find_value exactly.

    Only meaningful for rules where both sides are literal — once find_value is an $ENV: token
    both sides read from the same variable and the check is true by construction.
    """
    for rule in read_rules(repository_directory):
        find_value = rule["find_value"]
        dev_value = rule["replace_value"].get("DEV")
        if dev_value is None or find_value.startswith("$ENV:"):
            continue
        assert dev_value == find_value, (
            f"DEV value {dev_value!r} does not restate find_value {find_value!r}"
        )


@pytest.mark.parametrize("repository_directory", REAL_FILES)
def test_connection_rules_are_supplied_by_environment(repository_directory):
    """Connections can't resolve live, so every one of them must come from a variable.

    A hardcoded TEST connection GUID would still work, but it's the class of value that
    changes for operational reasons and shouldn't require a commit.
    """
    for rule in read_rules(repository_directory):
        if "TEST" not in rule["replace_value"]:
            continue  # an _ALL_ rule — resolves live for every environment
        test_value = rule["replace_value"]["TEST"]
        if test_value.startswith(("$workspace", "$items")):
            continue  # resolved live against the Fabric API
        assert test_value.startswith("$ENV:"), (
            f"static TEST value {test_value!r} should be supplied as an $ENV: variable"
        )


@pytest.mark.parametrize("repository_directory", REAL_FILES)
def test_all_tokens_resolve_when_their_variables_are_set(deploy, repository_directory, isolated_env):
    """The whole chain: set FABRIC_PARAM_* for whatever the file references, and it passes."""
    content = (repository_directory / "parameter.yml").read_text()
    referenced = set(re.findall(deploy.TOKEN_PATTERN, content))
    for name in referenced:
        isolated_env.setenv(f"FABRIC_PARAM_{name}", "placeholder-value")

    provided = deploy.inject_parameter_env_vars()

    assert provided == referenced
    deploy.assert_all_tokens_resolvable(str(repository_directory), provided)


@pytest.mark.parametrize("repository_directory", REAL_FILES)
def test_deploy_is_refused_when_variables_are_absent(deploy, repository_directory):
    """No variables set: a file that references any token must stop the deploy."""
    content = (repository_directory / "parameter.yml").read_text()
    if not re.search(deploy.TOKEN_PATTERN, content):
        pytest.skip("this parameter.yml references no environment variables")

    with pytest.raises(SystemExit):
        deploy.assert_all_tokens_resolvable(str(repository_directory), set())
