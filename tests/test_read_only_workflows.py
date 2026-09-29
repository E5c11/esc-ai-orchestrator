"""BLA-44 slice 1-2: `plan` and `investigate` cannot change the repository -- forced policy, the before/after
check, success semantics, and the findings in the report. Drives the real runtime and scheduler with a fake
adapter that really edits, commits, or does nothing."""
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from esc_exec.registry import add_route
from esc_exec.yaml_io import load_yaml
from esc_orchestrator import escape_ai_cli as cli
from esc_orchestrator.application.doctor import doctor_check
from esc_orchestrator.entrypoints.cli.render import render_execution_preview, render_execution_result
from esc_orchestrator.runtime import ReadOnlyViolationError, RunBlockedError, _AdapterRuntime
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.store import Store
from tests import test_orchestrator
from tests.test_escape_ai_cli import _app
from tests.test_orchestrator import contracts


def git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, check=True)


class _Adapter:
    """Stands in for a real agent adapter: does `action(repository)` while 'running', records what it was given."""

    def __init__(self, repository: Path, action=None, summary="Nothing to report."):
        self.repository, self.action, self.summary = repository, action, summary
        self.policy = None
        self.called = False

    def execute(self, task_path, workspace_path, adapter_path, policy_path):
        self.called = True
        self.policy = load_yaml(Path(policy_path))
        if self.action:
            self.action(self.repository)
        run_dir = self.repository / ".esc-ai" / "runs" / "run-under-test"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "summary.json").write_text(json.dumps({"summary": self.summary, "provider": "fake"}))
        return run_dir


class _Case(unittest.TestCase):
    _repository_with_component = test_orchestrator.OrchestratorTests._repository_with_component

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo-checkout"
        self._repository_with_component(self.repo, "content", passing_verification=True)
        (self.repo / "content" / "Main.kt").write_text("fun main() {}\n")
        self.registry = self.root / "registry.yaml"
        add_route(self.registry, "repositories", "repo", self.repo)
        self.store = Store(self.root / "db.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def init_git(self):
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "T")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

    def run_task(self, work_type, adapter, allow_all_policy=True):
        runtime = _AdapterRuntime()
        runtime.adapter, runtime.registry = adapter, self.registry
        scheduler = Scheduler(self.store, runtime, self.registry)
        task_contracts = contracts()
        task_contracts["task"]["scope"] = {"components": ["content"]}
        task_contracts["task"]["task"]["work_type"] = work_type
        if allow_all_policy:
            task_contracts["policy"]["permissions"] = {
                "read": "allow", "edit": "allow", "execute": "allow", "network": "allow", "external_paths": "deny",
            }
        try:
            _, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()
        finally:
            scheduler.close()
        return run_id, self.store.get_run(run_id)


