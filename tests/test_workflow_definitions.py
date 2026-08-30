"""Static checks over the GitHub Actions workflow files.

These catch failures that only surface when a workflow is dispatched, where the whole run is
rejected at parse time before any step executes — so nothing in the deploy script's own guards
can help.
"""

import re
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT

REUSABLE = Path(__file__).resolve().parent.parent / ".github/workflows/deploy-fabric-item.yml"
CALLERS = sorted(REPO_ROOT.glob("ms-fabric-*/.github/workflows/deploy-test.yml"))
ALL_WORKFLOWS = ([REUSABLE] if REUSABLE.exists() else []) + CALLERS

pytestmark = pytest.mark.skipif(not ALL_WORKFLOWS, reason="no workflow files found")


def _trigger_block(path: Path) -> str:
    """Everything above `jobs:` — the on:/inputs section."""
    text = path.read_text()
    match = re.search(r"^jobs:", text, re.MULTILINE)
    return text[: match.start()] if match else text


@pytest.mark.parametrize("workflow", ALL_WORKFLOWS, ids=lambda p: f"{p.parts[-4]}/{p.name}")
def test_parses_as_yaml(workflow):
    assert yaml.safe_load(workflow.read_text())


@pytest.mark.parametrize("workflow", ALL_WORKFLOWS, ids=lambda p: f"{p.parts[-4]}/{p.name}")
def test_no_expressions_in_the_trigger_block(workflow):
    """Actions evaluates ${{ }} inside input descriptions, not just in steps.

    A `vars.*` example left in a workflow_call input description fails the whole workflow with
    "Unrecognized named-value: 'vars'" — vars isn't a valid context there. Costs a dispatch to
    discover, so assert it here instead.
    """
    found = re.findall(r"\$\{\{.*?\}\}", _trigger_block(workflow))

    assert not found, f"expression(s) in the on:/inputs block: {found}"


@pytest.mark.skipif(not CALLERS, reason="caller repos not cloned alongside platform-core")
@pytest.mark.parametrize("workflow", CALLERS, ids=lambda p: p.parts[-4])
def test_callers_reference_the_reusable_workflow_correctly(workflow):
    """A wrong org, repo, path or ref reports as "workflow was not found" — the same message
    GitHub gives when a private repo simply hasn't shared its reusable workflows."""
    expected = "vtiyer89/ms-fabric-platform-core/.github/workflows/deploy-fabric-item.yml@main"

    for used in re.findall(r"uses:\s*(\S+)", workflow.read_text()):
        if "ms-fabric-platform-core" in used:
            assert used == expected, f"unexpected reusable-workflow reference: {used}"


@pytest.mark.skipif(not CALLERS, reason="caller repos not cloned alongside platform-core")
@pytest.mark.parametrize("workflow", CALLERS, ids=lambda p: p.parts[-4])
def test_callers_only_pass_inputs_the_reusable_workflow_declares(workflow):
    """An undeclared input is another parse-time rejection of the entire run."""
    declared = set(yaml.safe_load(REUSABLE.read_text())[True]["workflow_call"]["inputs"])

    for job in yaml.safe_load(workflow.read_text())["jobs"].values():
        if "ms-fabric-platform-core" not in str(job.get("uses", "")):
            continue
        unknown = set(job.get("with", {})) - declared
        assert not unknown, f"passes inputs the reusable workflow doesn't declare: {unknown}"


@pytest.mark.skipif(not CALLERS, reason="caller repos not cloned alongside platform-core")
@pytest.mark.parametrize("workflow", CALLERS, ids=lambda p: p.parts[-4])
def test_every_job_supplies_all_tokens_its_parameter_file_needs(workflow):
    """Checked per job, not per repo.

    $ENV: tokens are resolved for the whole parameter.yml before fabric-cicd parses it, and the
    deploy script's guard scans the whole file — so a job needs every token in the file it
    points at, even ones only used by items outside its items-to-include list.

    ms-fabric-ingestion is where this bites: deploy-landing and deploy-bronze share one
    parameter.yml, and only the copy job uses the connection. Comparing per repo hides it,
    because the other job supplies the variables.
    """
    repo_root = workflow.parents[2]
    jobs = yaml.safe_load(workflow.read_text())["jobs"]

    for job_name, job in jobs.items():
        directory = (job.get("with") or {}).get("repository-directory")
        if not directory:
            continue
        parameter_file = repo_root / directory / "parameter.yml"
        if not parameter_file.exists():
            continue

        needed = set(re.findall(r"\$ENV:([A-Za-z_][A-Za-z0-9_]*)", parameter_file.read_text()))
        supplied = set(re.findall(r"FABRIC_PARAM_([A-Z_]+)", (job.get("with") or {}).get("parameter-env-vars", "")))

        assert not (needed - supplied), (
            f"job '{job_name}' points at {directory}/parameter.yml, which needs "
            f"{sorted(needed - supplied)}, but the job doesn't supply them"
        )
