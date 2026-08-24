"""Every item must be published by exactly one caller job.

`items-to-include` lets one repository-directory feed two workspaces (ingestion's landing and
bronze). The failure mode is silent in both directions: an item named in no job is never
deployed and nothing errors, and an item named in two jobs is published to both workspaces.

Only repos whose workflow actually uses items-to-include are checked.
"""

import re

import pytest
import yaml

from conftest import REPO_ROOT

ITEM_DIR_PATTERN = re.compile(r"([^/]+)\.(Lakehouse|DataPipeline|CopyJob|Notebook|SemanticModel|Report)/")


def _workflows_using_items_to_include():
    found = []
    for workflow in sorted(REPO_ROOT.glob("ms-fabric-*/.github/workflows/deploy-test.yml")):
        content = yaml.safe_load(workflow.read_text())
        jobs = content.get("jobs", {})
        if any("items-to-include" in job.get("with", {}) for job in jobs.values()):
            found.append(pytest.param(workflow, id=workflow.parts[-4]))
    return found


SPLIT_WORKFLOWS = _workflows_using_items_to_include()

pytestmark = pytest.mark.skipif(
    not SPLIT_WORKFLOWS, reason="no repo currently uses items-to-include"
)


def _covered_and_actual(workflow):
    content = yaml.safe_load(workflow.read_text())
    covered, directories = [], set()
    for job in content["jobs"].values():
        with_block = job.get("with", {})
        covered += [i.strip() for i in with_block.get("items-to-include", "").split(",") if i.strip()]
        directories.add(with_block["repository-directory"])

    repo_root = workflow.parents[2]
    actual = set()
    for directory in directories:
        for path in (repo_root / directory).rglob("*"):
            match = ITEM_DIR_PATTERN.search(str(path.relative_to(repo_root / directory)) + "/")
            if match:
                actual.add(f"{match.group(1)}.{match.group(2)}")
    return covered, actual


@pytest.mark.parametrize("workflow", SPLIT_WORKFLOWS)
def test_no_item_is_left_undeployed(workflow):
    covered, actual = _covered_and_actual(workflow)

    assert not (actual - set(covered)), (
        f"items in the repo but named by no job, so never deployed: {sorted(actual - set(covered))}"
    )


@pytest.mark.parametrize("workflow", SPLIT_WORKFLOWS)
def test_no_item_is_deployed_to_two_workspaces(workflow):
    covered, _ = _covered_and_actual(workflow)
    duplicated = sorted({name for name in covered if covered.count(name) > 1})

    assert not duplicated, f"items named by more than one job: {duplicated}"


@pytest.mark.parametrize("workflow", SPLIT_WORKFLOWS)
def test_no_job_names_a_nonexistent_item(workflow):
    """A typo'd name is accepted silently and simply publishes nothing."""
    covered, actual = _covered_and_actual(workflow)

    assert not (set(covered) - actual), (
        f"named by a job but not present in the repo: {sorted(set(covered) - actual)}"
    )
