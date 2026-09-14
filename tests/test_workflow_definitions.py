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


def _workflow_at_ref(ref):
    """The reusable workflow as it exists at `ref`, or None if that ref isn't available."""
    import subprocess

    result = subprocess.run(
        ["git", "show", f"{ref}:.github/workflows/deploy-fabric-item.yml"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


@pytest.mark.skipif(not CALLERS, reason="caller repos not cloned alongside platform-core")
@pytest.mark.parametrize("workflow", CALLERS, ids=lambda p: p.parts[-4])
def test_callers_match_the_reusable_workflow_at_the_REF_THEY_PIN(workflow):
    """The check above compares against the working tree. Reality uses the pinned ref.

    A caller says `uses: …/deploy-fabric-item.yml@main`, so at run time GitHub reads MAIN's copy,
    not the one in this branch. On a feature branch that adds an input to both the caller and the
    reusable workflow, the working-tree comparison passes while the real dispatch fails at parse
    time with "unexpected input" — the entire run rejected before any step executes.

    That is exactly how ms-fabric-dp-trip-report's allow-deletes bug survived: locally consistent,
    broken against the ref it actually pinned.

    Skipped when the pinned ref isn't fetched locally, so this can't fail for the wrong reason.
    """
    for job in yaml.safe_load(workflow.read_text())["jobs"].values():
        uses = str(job.get("uses", ""))
        if "ms-fabric-platform-core" not in uses:
            continue

        ref = uses.rsplit("@", 1)[-1]
        source = _workflow_at_ref(ref)
        if source is None:
            pytest.skip(f"ref {ref!r} not available locally")

        on = yaml.safe_load(source)
        declared = set((on.get(True) or on.get("on"))["workflow_call"].get("inputs") or {})
        unknown = set(job.get("with", {})) - declared
        assert not unknown, (
            f"passes {sorted(unknown)}, which deploy-fabric-item.yml@{ref} does not declare. "
            f"The run would be rejected at parse time. Merge platform-core to {ref} first, or "
            f"repoint the caller."
        )
