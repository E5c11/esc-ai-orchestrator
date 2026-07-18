import builtins
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.registry import add_route

from esc_orchestrator import escape_ai_cli as cli
from esc_orchestrator.store import Store


def _make_gradle_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle.kts").write_text(
        'rootProject.name = "repo"\ninclude(":content")\n', encoding="utf-8",
    )
    (root / "content/src/main/kotlin").mkdir(parents=True)
    (root / "content/build.gradle.kts").write_text("", encoding="utf-8")


class RenderingTests(unittest.TestCase):
    """Pure rendering tests -- fake data only, never exercises real onboarding logic."""

    def test_render_menu_lists_all_six_items_in_order(self):
        rendered = cli.render_menu()
        for index, item in enumerate(cli.MENU, 1):
            self.assertIn(f"{index}. {item}", rendered)

    def test_render_proposal_shows_files_questions_and_suggestions(self):
        proposal = {
            "repository": {"id": "repo", "type": "gradle-multi-project"},
            "files": [{"path": "esc-execution.yaml", "action": "create", "evidence": "no manifest found"}],
            "semantic_questions": [{"component_id": "content", "field": "purpose", "prompt": "What is the purpose?"}],
            "profile_id_suggestions": {"content": ["ARCH-BE"]},
            "existing_adoption": {"instructions_file": True, "workflows_directory": False, "project_profile": False},
        }
        rendered = cli.render_proposal(proposal)
        self.assertIn("[create", rendered)
        self.assertIn("esc-execution.yaml", rendered)
        self.assertIn("ARCH-BE", rendered)
        self.assertIn("instructions_file", rendered)
        self.assertIn("1 question(s) require your input", rendered)

    def test_render_proposal_with_no_questions_says_ready(self):
        proposal = {
            "repository": {"id": "repo", "type": "gradle-multi-project"},
            "files": [], "semantic_questions": [], "profile_id_suggestions": {}, "existing_adoption": {},
        }
        self.assertIn("ready to apply", cli.render_proposal(proposal))

    def test_render_apply_result_shows_written_files_and_warnings(self):
        result = {
            "written": ["esc-execution.yaml", "content/esc-component.yaml"],
            "workflow_inheritance": {"created": ["INSTRUCTIONS.md"], "existing": [".esc-ai/workflows/README.md"]},
            "stub_documents": {"content": ["ORCH-BE-FEAT"]},
            "missing_documents": {"content": ["ARCH-NOT-REAL"]},
            "empty_profile_id_suggestions": ["other"],
        }
        rendered = cli.render_apply_result(result)
        self.assertIn("esc-execution.yaml", rendered)
        self.assertIn("INSTRUCTIONS.md", rendered)
        self.assertIn("left untouched", rendered)
        self.assertIn("stub architecture documents", rendered)
        self.assertIn("ORCH-BE-FEAT", rendered)
        self.assertIn("could not be resolved", rendered)
        self.assertIn("No architecture.profile_ids could be suggested for: other", rendered)
        self.assertIn("Nothing has been committed", rendered)

    def test_render_status_and_validation(self):
        self.assertIn("registered: True", cli.render_status({"registered": True}))
        from esc_exec.model import ManifestState, ValidationResult
        results = [ValidationResult(ManifestState.INVALID, "path/to/file", ["bad thing"])]
        rendered = cli.render_validation(results)
        self.assertIn("INVALID", rendered)
        self.assertIn("bad thing", rendered)


class NonInteractiveDispatchTests(unittest.TestCase):
    """End-to-end: real analyze_repository/apply_onboarding_answers against a real repo."""

    def test_add_analyze_answer_apply_validate_status(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            answers_file = root / "answers.json"
            answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            code, out = run(["repository", "add", "repo", str(repository_dir)])
            self.assertEqual(0, code)
            self.assertIn("REGISTERED", out)

            code, out = run(["repository", "analyze", "repo", "--json"])
            self.assertEqual(0, code)
            proposal = json.loads(out)
            self.assertEqual("repo", proposal["repository"]["id"])
            self.assertTrue(any(q["field"] == "purpose" for q in proposal["semantic_questions"]))

            code, out = run(["repository", "answer", "repo", str(answers_file)])
            self.assertEqual(0, code)
            self.assertIn("STORED", out)

            code, out = run(["repository", "apply", "repo"])
            self.assertEqual(0, code)
            self.assertIn("Applied.", out)
            self.assertTrue((repository_dir / "INSTRUCTIONS.md").is_file())
            self.assertTrue((repository_dir / ".esc-ai" / "workflows" / "README.md").is_file())
            manifest_text = (repository_dir / "content" / "esc-component.yaml").read_text(encoding="utf-8")
            self.assertIn("Owns content.", manifest_text)

            code, out = run(["repository", "validate", "repo"])
            self.assertIn("VALID", out)

            code, out = run(["repository", "status", "repo"])
            self.assertEqual(0, code)
            self.assertIn("has_applied_answers: True", out)

    def test_apply_without_pending_answers_is_incomplete(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            add_route(registry, "repositories", "repo", repository_dir)

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.main(["--db", str(db), "--registry", str(registry), "repository", "apply", "repo"])
            self.assertEqual(2, code)
            self.assertIn("INCOMPLETE", buffer.getvalue())


class InteractiveOnboardingTests(unittest.TestCase):
    """End-to-end via the interactive path: real business logic, scripted input()."""

    def test_full_interactive_onboarding_writes_real_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            responses = iter([
                str(repository_dir),  # repository path
                "Owns content.",      # answer to the purpose question
                "y",                  # confirm apply
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("Applied.", output)
            self.assertIn("Nothing has been committed", output)
            self.assertTrue((repository_dir / "esc-execution.yaml").is_file())
            self.assertTrue((repository_dir / "INSTRUCTIONS.md").is_file())
            self.assertIn("Owns content.", (repository_dir / "content" / "esc-component.yaml").read_text(encoding="utf-8"))

    def test_cancelling_before_confirmation_writes_nothing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            responses = iter([str(repository_dir), "Owns content.", "n"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(0, code)
            self.assertIn("Cancelled", buffer.getvalue())
            self.assertFalse((repository_dir / "esc-execution.yaml").exists())
            self.assertFalse((repository_dir / "INSTRUCTIONS.md").exists())

    def test_resuming_unchanged_repository_detects_existing_proposal(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            first_responses = iter([str(repository_dir), "Owns content.", "y"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(first_responses)
            try:
                with redirect_stdout(io.StringIO()):
                    cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            second_responses = iter([str(repository_dir), "n"])
            builtins.input = lambda prompt="": next(second_responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(0, code)
            self.assertIn("resuming", buffer.getvalue())
            self.assertIn("already onboarded", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
