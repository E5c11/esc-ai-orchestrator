"""BLA-43: `escape-ai fix` end to end -- the root_cause gate, what the agent is given, and the final report."""
import builtins
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from esc_exec.yaml_io import load_yaml
from esc_orchestrator import escape_ai_cli as cli
from esc_orchestrator.application.planning import root_cause_from_answers
from esc_orchestrator.entrypoints.cli.render import render_execution_preview, render_execution_result
from esc_orchestrator.store import Store
from tests.test_escape_ai_cli import (
    _app,
    _FakeWorktreeSucceedingRuntime,
    _git_init,
    _make_gradle_repository,
)

OBJECTIVE = "CSV export drops the last row."
CAUSE = {
    "statement": "The export loop stops at len(rows) - 1, so the final row is never written.",
    "evidence": ["export.py:42", "test_export_last_row fails"],
    "reproduction": "pytest -k export_last_row",
}
ANSWERS = {"components": {"repo": ["content"]}, "scope_boundary": "", "completion_conditions": ["last row exported"], "rollout_needs": ""}


class _FakeFixRuntime(_FakeWorktreeSucceedingRuntime):
    """Leaves a real diff in a real worktree and a verification summary, like a finished, validated fix."""

    def execute(self, contracts):
        path = super().execute(contracts)
        (path / "verification-summary.json").write_text(json.dumps({
            "schema_version": 1,
            "verification": {"profile": "p", "source_format": "junit-xml", "status": "passed", "generated_at": "2026-09-29T00:00:00Z"},
            "totals": {"tests": 12, "passed": 12, "failed": 0, "errors": 0, "skipped": 0, "duration_ms": 10},
        }), encoding="utf-8")
        return path


