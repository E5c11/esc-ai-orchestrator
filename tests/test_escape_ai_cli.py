import builtins
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.manifests import component_manifest_path
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
            "files": [{"path": ".esc-ai/esc-execution.yaml", "action": "create", "evidence": "no manifest found"}],
            "semantic_questions": [{"component_id": "content", "field": "purpose", "prompt": "What is the purpose?"}],
            "profile_id_suggestions": {"content": ["ARCH-BE"]},
            "existing_adoption": {"instructions_file": True, "workflows_directory": False, "project_profile": False},
        }
        rendered = cli.render_proposal(proposal)
        self.assertIn("[create", rendered)
        self.assertIn(".esc-ai/esc-execution.yaml", rendered)
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
            "written": [".esc-ai/esc-execution.yaml", ".esc-ai/components/content/esc-component.yaml"],
            "workflow_inheritance": {"created": [".esc-ai/INSTRUCTIONS.md"], "existing": [".esc-ai/workflows/README.md"]},
            "stub_documents": {"content": ["ORCH-BE-FEAT"]},
            "missing_documents": {"content": ["ARCH-NOT-REAL"]},
            "empty_profile_id_suggestions": ["other"],
        }
        rendered = cli.render_apply_result(result)
        self.assertIn(".esc-ai/esc-execution.yaml", rendered)
        self.assertIn(".esc-ai/INSTRUCTIONS.md", rendered)
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


class PlanningRenderingTests(unittest.TestCase):
    """Pure rendering tests -- fake data only, never exercises real planning logic."""

    def test_render_work_types_lists_all_five(self):
        rendered = cli.render_work_types()
        for i, work_type in enumerate(cli.WORK_TYPES, 1):
            self.assertIn(f"{i}. {work_type}", rendered)

    def test_render_plan_draft_shows_routing_and_question_count(self):
        draft = {
            "initiative_id": "feature-export", "work_type": "feature",
            "objective": "Add CSV export.",
            "repositories": ["repo-a", "repo-b"],
            "routing": {"repo-a": [{"component_id": "content", "score": 8, "reasons": []}], "repo-b": []},
            "questions": [{"field": "components", "repository": "repo-a", "prompt": "?"}],
        }
        rendered = cli.render_plan_draft(draft)
        self.assertIn("feature-export", rendered)
        self.assertIn("repo-a: suggested [content]", rendered)
        self.assertIn("repo-b: suggested [no matches]", rendered)
        self.assertIn("1 question(s) require your input", rendered)

    def test_render_plan_result_lists_written_files_per_repository(self):
        result = {"repo-a": [".esc-ai/workflows/active/task/task.yaml", ".esc-ai/workflows/active/task/README.md"]}
        rendered = cli.render_plan_result(result)
        self.assertIn("repo-a:", rendered)
        self.assertIn("task.yaml", rendered)
        self.assertIn("Nothing has been committed", rendered)


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
            self.assertTrue((repository_dir / ".esc-ai" / "INSTRUCTIONS.md").is_file())
            self.assertTrue((repository_dir / ".esc-ai" / "workflows" / "README.md").is_file())
            manifest_text = component_manifest_path(repository_dir, "content").read_text(encoding="utf-8")
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


