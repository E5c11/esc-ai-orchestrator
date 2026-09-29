from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from esc_exec.claude_client import ClaudeCodeClient
from esc_exec.claude_code_adapter import ClaudeCodeAdapter
from esc_exec.codex_adapter import CodexAdapter, CodexClient
from esc_exec.environment import check_prerequisites
from esc_exec.json_io import write_json
from esc_exec.opencode_adapter import OpenCodeAdapter, OpenCodeClient
from esc_exec.read_only import effective_policy, is_read_only, state_violations
from esc_exec.registry import resolve_route
from esc_exec.task_context import build_task_context, build_verification_plan
from esc_exec.verification_execution import execute_verification_plan
from esc_exec.worktree import repository_state
from esc_exec.yaml_io import write_yaml
from esc_orchestrator.application.doctor import architecture_coverage_blockers


class RunBlockedError(Exception):
    """Common base for every reason `_AdapterRuntime.execute` stops a run and records blockers rather than a bare
    error: the pre-dispatch gates below, and the post-run read-only check. Carries one blocker string per distinct
    problem so `Scheduler._work` can name each one in the run's checkpoint. It checks
    `isinstance(exc, RunBlockedError)` once instead of growing a per-subclass chain."""

    def __init__(self, blockers: list[str]):
        self.blockers = blockers
        super().__init__("; ".join(blockers))


class ReadOnlyViolationError(RunBlockedError):
    """A `plan` or `investigation` run changed the repository. The run is failed and the files are named; nothing
    is reverted automatically, because an automatic revert would itself be a destructive edit to the checkout."""


class PreDispatchBlockerError(RunBlockedError):
    """
    Common base for every "don't even dispatch the agent" gate `_AdapterRuntime.execute`
    runs before `self.adapter.execute(...)` -- currently architecture-coverage gaps
    (`ArchitectureCoverageError`) and unsatisfied environment prerequisites
    (`EnvironmentPrerequisiteError`). Carries one blocker string per distinct gap
    (not just a single opaque message) so `Scheduler._work`'s existing
    exception-driven checkpoint path can name each one individually, the same way a
    not-clean verification result already does. `Scheduler._work` checks
    `isinstance(exc, PreDispatchBlockerError)` once, rather than growing a per-subclass
    isinstance chain every time a new pre-dispatch gate is added.
    """


class ArchitectureCoverageError(PreDispatchBlockerError):
    """
    Raised when a task's declared architecture coverage isn't complete -- a
    referenced doc doesn't exist at all, or exists but is still `status: stub`, not
    yet promoted `active` (see plan/active/headless-backdoor-mode.md). Never
    dispatches the adapter in this case: nothing should proceed on under-specified
    guidance when there's no human in a live onboarding conversation to notice the
    gap.
    """


class EnvironmentPrerequisiteError(PreDispatchBlockerError):
    """
    Raised when a task's verification gates declare external prerequisites (env
    vars, TCP services, credential files -- see `esc_exec.environment.
    check_prerequisites`) that aren't satisfied in the current environment. Raised
    strictly before `self.adapter.execute(...)` -- see
    plan/active/pre-flight-doctor-and-gate-prerequisites.md: the whole point is to
    catch a wasted, subscription-metered dispatch before it happens, not after a
    gate command fails deep in a real run.
    """


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
        work_type = contracts["task"]["task"].get("work_type")
        read_only = is_read_only(work_type)
        # The authoritative place the policy is decided: every submitted task (the CLI, `api.py`'s POST /tasks,
        # automatic advancement) passes through here, so a read-only work type cannot be run with edit rights by
        # supplying a permissive policy from somewhere else.
        contracts = {**contracts, "policy": effective_policy(contracts["policy"], work_type)}
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
            blockers = architecture_coverage_blockers(context)
            if blockers:
                raise ArchitectureCoverageError(blockers)
            plan = None
            if not read_only:
                plan = build_verification_plan(repository, paths["task"], root / "verification-plan.json")
                # Pre-flight environment check -- see
                # plan/active/pre-flight-doctor-and-gate-prerequisites.md. Resolves the
                # plan's declared gate prerequisites (env vars, TCP services, credential
                # files) against the real local environment before spending a real,
                # subscription-metered agent dispatch on a task whose build can't even
                # resolve its dependencies or reach the services it needs.
                prerequisite_blockers = check_prerequisites(plan, repository)
                if prerequisite_blockers:
                    raise EnvironmentPrerequisiteError(prerequisite_blockers)
            before = repository_state(repository) if read_only else None
            run_dir = self.adapter.execute(
                paths["task"], paths["workspace"], paths["adapter"], paths["policy"]
            )
            if read_only:
                # No verification gates for read-only work (its procedure has no `verify` stage: nothing was
                # supposed to change, and a test run can itself write build output that would read as a violation).
                # Instead the backstop: compare the repository before and after, whatever the adapter and policy.
                after = repository_state(repository)
                violations = state_violations(before, after)
                check = {"checked": before is not None and after is not None, "violations": violations}
                write_json(run_dir / "read-only-check.json", check)
                if violations:
                    raise ReadOnlyViolationError([
                        f"a read-only {work_type} run changed the repository: {violation}" for violation in violations
                    ])
                return run_dir
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