class ReadOnlyEnforcementTests(_Case):
    def setUp(self):
        super().setUp()
        self.init_git()

    def edit(self, repository):
        (repository / "content" / "Main.kt").write_text("fun main() { sneaky() }\n")

    def test_a_clean_read_only_run_is_a_success_not_no_changes_and_records_that_it_was_checked(self):
        for work_type in ("plan", "investigation"):
            with self.subTest(work_type=work_type):
                adapter = _Adapter(self.repo)
                run_id, run = self.run_task(work_type, adapter)
                self.assertEqual("succeeded", run["status"])
                check = json.loads((Path(run["output_path"]) / "read-only-check.json").read_text())
                self.assertEqual({"checked": True, "violations": []}, check)
                self.store = Store(self.root / f"db-{work_type}.sqlite")  # fresh store: task ids repeat

    def test_editing_a_file_fails_the_run_names_it_and_does_not_revert_it(self):
        adapter = _Adapter(self.repo, self.edit)
        run_id, run = self.run_task("investigation", adapter)
        self.assertEqual("failed", run["status"])
        self.assertIn("read-only investigation run changed the repository", run["error"])
        self.assertIn("content/Main.kt", run["error"])
        blockers = self.store.output_yaml(run_id, "checkpoint.yaml")["progress"]["blockers"]
        self.assertEqual(1, len(blockers))
        self.assertIn("content/Main.kt", blockers[0])
        self.assertIn("sneaky", (self.repo / "content" / "Main.kt").read_text())  # reported, never reverted

    def test_creating_a_file_or_committing_also_fails_the_run(self):
        cases = {
            "create": lambda repository: (repository / "new.txt").write_text("x"),
            "commit": lambda repository: (self.edit(repository), git(repository, "commit", "-qam", "sneaky")),
        }
        for name, action in cases.items():
            with self.subTest(action=name):
                _, run = self.run_task("plan", _Adapter(self.repo, action))
                self.assertEqual("failed", run["status"], run.get("error"))
                self.store = Store(self.root / f"db-{name}.sqlite")
                git(self.repo, "reset", "-q", "--hard", "HEAD~1") if name == "commit" else (self.repo / "new.txt").unlink()

    def test_an_agent_that_ignores_permissions_is_still_caught(self):
        """The whole point of the backstop: the check does not depend on the policy having been honoured."""
        adapter = _Adapter(self.repo, self.edit)
        _, run = self.run_task("plan", adapter)
        self.assertEqual("deny", adapter.policy["permissions"]["edit"])  # it was denied...
        self.assertEqual("failed", run["status"])  # ...and the edit was caught anyway

    def test_the_adapter_is_given_a_forced_read_only_policy_whatever_was_submitted(self):
        for work_type in ("plan", "investigation"):
            with self.subTest(work_type=work_type):
                adapter = _Adapter(self.repo)
                self.run_task(work_type, adapter, allow_all_policy=True)
                permissions = adapter.policy["permissions"]
                self.assertEqual("allow", permissions["read"])
                for category in ("edit", "execute", "network"):
                    self.assertEqual("deny", permissions[category])
                self.store = Store(self.root / f"db-policy-{work_type}.sqlite")

    def test_work_that_may_edit_keeps_the_policy_it_was_given(self):
        adapter = _Adapter(self.repo, self.edit)
        _, run = self.run_task("feature", adapter, allow_all_policy=True)
        self.assertEqual("allow", adapter.policy["permissions"]["edit"])
        self.assertNotEqual("failed", run["status"], run.get("error"))

    def test_read_only_work_runs_no_verification_gates(self):
        adapter = _Adapter(self.repo)
        _, run = self.run_task("investigation", adapter)
        run_dir = Path(run["output_path"])
        self.assertFalse((run_dir / "verification-result.json").exists())
        self.assertFalse((run_dir / "verification-plan.json").exists())

    def test_a_violation_is_a_run_blocked_error_carrying_each_file(self):
        def write_two_files(repository):
            (repository / "a.txt").write_text("1")
            (repository / "b.txt").write_text("2")

        runtime = _AdapterRuntime()
        runtime.adapter, runtime.registry = _Adapter(self.repo, write_two_files), self.registry
        task_contracts = contracts()
        task_contracts["task"]["scope"] = {"components": ["content"]}
        task_contracts["task"]["task"]["work_type"] = "plan"
        with self.assertRaises(ReadOnlyViolationError) as caught:
            runtime.execute(task_contracts)
        self.assertIsInstance(caught.exception, RunBlockedError)
        self.assertEqual(2, len(caught.exception.blockers))


class ReadOnlyWithoutGitTests(_Case):
    def test_without_git_the_check_is_skipped_and_says_so_rather_than_passing_silently(self):
        _, run = self.run_task("investigation", _Adapter(self.repo))
        self.assertEqual("succeeded", run["status"])
        check = json.loads((Path(run["output_path"]) / "read-only-check.json").read_text())
        self.assertFalse(check["checked"])


