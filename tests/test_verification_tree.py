"""Regression (found while building BLA-44): verification ran in the live checkout even when the agent edited a
disposable worktree, so a change that broke the build was reported `succeeded` and one that fixed it `failed`."""
import json
import unittest
from pathlib import Path

from esc_exec.manifests import component_manifest_path
from esc_exec.worktree import ensure_worktree, finalize_worktree, worktree_branch
from esc_exec.yaml_io import write_yaml
from esc_orchestrator.runtime import _AdapterRuntime
from esc_orchestrator.scheduler import Scheduler
from tests.test_orchestrator import contracts
from tests.test_read_only_workflows import _Case


class _WorktreeAdapter:
    """What ClaudeCodeAdapter does with workspace.kind == worktree: edit the worktree, finalize it, and record
    `bindings.worktree` in run.json."""

    def __init__(self, repository: Path, content: str):
        self.repository, self.content = repository, content

    def execute(self, task_path, workspace_path, adapter_path, policy_path):
        worktree = ensure_worktree(self.repository, "task-1")
        (worktree / "content" / "Main.kt").write_text(self.content)
        kept = finalize_worktree(self.repository, "task-1", "agent change")
        return _finish(self.repository, {"worktree": {"branch": worktree_branch("task-1"), "kept": kept}})


class _LiveCheckoutAdapter:
    """What the Codex and OpenCode adapters do: edit the live checkout and record no worktree."""

    def __init__(self, repository: Path, content: str):
        self.repository, self.content = repository, content

    def execute(self, task_path, workspace_path, adapter_path, policy_path):
        (self.repository / "content" / "Main.kt").write_text(self.content)
        return _finish(self.repository, {})


def _finish(repository: Path, bindings: dict) -> Path:
    run_dir = repository / ".esc-ai" / "runs" / "run-x"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps({"summary": "done"}))
    (run_dir / "run.json").write_text(json.dumps({"bindings": bindings}))
    return run_dir


class VerificationTreeTests(_Case):
    def check_that(self, shell: str) -> None:
        profile = component_manifest_path(self.repo, "content").parent / "esc-verification-profile.yaml"
        write_yaml(profile, {
            "schema_version": 1, "profile": {"id": "content-verification", "component": "content"},
            "gates": {"focused": [], "component": [], "impact": [], "final": [{"id": "check", "command": ["sh", "-c", shell]}]},
        })
        self.init_git()

    def run_with(self, adapter, kind):
        runtime = _AdapterRuntime()
        runtime.adapter, runtime.registry = adapter, self.registry
        scheduler = Scheduler(self.store, runtime, self.registry)
        task_contracts = contracts()
        task_contracts["task"]["scope"] = {"components": ["content"]}
        task_contracts["task"]["task"]["work_type"] = "feature"
        task_contracts["workspace"]["workspace"]["kind"] = kind
        try:
            _, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()
        finally:
            scheduler.close()
        return self.store.get_run(run_id)

    def test_a_worktree_change_that_fixes_the_check_is_seen_as_fixing_it(self):
        self.check_that("grep -q FIXED content/Main.kt")
        run = self.run_with(_WorktreeAdapter(self.repo, "FIXED\n"), "worktree")
        self.assertEqual("succeeded", run["status"], run.get("error"))

    def test_a_worktree_change_that_breaks_the_check_is_caught(self):
        self.check_that("! grep -q BROKEN content/Main.kt")
        run = self.run_with(_WorktreeAdapter(self.repo, "BROKEN\n"), "worktree")
        self.assertEqual("failed", run["status"])
        self.assertIn("verification failed: final.check", run["error"])

    def test_an_agent_that_edits_the_live_checkout_is_verified_there(self):
        self.check_that("! grep -q BROKEN content/Main.kt")
        run = self.run_with(_LiveCheckoutAdapter(self.repo, "BROKEN\n"), "local")
        self.assertEqual("failed", run["status"])
        self.check_that("grep -q FIXED content/Main.kt")

    def test_logs_and_the_verification_result_are_still_written_into_the_live_run_directory(self):
        self.check_that("grep -q FIXED content/Main.kt")
        run = self.run_with(_WorktreeAdapter(self.repo, "FIXED\n"), "worktree")
        run_dir = Path(run["output_path"])
        result = json.loads((run_dir / "verification-result.json").read_text())
        check = next(c for gate in result["gates"] for c in gate["checks"] if c["id"] == "check")
        self.assertTrue((self.repo / check["stdout_path"]).is_file())

    def test_an_agent_cannot_rewrite_its_own_gates_by_editing_the_profile_in_its_worktree(self):
        self.check_that("grep -q NEVER content/Main.kt")

        class Cheater(_WorktreeAdapter):
            def execute(self, task_path, workspace_path, adapter_path, policy_path):
                worktree = ensure_worktree(self.repository, "task-1")
                profile = component_manifest_path(worktree, "content").parent / "esc-verification-profile.yaml"
                write_yaml(profile, {
                    "schema_version": 1, "profile": {"id": "content-verification", "component": "content"},
                    "gates": {"focused": [], "component": [], "impact": [], "final": [{"id": "check", "command": ["true"]}]},
                })
                kept = finalize_worktree(self.repository, "task-1", "agent change")
                return _finish(self.repository, {"worktree": {"branch": worktree_branch("task-1"), "kept": kept}})

        run = self.run_with(Cheater(self.repo, ""), "worktree")
        self.assertEqual("failed", run["status"])  # the plan came from the live checkout, so `true` never replaced the check


if __name__ == "__main__":
    unittest.main()
