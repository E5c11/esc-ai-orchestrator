"""BLA-44 slice 3: `refactor` -- the baseline_capture stage, end to end through the real runtime and scheduler."""
import json
import unittest
from pathlib import Path

from esc_exec.manifests import component_manifest_path
from esc_exec.planning import generate_single_repository_workflow
from esc_exec.yaml_io import write_yaml
from esc_orchestrator.application.doctor import doctor_check
from esc_orchestrator.entrypoints.cli.render import render_execution_result
from esc_orchestrator.runtime import _AdapterRuntime
from esc_orchestrator.scheduler import Scheduler
from tests.test_orchestrator import contracts
from tests.test_read_only_workflows import _Case
from tests.test_verification_tree import _WorktreeAdapter


class _CountingAdapter(_WorktreeAdapter):
    def __init__(self, repository, content):
        super().__init__(repository, content)
        self.calls = 0

    def execute(self, *args):
        self.calls += 1
        return super().execute(*args)


class RefactorTests(_Case):
    def profile(self, *checks):
        """Replace the component's verification profile with these (id, shell) checks and commit it."""
        path = component_manifest_path(self.repo, "content").parent / "esc-verification-profile.yaml"
        write_yaml(path, {
            "schema_version": 1, "profile": {"id": "content-verification", "component": "content"},
            "gates": {"focused": [], "component": [], "impact": [], "final": [{"id": i, "command": ["sh", "-c", c]} for i, c in checks]},
        })

    def refactor(self, adapter, work_type="refactor"):
        runtime = _AdapterRuntime()
        runtime.adapter, runtime.registry = adapter, self.registry
        scheduler = Scheduler(self.store, runtime, self.registry)
        task_contracts = contracts()
        task_contracts["task"]["scope"] = {"components": ["content"]}
        task_contracts["task"]["task"]["work_type"] = work_type
        task_contracts["workspace"]["workspace"]["kind"] = "worktree"
        try:
            _, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()
        finally:
            scheduler.close()
        return run_id, self.store.get_run(run_id)

    def blockers(self, run_id):
        return self.store.output_yaml(run_id, "checkpoint.yaml")["progress"]["blockers"]

    def test_a_behaviour_preserving_refactor_succeeds_and_keeps_the_baseline(self):
        self.profile(("keeps-main", "grep -q 'fun main' content/Main.kt"))
        self.init_git()
        run_id, run = self.refactor(_CountingAdapter(self.repo, "fun main() { /* renamed internals */ }\n"))
        self.assertEqual("succeeded", run["status"], run.get("error"))
        baseline = json.loads((Path(run["output_path"]) / "baseline-verification-result.json").read_text())
        self.assertEqual("passed", baseline["status"])

    def test_a_refactor_that_changes_behaviour_fails_verification_in_the_tree_it_changed(self):
        self.profile(("keeps-main", "grep -q 'fun main' content/Main.kt"))
        self.init_git()
        run_id, run = self.refactor(_CountingAdapter(self.repo, "fun renamed() {}\n"))
        self.assertEqual("failed", run["status"])
        self.assertIn("verification failed: final.keeps-main", run["error"])

    def test_a_baseline_that_is_already_failing_refuses_the_refactor_before_the_agent_runs(self):
        self.profile(("already-broken", "false"))
        self.init_git()
        adapter = _CountingAdapter(self.repo, "fun main() {}\n")
        run_id, run = self.refactor(adapter)
        self.assertEqual("failed", run["status"])
        self.assertEqual(0, adapter.calls)
        self.assertEqual(1, len(self.blockers(run_id)))
        self.assertIn("final.already-broken is failed before any change", self.blockers(run_id)[0])
        self.assertIn("escape-ai fix", self.blockers(run_id)[0])

    def test_a_refactor_with_no_runnable_check_is_refused_not_vacuously_verified(self):
        self.profile()  # a profile with no checks at all
        self.init_git()
        adapter = _CountingAdapter(self.repo, "fun main() {}\n")
        run_id, run = self.refactor(adapter)
        self.assertEqual("failed", run["status"])
        self.assertEqual(0, adapter.calls)
        self.assertIn("nothing could show a refactor preserved behaviour", self.blockers(run_id)[0])

    def test_the_same_situation_for_other_work_is_not_blocked(self):
        """Only a refactor claims behaviour is unchanged; a feature with no checks still runs (as before)."""
        self.profile()
        self.init_git()
        adapter = _CountingAdapter(self.repo, "fun main() {}\n")
        run_id, run = self.refactor(adapter, work_type="feature")
        self.assertEqual(1, adapter.calls)  # the agent was dispatched: no baseline gate applies to a feature
        self.assertFalse((Path(run["output_path"]) / "baseline-verification-result.json").exists())

    def test_uncommitted_changes_refuse_the_refactor_because_the_agent_starts_from_the_last_commit(self):
        self.profile(("keeps-main", "grep -q 'fun main' content/Main.kt"))
        self.init_git()
        (self.repo / "content" / "Main.kt").write_text("fun main() { /* work in progress */ }\n")
        adapter = _CountingAdapter(self.repo, "fun main() {}\n")
        run_id, run = self.refactor(adapter)
        self.assertEqual("failed", run["status"])
        self.assertEqual(0, adapter.calls)
        self.assertIn("uncommitted changes (content/Main.kt)", self.blockers(run_id)[0])

    def test_escape_ais_own_untracked_files_do_not_count_as_uncommitted_work(self):
        self.profile(("keeps-main", "grep -q 'fun main' content/Main.kt"))
        self.init_git()
        (self.repo / ".esc-ai" / "workflows" / "active" / "refactor-x").mkdir(parents=True)
        (self.repo / ".esc-ai" / "workflows" / "active" / "refactor-x" / "task.yaml").write_text("x: 1\n")
        adapter = _CountingAdapter(self.repo, "fun main() {}\n")
        run_id, run = self.refactor(adapter)
        self.assertEqual(1, adapter.calls, run.get("error"))

    def test_doctor_reports_a_refactor_with_no_runnable_check_without_running_anything(self):
        self.profile()
        self.init_git()
        task_path, _ = generate_single_repository_workflow(
            self.repo, "repo", "refactor-x", "Tidy the export.", "refactor", ["content"], "", ["behaviour unchanged"],
        )
        blockers = doctor_check(self.repo, task_path, self.registry)
        self.assertTrue(any("nothing could show a refactor preserved behaviour" in blocker for blocker in blockers), blockers)
        feature_path, _ = generate_single_repository_workflow(
            self.repo, "repo", "feature-x", "Add export.", "feature", ["content"], "", ["done"],
        )
        self.assertEqual([], doctor_check(self.repo, feature_path, self.registry))

    def test_the_report_says_behaviour_was_checked_before_and_after(self):
        report = render_execution_result({
            "run_id": "r", "attempt": 1, "status": "succeeded", "baseline": {"passed_checks": 3},
        })
        self.assertIn("Behaviour: 3 check(s) passed on the untouched code and the same checks passed after the change", report)
        self.assertNotIn("Behaviour:", render_execution_result({"run_id": "r", "attempt": 1, "status": "failed", "baseline": {"passed_checks": 3}}))
        self.assertNotIn("Behaviour:", render_execution_result({"run_id": "r", "attempt": 1, "status": "succeeded", "baseline": None}))


class RefactorHelpTests(unittest.TestCase):
    def test_help_documents_the_gate_its_cost_and_its_limit(self):
        from esc_orchestrator import escape_ai_cli as cli
        text = next(a for a in cli.build_parser()._subparsers._group_actions).choices["refactor"].format_help()
        for expected in ("baseline_capture gate", "verification alone reports `passed` when", "uncommitted changes", "runs twice", "nothing checks that"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
