from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from esc_exec.claude_code_adapter import ClaudeCodeAdapter, ClaudeCodeClient
from esc_exec.codex_adapter import CodexAdapter, CodexClient
from esc_exec.opencode_adapter import OpenCodeAdapter, OpenCodeClient
from esc_exec.registry import resolve_route
from esc_exec.task_context import build_task_context, build_verification_plan
from esc_exec.verification_execution import execute_verification_plan
from esc_exec.json_io import write_json
from esc_exec.yaml_io import write_yaml


class ArchitectureCoverageError(Exception):
    """
    Raised by `_AdapterRuntime.execute` when a task's declared architecture
    coverage isn't complete -- a referenced doc doesn't exist at all, or exists
    but is still `status: stub`, not yet promoted `active` (see
    plan/active/headless-backdoor-mode.md). Never dispatches the adapter in this
    case: nothing should proceed on under-specified guidance when there's no
    human in a live onboarding conversation to notice the gap.

    Carries one blocker string per incomplete doc (not just a single opaque
    message) so `Scheduler._work`'s existing exception-driven checkpoint path --
    the same one an uncaught exception already uses -- can name each gap
    individually, the same way a not-clean verification result already does.
    """
    def __init__(self, blockers: list[str]):
        self.blockers = blockers
        super().__init__("; ".join(blockers))


def _architecture_coverage_blockers(context: dict[str, Any]) -> list[str]:
    blockers = []
    for component in context["routing"]["components"]:
        architecture = component.get("architecture") or {}
        for doc_id in architecture.get("missing", []):
            blockers.append(f"{component['id']}: architecture doc {doc_id} does not exist")
        for doc_id in architecture.get("stubs", []):
            blockers.append(f"{component['id']}: architecture doc {doc_id} is still a stub, not yet active")
    return blockers


class _AdapterRuntime:
    """
    Shared `Runtime.execute(contracts) -> Path` glue: write the in-memory portable
    contracts to temp YAML, build the verification plan, delegate to whichever
    adapter this runtime wraps, then independently execute that plan's gates
    ourselves -- the agent's own report of what it did is never the authoritative
    result (see plan/active/task-orchestration-and-verification-loop.md). Provider-
    agnostic -- unlike tools_for_policy/token translation, nothing here depends on
    which agent runtime did the actual editing.
    """
    adapter: Any
    registry: Path

    def execute(self, contracts: dict[str, Any]) -> Path:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for kind in ("task", "workspace", "adapter", "policy"):
                paths[kind] = root / f"{kind}.yaml"
                write_yaml(paths[kind], contracts[kind])
            repository = resolve_route(
                self.registry, "repositories", contracts["task"]["task"]["repository"]
            )
            # Coverage gate before any agent work happens -- see
            # plan/active/headless-backdoor-mode.md task 1. build_task_context runs
            # again inside the adapter itself for the real instruction bundle; a
            # second cheap, local-file-only call here is accepted so the gate can
            # run strictly before dispatch, not after inspecting the adapter's
            # internal state.
            context = build_task_context(
                repository, paths["task"], root / "task-context.json", registry_path=self.registry
            )
            blockers = _architecture_coverage_blockers(context)
            if blockers:
                raise ArchitectureCoverageError(blockers)
            plan = build_verification_plan(repository, paths["task"], root / "verification-plan.json")
            run_dir = self.adapter.execute(
                paths["task"], paths["workspace"], paths["adapter"], paths["policy"]
            )
            write_json(run_dir / "verification-plan.json", plan)
            execute_verification_plan(plan, repository, run_dir)
            return run_dir


class OpenCodeRuntime(_AdapterRuntime):
    def __init__(self, server: str, registry: Path):
        self.adapter = OpenCodeAdapter(OpenCodeClient(server), registry)
        self.registry = registry


class ClaudeCodeRuntime(_AdapterRuntime):
    def __init__(self, registry: Path, binary: str = "claude"):
        self.adapter = ClaudeCodeAdapter(ClaudeCodeClient(binary), registry)
        self.registry = registry


class CodexRuntime(_AdapterRuntime):
    def __init__(self, registry: Path, binary: str = "codex"):
        self.adapter = CodexAdapter(CodexClient(binary), registry)
        self.registry = registry
