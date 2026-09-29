"""Pre-dispatch checks shared by `task doctor` and the runtime (moved out of `runtime.py`, which is infrastructure)."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from esc_exec.baseline import plan_blockers
from esc_exec.environment import check_prerequisites
from esc_exec.read_only import is_read_only
from esc_exec.task_context import build_task_context, build_verification_plan
from esc_exec.yaml_io import load_yaml


def architecture_coverage_blockers(context: dict[str, Any]) -> list[str]:
    blockers = []
    for component in context["routing"]["components"]:
        architecture = component.get("architecture") or {}
        for doc_id in architecture.get("missing", []):
            blockers.append(f"{component['id']}: architecture doc {doc_id} does not exist")
        for doc_id in architecture.get("stubs", []):
            blockers.append(f"{component['id']}: architecture doc {doc_id} is still a stub, not yet active")
    return blockers


def doctor_check(repository: Path, task_path: Path, registry: Path) -> list[str]:
    """
    Runs every pre-dispatch gate `_AdapterRuntime.execute` runs before an agent is
    ever dispatched -- architecture coverage, then environment prerequisites -- and
    returns the combined blocker list instead of raising, so both `_AdapterRuntime.
    execute` (which wants to raise) and the standalone `task doctor` CLI command
    (which wants to print) can share one implementation. An empty list means both
    gates are clean; it says nothing about whether the verification gate *commands*
    themselves would actually pass once dispatched.
    """
    with TemporaryDirectory() as temp:
        root = Path(temp)
        context = build_task_context(repository, task_path, root / "task-context.json", registry_path=registry)
        blockers = architecture_coverage_blockers(context)
        work_type = load_yaml(task_path)["task"].get("work_type")
        if is_read_only(work_type):
            return blockers  # no verification gates run for read-only work, so no prerequisites to check
        plan = build_verification_plan(repository, task_path, root / "verification-plan.json")
        if work_type == "refactor":
            blockers += plan_blockers(plan)  # the static half of baseline_capture; running it is the run's job
        blockers += check_prerequisites(plan, repository)
        return blockers
