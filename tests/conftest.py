"""Shared fixtures for the deploy-script tests.

The module under test is a standalone script, not an installed package, so it's loaded by
path rather than imported by name.

Every guard in it reads and writes the process environment, so `isolated_env` is applied
automatically to keep one test's variables from leaking into the next.

The `workspace` fixture and the parameter_files/read_rules helpers were removed with
parameter.yml — checks against the real files now live in test_real_environment_maps.py.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

PLATFORM_CORE = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT = PLATFORM_CORE / "scripts" / "deploy_fabric_item.py"

# The five item repos are siblings of platform-core when all are cloned side by side.
# Integration tests skip themselves when they aren't.
REPO_ROOT = PLATFORM_CORE.parent


def _load_deploy_module():
    spec = importlib.util.spec_from_file_location("deploy_fabric_item", DEPLOY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["deploy_fabric_item"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def deploy():
    """The deploy script, loaded as a module."""
    return _load_deploy_module()


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Drop every FABRIC_PARAM_* and $ENV:* variable so tests can't contaminate each other."""
    for key in list(os.environ):
        if key.startswith(("FABRIC_PARAM_", "$ENV:")):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch
