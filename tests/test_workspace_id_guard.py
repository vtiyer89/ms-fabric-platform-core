"""assert_workspace_id_supplied: refuse to deploy without a real workspace ID.

`${{ vars.FOO }}` interpolates to "" when the GitHub variable doesn't exist, and argparse's
required=True accepts "". fabric-cicd then fails with a generic message that doesn't name the
missing variable — bug 7.3. Renaming a workspace variable (as the landing/bronze split did)
is the easiest way to recreate it.
"""

import pytest

VALID = "e9472408-b9e1-45ae-8c2a-e22911c8b109"


def test_accepts_a_real_workspace_id(deploy):
    deploy.assert_workspace_id_supplied(VALID)


def test_accepts_an_uppercase_guid(deploy):
    deploy.assert_workspace_id_supplied(VALID.upper())


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_exits_on_a_blank_id(deploy, blank):
    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_workspace_id_supplied(blank)

    assert "empty" in str(exit_info.value)


def test_blank_error_points_at_variables_rather_than_secrets(deploy):
    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_workspace_id_supplied("")

    assert "Secrets" in str(exit_info.value)


def test_exits_when_given_a_display_name_instead_of_an_id(deploy):
    """A plausible mistake now that some workspaces are referenced by name elsewhere."""
    with pytest.raises(SystemExit) as exit_info:
        deploy.assert_workspace_id_supplied("ws-test-landing-rjoose-v2")

    assert "not a GUID" in str(exit_info.value)


def test_exits_on_a_truncated_guid(deploy):
    with pytest.raises(SystemExit):
        deploy.assert_workspace_id_supplied(VALID[:-4])