class FixWorkflowTests(unittest.TestCase):
    def _setup(self, temp: str):
        root = Path(temp)
        self.db, self.registry = root / "db.sqlite", root / "registry.yaml"
        self.repository = root / "repo-checkout"
        _make_gradle_repository(self.repository)
        _git_init(self.repository)
        self.root = root
        self.store = Store(self.db)
        self.assertEqual(0, self.run_cli(["repository", "add", "repo", str(self.repository)])[0])
        self.run_cli(["repository", "analyze", "repo", "--json"])
        (root / "onboard.json").write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
        self.run_cli(["repository", "answer", "repo", str(root / "onboard.json")])
        self.assertEqual(0, self.run_cli(["repository", "apply", "repo"])[0])

    def run_cli(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["--db", str(self.db), "--registry", str(self.registry), *argv])
        return code, buffer.getvalue()

    def answer(self, initiative, answers):
        path = self.root / f"{initiative}-answers.json"
        path.write_text(json.dumps(answers), encoding="utf-8")
        return self.run_cli(["initiative", "answer", initiative, str(path)])

    def task_dir(self, initiative):
        return self.repository / ".esc-ai" / "workflows" / "active" / initiative

    def test_fix_drafts_ask_for_the_root_cause_first(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            code, out = self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export", "--json"])
            self.assertEqual(0, code, out)
            fields = [question["field"] for question in json.loads(out)["questions"]]
            self.assertEqual(["root_cause_statement", "root_cause_evidence", "root_cause_reproduction"], fields[:3])
            self.assertIn("components", fields)

    def test_the_draft_output_explains_the_gate(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            code, out = self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export"])
            self.assertEqual(0, code, out)
            self.assertIn("root_cause gate", out)
            self.assertIn("cannot check that the cause is *true*", out)

    def test_a_feature_draft_does_not_ask_for_one(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            _, out = self.run_cli(["feature", "Add CSV export.", "-r", "repo", "--json"])
            self.assertFalse([q for q in json.loads(out)["questions"] if q["field"].startswith("root_cause")])

    def test_applying_a_fix_without_a_root_cause_is_incomplete_and_writes_nothing(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export"])
            self.answer("fix-export", ANSWERS)
            code, out = self.run_cli(["initiative", "apply", "fix-export"])
            self.assertEqual(2, code, out)
            self.assertIn("INCOMPLETE", out)
            self.assertIn("root cause", out)
            self.assertIn('"evidence"', out)  # the hint shows the shape to supply
            self.assertFalse(self.task_dir("fix-export").exists())

    def test_a_symptom_only_root_cause_is_rejected(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export"])
            self.answer("fix-export", {**ANSWERS, "root_cause": {"statement": OBJECTIVE, "evidence": ["it is broken"]}})
            code, out = self.run_cli(["initiative", "apply", "fix-export"])
            self.assertEqual(1, code, out)
            self.assertIn("restates the objective", out)
            self.assertFalse(self.task_dir("fix-export").exists())

    def test_a_root_cause_without_evidence_is_rejected(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export"])
            self.answer("fix-export", {**ANSWERS, "root_cause": {"statement": CAUSE["statement"], "evidence": []}})
            code, out = self.run_cli(["initiative", "apply", "fix-export"])
            self.assertEqual(1, code, out)
            self.assertIn("evidence is required", out)

    def test_with_a_root_cause_the_fix_is_planned_and_recorded(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export"])
            self.answer("fix-export", {**ANSWERS, "root_cause": CAUSE})
            code, out = self.run_cli(["initiative", "apply", "fix-export"])
            self.assertEqual(0, code, out)
            document = load_yaml(self.task_dir("fix-export") / "task.yaml")
            self.assertEqual("fix", document["task"]["work_type"])
            self.assertEqual(CAUSE, document["root_cause"])
            self.assertIn("## Root cause", (self.task_dir("fix-export") / "README.md").read_text())

    def test_the_flat_fields_the_interactive_flow_collects_work_too(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export"])
            self.answer("fix-export", {
                **ANSWERS, "root_cause_statement": CAUSE["statement"],
                "root_cause_evidence": "export.py:42; test_export_last_row fails", "root_cause_reproduction": "",
            })
            code, out = self.run_cli(["initiative", "apply", "fix-export"])
            self.assertEqual(0, code, out)
            recorded = load_yaml(self.task_dir("fix-export") / "task.yaml")["root_cause"]
            self.assertEqual(["export.py:42", "test_export_last_row fails"], recorded["evidence"])
            self.assertNotIn("reproduction", recorded)

    def test_a_multi_repository_fix_records_one_cause_in_every_task_and_validates_before_writing(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            second = self.root / "repo-two-checkout"
            _make_gradle_repository(second)
            _git_init(second)
            self.assertEqual(0, self.run_cli(["repository", "add", "repo-two", str(second)])[0])
            self.run_cli(["repository", "analyze", "repo-two", "--json"])
            (self.root / "two.json").write_text(json.dumps({"content": {"purpose": "Second."}}), encoding="utf-8")
            self.run_cli(["repository", "answer", "repo-two", str(self.root / "two.json")])
            self.assertEqual(0, self.run_cli(["repository", "apply", "repo-two"])[0])
            self.run_cli(["fix", OBJECTIVE, "-r", "repo", "-r", "repo-two", "--initiative-id", "fix-multi"])
            multi = {**ANSWERS, "components": {"repo": ["content"], "repo-two": ["content"]}, "depends_on": {"repo-two": ["repo"]}}
            self.answer("fix-multi", {**multi, "root_cause": {"statement": "s", "evidence": []}})
            self.assertEqual(1, self.run_cli(["initiative", "apply", "fix-multi"])[0])
            self.assertFalse((self.repository / ".esc-ai" / "workflows" / "active" / "fix-multi-repo").exists())
            self.assertFalse((second / ".esc-ai" / "workflows" / "active" / "fix-multi-repo-two").exists())
            self.answer("fix-multi", {**multi, "root_cause": CAUSE})
            code, out = self.run_cli(["initiative", "apply", "fix-multi"])
            self.assertEqual(0, code, out)
            for checkout, task_id in ((self.repository, "fix-multi-repo"), (second, "fix-multi-repo-two")):
                document = load_yaml(checkout / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml")
                self.assertEqual(CAUSE, document["root_cause"])

    def test_the_final_report_says_what_was_wrong_what_changed_and_how_it_was_validated(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export"])
            self.answer("fix-export", {**ANSWERS, "root_cause": CAUSE})
            self.assertEqual(0, self.run_cli(["initiative", "apply", "fix-export"])[0])
            runtime = _FakeFixRuntime(self.root / "runs", self.repository, leave_a_diff=True)
            result = _app(self.store, self.registry).execute_task(
                "repo", self.repository, "fix-export", {"id": "claude", "route": "api-key"}, runtime=runtime,
            )
            self.assertEqual("succeeded", result["status"])
            self.assertEqual(CAUSE, result["root_cause"])
            self.assertEqual("passed", result["verification"]["status"])
            report = render_execution_result(result, cli.worktree_diff(self.repository, "fix-export"))
            self.assertIn("Root cause: The export loop stops at len(rows) - 1", report)
            self.assertIn("evidence: export.py:42", report)
            self.assertIn("Validation: passed -- 12 test(s): 12 passed, 0 failed", report)
            self.assertIn("Changes:", report)
            self.assertIn("agent-output.txt", report)

    def test_a_task_that_is_not_a_fix_reports_no_root_cause(self):
        report = render_execution_result({"run_id": "r", "attempt": 1, "status": "succeeded", "root_cause": None, "verification": None})
        self.assertNotIn("Root cause", report)
        self.assertNotIn("Validation", report)

    def test_the_preview_shows_the_recorded_root_cause(self):
        task_document = {
            "task": {"objective": OBJECTIVE}, "scope": {"components": ["content"]}, "root_cause": CAUSE,
        }
        preview = render_execution_preview("repo", "fix-export", task_document)
        self.assertIn("Root cause: The export loop stops", preview)
        self.assertIn("reproduce: pytest -k export_last_row", preview)

    def test_a_hand_edited_fix_task_without_a_root_cause_is_stopped_by_the_doctor(self):
        with TemporaryDirectory() as temp:
            self._setup(temp)
            self.run_cli(["fix", OBJECTIVE, "-r", "repo", "--initiative-id", "fix-export"])
            self.answer("fix-export", {**ANSWERS, "root_cause": CAUSE})
            self.run_cli(["initiative", "apply", "fix-export"])
            task_path = self.task_dir("fix-export") / "task.yaml"
            document = load_yaml(task_path)
            del document["root_cause"]
            from esc_exec.yaml_io import write_yaml
            write_yaml(task_path, document)
            from esc_exec.contracts import validate_contract
            result = validate_contract("task", task_path)
            self.assertTrue(any("root_cause is required for a fix task" in m for m in result.messages))


class RootCauseFromAnswersTests(unittest.TestCase):
    def test_nested_object_is_returned_as_given(self):
        self.assertEqual(CAUSE, root_cause_from_answers({"root_cause": CAUSE}))

    def test_flat_fields_are_assembled_and_a_blank_reproduction_is_dropped(self):
        result = root_cause_from_answers(
            {"root_cause_statement": " cause ", "root_cause_evidence": "a; b", "root_cause_reproduction": "  "},
        )
        self.assertEqual({"statement": "cause", "evidence": "a; b"}, result)

    def test_no_root_cause_at_all_is_none(self):
        self.assertIsNone(root_cause_from_answers({}))
        self.assertIsNone(root_cause_from_answers({"root_cause_statement": " ", "root_cause_evidence": ""}))

    def test_a_partial_flat_root_cause_is_passed_on_so_the_engine_can_name_what_is_missing(self):
        self.assertEqual(
            {"statement": "", "evidence": "x"}, root_cause_from_answers({"root_cause_statement": "", "root_cause_evidence": "x"}),
        )


class InteractiveReclassificationTests(unittest.TestCase):
    """Drift detection can change the work type after the questions were answered."""

    def test_leaving_fix_discards_the_root_cause_and_becoming_fix_asks_for_one(self):
        from esc_orchestrator.entrypoints.cli.interactive.planning import _reconcile_root_cause

        answers = {"root_cause_statement": "s", "root_cause_evidence": "e", "root_cause_reproduction": "r", "scope_boundary": "x"}
        _reconcile_root_cause(answers, "fix", "feature")
        self.assertEqual({"scope_boundary": "x"}, answers)

        responses = iter(["The loop bound is wrong.", "export.py:42", ""])
        original = builtins.input
        builtins.input = lambda prompt="": next(responses)
        try:
            with redirect_stdout(io.StringIO()):
                _reconcile_root_cause(answers, "feature", "fix")
        finally:
            builtins.input = original
        self.assertEqual("The loop bound is wrong.", answers["root_cause_statement"])
        self.assertEqual("export.py:42", answers["root_cause_evidence"])

    def test_an_existing_root_cause_is_not_asked_for_again(self):
        from esc_orchestrator.entrypoints.cli.interactive.planning import _reconcile_root_cause

        answers = {"root_cause_statement": "already", "root_cause_evidence": "have it"}
        _reconcile_root_cause(answers, "maintenance", "fix")  # would raise StopIteration if it prompted
        self.assertEqual("already", answers["root_cause_statement"])


if __name__ == "__main__":
    unittest.main()
