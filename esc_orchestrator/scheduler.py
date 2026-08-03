from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Protocol, Any

from esc_orchestrator.initiative import analyze_task_impact
from esc_orchestrator.runtime import PreDispatchBlockerError
from esc_orchestrator.store import Store
from esc_exec.checkpoints import checkpoint_document
from esc_exec.registry import resolve_route
from esc_exec.yaml_io import load_yaml, write_yaml


class Runtime(Protocol):
    def execute(self, contracts: dict[str, Any]) -> Path: ...


def _verification_result(output: Path) -> dict[str, Any] | None:
    """
    Reads the `verification-result.json` a `Runtime.execute()` call should have
    produced (`_AdapterRuntime` always writes one via `execute_verification_plan`).
    Returns None if absent -- a test double that doesn't model verification at all
    (e.g. `FakeRuntime`) still gets the pre-task-5 trust-the-runtime behavior below,
    rather than being forced to fake this file too.
    """
    path = output / "verification-result.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _verification_failures(result: dict[str, Any]) -> list[str]:
    # failure_category (plan/active/pre-flight-doctor-and-gate-prerequisites.md
    # finding #6) is always present on a failed/error check -- see
    # esc_exec.verification_execution.classify_failure -- so a human (or the next
    # agent attempt) reading this blocker doesn't have to re-derive it from a raw
    # log every time.
    return [
        f"{gate['id']}.{check['id']} ({check['status']}, exit_code={check['exit_code']}, "
        f"category={check.get('failure_category')})"
        for gate in result["gates"]
        for check in gate["checks"]
        if check["status"] in {"failed", "error"}
    ]


def _verification_failure_summary(failures: list[str]) -> str:
    return "verification failed: " + ", ".join(failures)


def _run_produced_changes(output: Path) -> bool:
    """
    Whether a succeeded run actually changed anything, per its own `run.json` --
    see plan/done/run-outcome-surfacing.md finding #9: a run that wrote no code at
    all (a research summary ending in a clarifying question, say) is otherwise
    indistinguishable from a real completed implementation.

    `bindings.worktree.kept` (written by the worktree-isolated Claude Code
    adapter -- `esc_exec.worktree.finalize_worktree`'s return value) is the
    precise signal when present: a worktree is kept only when it has real commits
    ahead of the base branch. Absence of that signal -- no `run.json` at all
    (some test doubles), or a real run.json with no `worktree` binding at all
    (Codex/OpenCode, which don't create a worktree and operate on the live
    checkout directly) -- means "unknown," not "no change." Defaults to True so
    every run without the signal keeps today's unconditional-success behavior
    exactly as it was before this check existed, rather than guessing from a live
    `git diff` that would misclassify synthetic/test repositories with no real git
    history.
    """
    path = output / "run.json"
    if not path.is_file():
        return True
    run_document = json.loads(path.read_text(encoding="utf-8"))
    worktree = run_document.get("bindings", {}).get("worktree")
    if worktree is None:
        return True
    return bool(worktree.get("kept"))


def _permission_denials(output: Path) -> list[dict[str, Any]] | None:
    """
    Reads the `permission-denials.json` a ClaudeCodeAdapter run writes -- see
    plan/future/pre-flight-consent-and-bounded-autonomy.md layer 6. Returns
    None if absent, same discipline as `_verification_result`: every other
    adapter (no per-action permission classifier to report from) and an older
    run predating this file are both read as "no signal here," not an error.
    """
    path = output / "permission-denials.json"
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    return document.get("denials", [])


def _permission_denial_blockers(denials: list[dict[str, Any]]) -> list[str]:
    return [
        f"permission denied: {denial.get('tool_name')} ({json.dumps(denial.get('tool_input'))[:200]})"
        for denial in denials
    ]


def _permission_denial_summary(blockers: list[str]) -> str:
    return "; ".join(blockers)


def _write_checkpoint_candidate(
    store: Store, task_id: str, run_id: str, candidate_dir: Path, blockers: list[str],
) -> None:
    """
    Best-effort transient checkpoint candidate at `candidate_dir/checkpoint.yaml`, for a
    human to review and promote later (see `promote_checkpoint`) -- shared by both
    triggers into this path: an uncaught exception, and (task 6) a run whose adapter
    reported done but whose independently-verified result came back not-clean. Never
    raises itself, so a bug here can't prevent the caller from still recording the run's
    real status/error.
    """
    try:
        task = store.contracts(task_id)["task"]
        document = checkpoint_document(
            task,
            run_id=run_id,
            status="blocked",
            remaining=["Resolve the recorded blocker and resume the task."],
            blockers=blockers,
            last_event_sequence=len(store.events(run_id)) - 1,
        )
        write_yaml(candidate_dir / "checkpoint.yaml", document)
    except Exception:
        pass


