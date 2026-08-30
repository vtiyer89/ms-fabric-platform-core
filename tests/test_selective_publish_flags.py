"""enable_selective_publish_flags: both flags items_to_include requires.

fabric-cicd gates selective publishing behind its own flag AND the general experimental one,
and rejects the run if either is missing:

    Feature flags 'enable_experimental_features' and 'enable_items_to_include' must be set.

Setting only the specific flag looks correct and fails at deploy time, so both are asserted.
"""

import pytest
from fabric_cicd import constants

REQUIRED = {"enable_experimental_features", "enable_items_to_include"}


@pytest.fixture(autouse=True)
def clean_flags():
    """Flags live in module-level global state, so restore whatever was there."""
    before = set(constants.FEATURE_FLAG)
    constants.FEATURE_FLAG.difference_update(REQUIRED)
    yield
    constants.FEATURE_FLAG.clear()
    constants.FEATURE_FLAG.update(before)


def test_sets_both_required_flags(deploy):
    deploy.enable_selective_publish_flags()

    assert REQUIRED <= constants.FEATURE_FLAG


@pytest.mark.parametrize("flag", sorted(REQUIRED))
def test_sets_each_flag_individually(deploy, flag):
    deploy.enable_selective_publish_flags()

    assert flag in constants.FEATURE_FLAG


def test_is_idempotent(deploy):
    deploy.enable_selective_publish_flags()
    deploy.enable_selective_publish_flags()

    assert REQUIRED <= constants.FEATURE_FLAG


def test_flag_names_match_the_installed_fabric_cicd(deploy):
    """Guards against a typo or an upstream rename on the next version bump."""
    known = {flag.value for flag in constants.FeatureFlag}

    assert REQUIRED <= known