class DoctorTests(_Case):
    def test_read_only_tasks_have_no_verification_prerequisites_to_check(self):
        from esc_exec.planning import generate_single_repository_workflow
        task_path, _ = generate_single_repository_workflow(
            self.repo, "repo", "investigate-x", "How does export work?", "investigation", ["content"], "", ["answered"],
        )
        with patch("esc_orchestrator.application.doctor.check_prerequisites", side_effect=AssertionError("must not be called")):
            self.assertEqual([], doctor_check(self.repo, task_path, self.registry))


class ReportTests(_Case):
    def test_the_result_carries_the_agents_findings_and_the_report_prints_them(self):
        self.init_git()
        from esc_exec.planning import generate_single_repository_workflow
        generate_single_repository_workflow(
            self.repo, "repo", "investigate-x", "How does export work?", "investigation", ["content"], "", ["answered"],
        )
        adapter = _Adapter(self.repo, summary="Export streams rows in export.py:42.\nIt never flushes the last buffer.")
        runtime = _AdapterRuntime()
        runtime.adapter, runtime.registry = adapter, self.registry
        result = _app(self.store, self.registry).execute_task(
            "repo", self.repo, "investigate-x", {"id": "claude", "route": "api-key"}, runtime=runtime,
        )
        self.assertEqual("succeeded", result["status"])
        self.assertTrue(result["read_only"])
        self.assertIn("export.py:42", result["findings"])
        report = render_execution_result(result)
        self.assertIn("Findings:", report)
        self.assertIn("  Export streams rows in export.py:42.", report)
        self.assertIn("Read-only check: repository unchanged", report)
        self.assertNotIn("Validation:", report)

    def test_a_plan_is_headed_plan_and_a_missing_summary_is_said_not_hidden(self):
        report = render_execution_result({
            "run_id": "r", "attempt": 1, "status": "succeeded", "work_type": "plan", "read_only": True,
            "findings": None, "read_only_check": {"checked": False, "violations": []},
        })
        self.assertIn("Plan:", report)
        self.assertIn("the agent returned no summary", report)
        self.assertIn("skipped -- not a git repository", report)

    def test_the_preview_states_what_a_read_only_run_may_not_do(self):
        preview = render_execution_preview(
            "repo", "plan-x", {"task": {"objective": "o", "work_type": "plan"}, "scope": {"components": ["content"]}},
        )
        self.assertIn("Read-only: this plan run may not change the repository", preview)
        self.assertNotIn("Read-only", render_execution_preview(
            "repo", "t", {"task": {"objective": "o", "work_type": "feature"}, "scope": {"components": ["content"]}},
        ))

    def test_the_preview_policy_is_the_forced_one(self):
        from esc_exec.planning import generate_single_repository_workflow
        from esc_orchestrator.application.runs import prepare_task_run
        generate_single_repository_workflow(
            self.repo, "repo", "plan-x", "Plan the export.", "plan", ["content"], "", ["plan written"],
        )
        preview = prepare_task_run(self.store, self.registry, "repo", "plan-x")
        self.assertEqual("deny", preview.policy["permissions"]["edit"])
        self.assertEqual("deny", preview.policy["permissions"]["execute"])


class CliHelpTests(unittest.TestCase):
    def test_plan_and_investigate_document_inputs_behaviour_and_outputs(self):
        parser = cli.build_parser()
        subparsers = next(action for action in parser._subparsers._group_actions).choices
        for verb, output in (("plan", "a Plan"), ("investigate", "Findings")):
            with self.subTest(verb=verb):
                text = subparsers[verb].format_help()
                for expected in ("Read-only, enforced", "Input:", "Output:", output, "Limits:", "FAILS and names the files"):
                    self.assertIn(expected, text)

    def test_the_draft_output_for_a_read_only_verb_repeats_the_guarantee(self):
        with TemporaryDirectory() as temp:
            from tests.test_escape_ai_cli import IntentVerbTests
            case = IntentVerbTests()
            run, _ = case._setup(Path(temp))
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code, out = run(["investigate", "How does export work?", "-r", "repo"])
            self.assertEqual(0, code)
            self.assertIn("Read-only, enforced", out)


if __name__ == "__main__":
    unittest.main()