class Scheduler:
    def __init__(self, store: Store, runtime: Runtime, registry: Path, workers: int = 5):
        self.store, self.runtime, self.registry = store, runtime, registry
        self.queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self.threads = [threading.Thread(target=self._work, daemon=True) for _ in range(workers)]
        for thread in self.threads:
            thread.start()

    def submit(self, contracts: dict[str, Any]) -> tuple[str, str]:
        task_id, run_id = self.store.submit(contracts)
        self.queue.put((task_id, run_id))
        return task_id, run_id

    def _work(self):
        while True:
            item = self.queue.get()
            if item is None: return
            task_id, run_id = item
            self.store.update_run(run_id, "running")
            try:
                output = self.runtime.execute(self.store.contracts(task_id))
                result = _verification_result(output)
                if result is not None and result["status"] != "passed":
                    failures = _verification_failures(result)
                    _write_checkpoint_candidate(self.store, task_id, run_id, output, failures)
                    self.store.update_run(
                        run_id, "failed", str(output), _verification_failure_summary(failures)
                    )
                elif (denials := _permission_denials(output)):
                    # Layer 6's third checkpoint trigger, alongside verification-
                    # failure above and the uncaught-exception branch below: the
                    # agent itself reported done (no exception, and verification
                    # -- if this task even has any -- passed), but at least one
                    # tool call was denied by the hard-deny list or another
                    # Claude Code permission check. Never auto-advances --
                    # "waiting-approval" means a human needs to decide whether to
                    # broaden the grant and retry, not that the task's objective
                    # was actually reached.
                    blockers = _permission_denial_blockers(denials)
                    _write_checkpoint_candidate(self.store, task_id, run_id, output, blockers)
                    self.store.update_run(
                        run_id, "waiting-approval", str(output), _permission_denial_summary(blockers)
                    )
                elif not _run_produced_changes(output):
                    # A no-op "success" must never look like real completed work
                    # (see plan/done/run-outcome-surfacing.md finding #9) -- a
                    # distinct status, not a repurposed "succeeded", so every
                    # existing `status == "succeeded"` check elsewhere keeps
                    # meaning "real completed work." Never advances: nothing
                    # should unblock a dependent task on a run that changed
                    # nothing.
                    self.store.update_run(run_id, "succeeded-no-changes", str(output))
                else:
                    self.store.update_run(run_id, "succeeded", str(output))
                    # Best-effort: a bug in advancement must never retroactively turn
                    # this already-recorded success into a failure.
                    try:
                        self._advance(task_id)
                    except Exception:
                        pass
            except Exception as exc:
                error = str(exc)[:1000]
                # Any PreDispatchBlockerError (ArchitectureCoverageError,
                # EnvironmentPrerequisiteError -- see
                # plan/active/pre-flight-doctor-and-gate-prerequisites.md) carries
                # one blocker per distinct gap; every other exception still gets the
                # single opaque message, exactly as before.
                blockers = exc.blockers if isinstance(exc, PreDispatchBlockerError) else [error]
                output_path = None
                try:
                    task = self.store.contracts(task_id)["task"]
                    repository = resolve_route(self.registry, "repositories", task["task"]["repository"])
                    candidate_dir = repository / ".esc-ai" / "runs" / run_id
                    _write_checkpoint_candidate(self.store, task_id, run_id, candidate_dir, blockers)
                    output_path = str(candidate_dir)
                except Exception:
                    pass
                self.store.update_run(run_id, "failed", output_path=output_path, error=error)
            finally:
                self.queue.task_done()

    def _advance(self, completed_task_id: str) -> None:
        """
        Event-driven automatic advancement (task 7 of
        task-orchestration-and-verification-loop.md): once a run is independently
        verified clean, check whether that unblocks any other declared task in the
        same initiative (task 2's analyze_task_impact) and, if so, submit it.

        Reuses the just-completed task's own `adapter`/`policy` contracts verbatim --
        this Scheduler was built with exactly one `runtime`, so those two sub-dicts are
        already proven compatible with it by the fact that the completed task just
        ran through it successfully. Per-adapter dispatch across multiple runtimes is
        deferred to native-cli-provider-adapters.md, not this task.

        Only a task with no Store history at all is auto-submitted. One a human
        already touched -- running, queued, or previously failed and awaiting
        checkpoint review -- is left alone; depends_on isn't enforced by `execute_task`
        itself, so a task can legitimately have been run out of order already, and
        auto-advancement must never silently resubmit out from under that.

        Uses `store.submit_if_new` rather than a separate "check, then submit" --
        with a bounded worker pool (task 8), two sibling dependencies of the same
        downstream task can finish concurrently on different workers, and both would
        otherwise independently see it as newly unblocked and submit it twice.
        `submit_if_new` makes the check-and-insert atomic, so only one of them wins.
        """
        impact = analyze_task_impact(self.store, self.registry, completed_task_id)
        if not impact["newly_unblocked"]:
            return
        contracts = self.store.contracts(completed_task_id)
        for node in impact["newly_unblocked"]:
            repository_id, unblocked_task_id = node.split("/", 1)
            repository = resolve_route(self.registry, "repositories", repository_id)
            task_path = repository / ".esc-ai" / "workflows" / "active" / unblocked_task_id / "task.yaml"
            submitted = self.store.submit_if_new({
                "task": load_yaml(task_path),
                # Mirrors escape_ai_cli.default_workspace's shape (can't import
                # it directly -- that module imports Scheduler from here, and
                # a reverse import would be circular). See
                # plan/future/pre-flight-consent-and-bounded-autonomy.md
                # layer 4 -- an auto-advanced task gets the same worktree
                # isolation a manually-dispatched one does, not the old
                # `local`/`process` default.
                "workspace": {
                    "schema_version": 1,
                    "workspace": {
                        "id": f"workspace-{repository_id}-default", "kind": "worktree",
                        "repository": repository_id, "isolation": "filesystem",
                    },
                },
                "adapter": contracts["adapter"],
                "policy": contracts["policy"],
            })
            if submitted is not None:
                self.queue.put(submitted)

    def close(self):
        for _ in self.threads:
            self.queue.put(None)
        for thread in self.threads:
            thread.join(timeout=5)