class PlanningDispatchTests(unittest.TestCase):
    """End-to-end: real route_objective/generate_*_workflow against real repos."""

    def _onboard(self, run, repo_id: str, repository_dir: Path, purpose: str) -> None:
        code, _ = run(["repository", "add", repo_id, str(repository_dir)])
        self.assertEqual(0, code)
        code, _ = run(["repository", "analyze", repo_id, "--json"])
        self.assertEqual(0, code)
        answers_file = repository_dir.parent / f"{repo_id}-answers.json"
        answers_file.write_text(json.dumps({"content": {"purpose": purpose}}), encoding="utf-8")
        code, _ = run(["repository", "answer", repo_id, str(answers_file)])
        self.assertEqual(0, code)
        code, out = run(["repository", "apply", repo_id])
        self.assertEqual(0, code, out)

    def test_single_repository_plan_draft_answer_apply(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            self._onboard(run, "repo", repository_dir, "Owns content.")

            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Add CSV export of content.",
                "repositories": ["repo"],
            }), encoding="utf-8")
            code, out = run(["plan", "draft", "feature-export", str(request_file)])
            self.assertEqual(0, code, out)
            self.assertIn("feature-export", out)

            answers_file = root / "plan-answers.json"
            answers_file.write_text(json.dumps({
                "components": {"repo": ["content"]},
                "scope_boundary": "No admin UI.",
                "completion_conditions": ["Export button works"],
                "rollout_needs": "",
            }), encoding="utf-8")
            code, out = run(["plan", "answer", "feature-export", str(answers_file)])
            self.assertEqual(0, code)
            self.assertIn("STORED", out)

            code, out = run(["plan", "apply", "feature-export"])
            self.assertEqual(0, code, out)
            self.assertIn("Planned.", out)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "feature-export"
            self.assertTrue((task_dir / "task.yaml").is_file())
            self.assertTrue((task_dir / "README.md").is_file())
            self.assertIn("Export button works", (task_dir / "README.md").read_text())

            code, out = run(["plan", "status", "feature-export"])
            self.assertEqual(0, code)
            self.assertIn("has_result: True", out)

    def test_multi_repository_plan_cross_links_tasks(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repo_a = root / "repo-a"
            repo_b = root / "repo-b"
            _make_gradle_repository(repo_a)
            _make_gradle_repository(repo_b)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            self._onboard(run, "repo-a", repo_a, "Owns repo-a content.")
            self._onboard(run, "repo-b", repo_b, "Owns repo-b content.")

            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Cross-repo export.",
                "repositories": ["repo-a", "repo-b"],
            }), encoding="utf-8")
            code, out = run(["plan", "draft", "feature-cross", str(request_file)])
            self.assertEqual(0, code, out)

            answers_file = root / "plan-answers.json"
            answers_file.write_text(json.dumps({
                "components": {"repo-a": ["content"], "repo-b": ["content"]},
                "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
            }), encoding="utf-8")
            run(["plan", "answer", "feature-cross", str(answers_file)])

            code, out = run(["plan", "apply", "feature-cross"])
            self.assertEqual(0, code, out)

            from esc_exec.yaml_io import load_yaml
            task_a = load_yaml(repo_a / ".esc-ai/workflows/active/feature-cross-repo-a/task.yaml")
            task_b = load_yaml(repo_b / ".esc-ai/workflows/active/feature-cross-repo-b/task.yaml")
            self.assertEqual("feature-cross", task_a["task"]["initiative"]["id"])
            self.assertNotIn("depends_on", task_a["task"]["initiative"])
            self.assertEqual(["repo-a/feature-cross-repo-a"], task_b["task"]["initiative"]["depends_on"])


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
            self.assertTrue((repository_dir / ".esc-ai" / "esc-execution.yaml").is_file())
            self.assertTrue((repository_dir / ".esc-ai" / "INSTRUCTIONS.md").is_file())
            self.assertIn("Owns content.", component_manifest_path(repository_dir, "content").read_text(encoding="utf-8"))

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
            self.assertFalse((repository_dir / ".esc-ai" / "esc-execution.yaml").exists())
            self.assertFalse((repository_dir / ".esc-ai" / "INSTRUCTIONS.md").exists())

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


class PlanningInteractiveTests(unittest.TestCase):
    def test_full_interactive_planning_writes_real_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(root / "db.sqlite"), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            run(["repository", "add", "repo", str(repository_dir)])
            run(["repository", "analyze", "repo", "--json"])
            answers_file = root / "answers.json"
            answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
            run(["repository", "answer", "repo", str(answers_file)])
            run(["repository", "apply", "repo"])

            responses = iter([
                "1",                     # work type: feature
                "Add CSV export.",       # objective
                "feature-export",        # initiative id
                "repo",                  # repositories
                "content",               # components question answer
                "No admin UI.",          # scope_boundary
                "Export works",          # completion_conditions
                "",                      # rollout_needs
                "y",                     # confirm
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("Planned.", output)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "feature-export"
            self.assertTrue((task_dir / "task.yaml").is_file())
            self.assertIn("Export works", (task_dir / "README.md").read_text())


class ExecutionRenderingTests(unittest.TestCase):
    """Pure rendering tests -- fake data only, never exercises real execution logic."""

    def test_render_active_work_lists_status_attempts_and_checkpoint_flag(self):
        items = [{
            "repository_id": "repo", "task_id": "feature-export", "objective": "Add CSV export.",
            "attempts": 2, "latest_run_status": "failed", "checkpoint_present": True,
        }]
        rendered = cli.render_active_work(items)
        self.assertIn("repo/feature-export", rendered)
        self.assertIn("failed", rendered)
        self.assertIn("2 attempt(s)", rendered)
        self.assertIn("checkpoint candidate pending review", rendered)

    def test_render_active_work_empty(self):
        self.assertIn("No active work", cli.render_active_work([]))

    def test_render_execution_preview_flags_placeholder_policy(self):
        task_document = {
            "task": {"objective": "Add CSV export."},
            "scope": {"components": ["content"]},
        }
        rendered = cli.render_execution_preview("repo", "feature-export", task_document)
        self.assertIn("repo/feature-export", rendered)
        self.assertIn("content", rendered)
        self.assertIn("placeholder", rendered)

    def test_render_execution_result_shows_status_and_error(self):
        rendered = cli.render_execution_result({
            "run_id": "run-1", "attempt": 2, "status": "failed", "error": "boom", "output_path": "/tmp/x",
        })
        self.assertIn("run-1", rendered)
        self.assertIn("attempt 2", rendered)
        self.assertIn("failed", rendered)
        self.assertIn("boom", rendered)

    def test_render_checkpoint_candidate_shows_blockers_and_remaining(self):
        candidate = {
            "run_id": "run-1",
            "checkpoint": {"status": "blocked"},
            "progress": {"blockers": ["provider unavailable"], "remaining": ["retry"], "decisions": []},
        }
        rendered = cli.render_checkpoint_candidate(candidate)
        self.assertIn("run-1", rendered)
        self.assertIn("provider unavailable", rendered)
        self.assertIn("retry", rendered)


class _FakeFailingRuntime:
    def execute(self, contracts):
        raise RuntimeError("provider unavailable")


class _FakeSucceedingRuntime:
    def __init__(self, output_root: Path):
        self.output_root = output_root

    def execute(self, contracts):
        path = self.output_root / f"{contracts['task']['task']['id']}-{contracts['task']['task']['id']}-run"
        path.mkdir(parents=True, exist_ok=True)
        return path


class ExecutionAndResumptionTests(unittest.TestCase):
    """End-to-end: real Store/Scheduler wiring against a real repository, fake Runtime
    (no real OpenCode server required)."""

    def test_execute_retry_promote_and_resume_view(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(db)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            # Onboard, then plan a single-repository task so a real task.yaml exists.
            run(["repository", "add", "repo", str(repository_dir)])
            run(["repository", "analyze", "repo", "--json"])
            answers_file = root / "answers.json"
            answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
            run(["repository", "answer", "repo", str(answers_file)])
            code, out = run(["repository", "apply", "repo"])
            self.assertEqual(0, code, out)

            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Add CSV export.", "repositories": ["repo"],
            }), encoding="utf-8")
            run(["plan", "draft", "feature-export", str(request_file)])
            plan_answers_file = root / "plan-answers.json"
            plan_answers_file.write_text(json.dumps({
                "components": {"repo": ["content"]}, "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
            }), encoding="utf-8")
            run(["plan", "answer", "feature-export", str(plan_answers_file)])
            code, out = run(["plan", "apply", "feature-export"])
            self.assertEqual(0, code, out)

            # Before any execution: active_work shows it, never run.
            items = cli.active_work(store, registry)
            self.assertEqual(1, len(items))
            self.assertEqual("repo", items[0]["repository_id"])
            self.assertEqual("feature-export", items[0]["task_id"])
            self.assertIsNone(items[0]["latest_run_status"])
            self.assertEqual(0, items[0]["attempts"])
            self.assertFalse(items[0]["checkpoint_present"])

            # First attempt fails -- a checkpoint candidate should appear.
            result = cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export", runtime=_FakeFailingRuntime(),
            )
            self.assertEqual(1, result["attempt"])
            self.assertEqual("failed", result["status"])

            items = cli.active_work(store, registry)
            self.assertEqual("failed", items[0]["latest_run_status"])
            self.assertEqual(1, items[0]["attempts"])
            self.assertTrue(items[0]["checkpoint_present"])

            # Promote the candidate into the durable, committable location.
            candidate = cli.checkpoint_candidate(store, "feature-export")
            self.assertEqual(["provider unavailable"], candidate["progress"]["blockers"])
            durable_path = cli.promote_checkpoint(repository_dir, "feature-export", candidate)
            self.assertTrue(durable_path.is_file())
            from esc_exec.contracts import validate_contract
            from esc_exec.model import ManifestState
            self.assertEqual(ManifestState.VALID, validate_contract("checkpoint", durable_path).state)

            # Retry succeeds -- attempt count increments, resume view no longer flags
            # a pending checkpoint (the latest run is no longer a failure).
            result = cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                runtime=_FakeSucceedingRuntime(root / "runs"),
            )
            self.assertEqual(2, result["attempt"])
            self.assertEqual("succeeded", result["status"])

            items = cli.active_work(store, registry)
            self.assertEqual("succeeded", items[0]["latest_run_status"])
            self.assertEqual(2, items[0]["attempts"])
            self.assertFalse(items[0]["checkpoint_present"])

    def test_task_run_without_yes_only_previews(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            run(["repository", "add", "repo", str(repository_dir)])
            run(["repository", "analyze", "repo", "--json"])
            answers_file = root / "answers.json"
            answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
            run(["repository", "answer", "repo", str(answers_file)])
            run(["repository", "apply", "repo"])
            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Add CSV export.", "repositories": ["repo"],
            }), encoding="utf-8")
            run(["plan", "draft", "feature-export", str(request_file)])
            plan_answers_file = root / "plan-answers.json"
            plan_answers_file.write_text(json.dumps({
                "components": {"repo": ["content"]}, "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
            }), encoding="utf-8")
            run(["plan", "answer", "feature-export", str(plan_answers_file)])
            run(["plan", "apply", "feature-export"])

            code, out = run(["task", "run", "repo", "feature-export"])
            self.assertEqual(0, code)
            self.assertIn("Preview only", out)
            self.assertIn("placeholder", out)

    def test_resume_json_output(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            run(["repository", "add", "repo", str(repository_dir)])
            run(["repository", "analyze", "repo", "--json"])
            answers_file = root / "answers.json"
            answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
            run(["repository", "answer", "repo", str(answers_file)])
            run(["repository", "apply", "repo"])
            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Add CSV export.", "repositories": ["repo"],
            }), encoding="utf-8")
            run(["plan", "draft", "feature-export", str(request_file)])
            plan_answers_file = root / "plan-answers.json"
            plan_answers_file.write_text(json.dumps({
                "components": {"repo": ["content"]}, "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
            }), encoding="utf-8")
            run(["plan", "answer", "feature-export", str(plan_answers_file)])
            run(["plan", "apply", "feature-export"])

            code, out = run(["resume", "--json"])
            self.assertEqual(0, code)
            items = json.loads(out)
            self.assertEqual(1, len(items))
            self.assertEqual("feature-export", items[0]["task_id"])

    def test_process_metrics_appear_once_onboarding_and_planning_complete(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            run(["repository", "add", "repo", str(repository_dir)])

            code, out = run(["repository", "status", "repo"])
            self.assertIn("process_metrics: None", out)

            run(["repository", "analyze", "repo", "--json"])
            answers_file = root / "answers.json"
            answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
            run(["repository", "answer", "repo", str(answers_file)])
            run(["repository", "apply", "repo"])

            code, out = run(["repository", "status", "repo"])
            self.assertNotIn("process_metrics: None", out)
            self.assertIn("'kind': 'onboarding'", out)


if __name__ == "__main__":
    unittest.main()
