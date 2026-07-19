import builtins
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from esc_exec.manifests import component_manifest_path
from esc_exec.registry import add_route, set_provider
from esc_exec.roadmap import load_project_roadmap

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

    def test_render_menu_shows_banner_and_prompt(self):
        rendered = cli.render_menu()
        self.assertIn("Escape drift. Engineer consistency.", rendered)
        self.assertIn("What would you like to do?", rendered)

    def test_render_menu_options_lists_all_six_items_in_order(self):
        rendered = cli.render_menu_options(cli.MENU)
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

    def test_render_work_types_names_the_menu(self):
        self.assertIn("Work type", cli.render_work_types())

    def test_render_menu_options_lists_all_five_work_types(self):
        rendered = cli.render_menu_options(cli.WORK_TYPES)
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
                "2",                  # decline the "connect an AI provider?" offer (Yes/No menu)
                "Owns content.",      # answer to the purpose question
                "1",                  # confirm apply (Yes/No menu)
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

    def test_ai_suggested_purpose_is_accepted_with_blank_input(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            original_suggest = cli.suggest_answers_via_provider
            cli.suggest_answers_via_provider = lambda registry, repository_path, purpose_ids, frameworks_ids: {
                "content": {"purpose": "Owns lesson publishing."}
            }
            responses = iter([str(repository_dir), "2", "", "1"])  # decline connect offer, then blank -- accept the (mocked) suggestion
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.suggest_answers_via_provider = original_suggest

            self.assertEqual(0, code)
            self.assertIn("Suggested: Owns lesson publishing.", buffer.getvalue())
            self.assertIn(
                "Owns lesson publishing.", component_manifest_path(repository_dir, "content").read_text(encoding="utf-8"),
            )

    def test_ai_suggested_purpose_can_be_overridden(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            original_suggest = cli.suggest_answers_via_provider
            cli.suggest_answers_via_provider = lambda registry, repository_path, purpose_ids, frameworks_ids: {
                "content": {"purpose": "Owns lesson publishing."}
            }
            responses = iter([str(repository_dir), "2", "Actually owns something else.", "1"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.suggest_answers_via_provider = original_suggest

            self.assertEqual(0, code)
            manifest_text = component_manifest_path(repository_dir, "content").read_text(encoding="utf-8")
            self.assertIn("Actually owns something else.", manifest_text)
            self.assertNotIn("Owns lesson publishing.", manifest_text)

    def test_onboarding_offers_to_connect_a_provider_and_then_suggests(self):
        # Full chain, no shortcuts mocked except the actual claude subprocess calls:
        # no provider connected yet -> onboarding offers to connect -> accepting
        # walks through the real prompt_provider_setup_interactive/connect_provider
        # path -> the newly-connected provider is then used for a real suggestion call.
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            original_available, original_status = cli.claude_cli_available, cli.claude_auth_status
            cli.claude_cli_available = lambda binary="claude": True
            cli.claude_auth_status = lambda binary="claude": {"loggedIn": True, "subscriptionType": "pro"}
            original_suggest = cli.suggest_onboarding_answers
            cli.suggest_onboarding_answers = lambda client, repository_path, purpose_ids, frameworks_ids: {
                "content": {"purpose": "Owns lesson publishing."}
            }

            responses = iter([
                str(repository_dir),  # repository path
                "1", "1", "1",        # connect offer: yes -> claude -> subscription
                "",                   # accept the suggestion
                "1",                  # confirm apply (Yes)
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.claude_cli_available, cli.claude_auth_status = original_available, original_status
                cli.suggest_onboarding_answers = original_suggest

            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("an AI provider can suggest answers", output)
            self.assertIn("Connected `claude` (subscription).", output)
            self.assertIn("Suggested: Owns lesson publishing.", output)
            self.assertIn(
                "Owns lesson publishing.", component_manifest_path(repository_dir, "content").read_text(encoding="utf-8"),
            )
            from esc_exec.registry import active_provider
            self.assertEqual({"id": "claude", "route": "subscription"}, active_provider(registry))

    def test_declining_the_connect_offer_falls_back_to_a_plain_question(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            responses = iter([str(repository_dir), "2", "Owns content.", "1"])
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
            self.assertIn("an AI provider can suggest answers", output)
            self.assertNotIn("Suggested:", output)
            from esc_exec.registry import active_provider
            self.assertIsNone(active_provider(registry))

    def test_cancelling_before_confirmation_writes_nothing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            responses = iter([str(repository_dir), "2", "Owns content.", "2"])
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

    def test_unfinished_onboarding_is_offered_and_can_be_resumed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            # First run: analyze, then decline to apply -- leaves a saved proposal
            # with no saved answers (unfinished).
            first_responses = iter([str(repository_dir), "2", "Owns content.", "2"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(first_responses)
            try:
                with redirect_stdout(io.StringIO()):
                    cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input
            self.assertEqual(["repo"], store.list_unfinished_onboardings())

            # Second run: the unfinished-onboarding menu should appear first, and
            # picking the only entry should skip straight past the repository-path
            # prompt into the same proposal.
            second_responses = iter(["1", "2", "Owns content.", "1"])  # pick unfinished repo -> decline connect offer -> answer -> apply
            builtins.input = lambda prompt="": next(second_responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("unfinished onboarding", output)
            self.assertIn("repo", output)
            self.assertIn("Applied.", output)
            self.assertEqual([], store.list_unfinished_onboardings())

    def test_resuming_unchanged_repository_detects_existing_proposal(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            first_responses = iter([str(repository_dir), "2", "Owns content.", "1"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(first_responses)
            try:
                with redirect_stdout(io.StringIO()):
                    cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            second_responses = iter([str(repository_dir), "2"])
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
                "1",                     # confirm (Yes)
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


def _conversation_stream(text: str, session_id: str = "ses-1") -> list[dict]:
    return [
        {"type": "system", "subtype": "init", "session_id": session_id, "tools": []},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}, "session_id": session_id},
        {
            "type": "result", "subtype": "success", "is_error": False, "result": text,
            "session_id": session_id, "num_turns": 1, "total_cost_usd": 0.01,
            "usage": {"input_tokens": 10, "output_tokens": 10, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 10},
        },
    ]


class _FakeConversationClient:
    """Mirrors ClaudeCodeClient.run's signature -- returns queued NDJSON message
    lists in call order, one per turn (including compact_conversation's own
    trailing --resume turn)."""

    def __init__(self, message_sequences: list[list[dict]]):
        self._sequences = list(message_sequences)
        self.calls: list[dict] = []

    def run(self, directory, prompt, tools, model=None, resume_session_id=None):
        self.calls.append({"prompt": prompt, "tools": tools, "resume_session_id": resume_session_id})
        return self._sequences.pop(0)


class PlanningConversationInteractiveTests(unittest.TestCase):
    def test_no_provider_connected_skips_without_prompting(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            result = cli.run_planning_conversation_interactive(registry, repository_dir, "repo", "init-1", "Add CSV export.")
        self.assertIsNone(result)

    def test_declining_returns_none_without_starting_a_session(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            set_provider(registry, "claude", "subscription")

            responses = iter(["2"])  # decline "Talk through this plan with AI first?"
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    result = cli.run_planning_conversation_interactive(
                        registry, repository_dir, "repo", "init-1", "Add CSV export.",
                    )
            finally:
                builtins.input = original_input
        self.assertIsNone(result)

    def test_full_conversation_saves_summary_and_updates_roadmap_on_confirm(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            set_provider(registry, "claude", "subscription")

            compaction_payload = json.dumps({
                "progress": {"completed": ["discussed scope"], "decisions": [], "remaining": [], "open_questions": []},
                "roadmap": {
                    "purpose": "CSV export tooling", "current_stage": "scope agreed",
                    "direction": "implement exporter", "durable_decisions": ["stream export, don't buffer in memory"],
                },
            })
            fake_client = _FakeConversationClient([
                _conversation_stream("What format should the export use?"),
                _conversation_stream(compaction_payload),
            ])

            responses = iter([
                "1",  # confirm "Talk through this plan with AI first?" -> Yes
                "",   # blank reply ends the conversation loop
                "1",  # confirm "Update this repository's saved roadmap...?" -> Yes
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer), patch("esc_orchestrator.escape_ai_cli.ClaudeCodeClient", return_value=fake_client):
                    result = cli.run_planning_conversation_interactive(
                        registry, repository_dir, "repo", "init-1", "Add CSV export.",
                    )
            finally:
                builtins.input = original_input

            self.assertEqual(result, "What format should the export use?")
            self.assertEqual(len(fake_client.calls), 2)
            self.assertEqual(fake_client.calls[1]["resume_session_id"], "ses-1")

            roadmap = load_project_roadmap(repository_dir)
            self.assertEqual(roadmap["project_roadmap"]["purpose"], "CSV export tooling")
            self.assertEqual(roadmap["project_roadmap"]["direction"], "implement exporter")
            self.assertIn("stream export, don't buffer in memory", roadmap["project_roadmap"]["durable_decisions"])

            summary = (repository_dir / ".esc-ai" / "conversations" / "plan-init-1" / "summary.yaml")
            self.assertTrue(summary.is_file())

    def test_declining_roadmap_update_leaves_no_roadmap_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            set_provider(registry, "claude", "subscription")

            compaction_payload = json.dumps({
                "progress": {"completed": [], "decisions": [], "remaining": [], "open_questions": []},
                "roadmap": {"purpose": "x", "current_stage": "y", "direction": "z", "durable_decisions": []},
            })
            fake_client = _FakeConversationClient([
                _conversation_stream("Sure, what's the target format?"),
                _conversation_stream(compaction_payload),
            ])

            responses = iter(["1", "", "2"])  # talk -> yes, blank to end, roadmap update -> No
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer), patch("esc_orchestrator.escape_ai_cli.ClaudeCodeClient", return_value=fake_client):
                    cli.run_planning_conversation_interactive(registry, repository_dir, "repo", "init-1", "Add CSV export.")
            finally:
                builtins.input = original_input

            self.assertIsNone(load_project_roadmap(repository_dir))


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

    def test_render_execution_preview_with_no_provider_says_so(self):
        task_document = {"task": {"objective": "Add CSV export."}, "scope": {"components": ["content"]}}
        rendered = cli.render_execution_preview("repo", "feature-export", task_document)
        self.assertIn("Provider: none connected yet", rendered)

    def test_render_execution_preview_shows_connected_provider(self):
        task_document = {"task": {"objective": "Add CSV export."}, "scope": {"components": ["content"]}}
        rendered = cli.render_execution_preview(
            "repo", "feature-export", task_document, {"id": "claude", "route": "subscription"},
        )
        self.assertIn("Provider: claude (subscription)", rendered)

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
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"}, runtime=_FakeFailingRuntime(),
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
                {"id": "claude", "route": "api-key"}, runtime=_FakeSucceedingRuntime(root / "runs"),
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
            self.assertIn("Provider: none connected yet", out)

    def test_task_run_with_yes_and_no_provider_is_incomplete(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
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

            code, out = run(["task", "run", "repo", "feature-export", "--yes"])
            self.assertEqual(2, code)
            self.assertIn("INCOMPLETE", out)
            self.assertIn("provider auth", out)

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


class ProviderTests(unittest.TestCase):
    def test_default_adapter_routes_subscription_to_claude_code(self):
        adapter = cli.default_adapter({"id": "claude", "route": "subscription"})
        self.assertEqual("claude-code", adapter["adapter"]["provider"])

    def test_default_adapter_routes_openai_subscription_to_codex(self):
        adapter = cli.default_adapter({"id": "openai", "route": "subscription"})
        self.assertEqual("codex", adapter["adapter"]["provider"])

    def test_default_adapter_routes_api_key_to_opencode(self):
        adapter = cli.default_adapter({"id": "openai", "route": "api-key"})
        self.assertEqual("opencode", adapter["adapter"]["provider"])

    def test_default_adapter_gemini_subscription_falls_back_to_opencode(self):
        # gemini has no subscription-route adapter (Antigravity is parked) -- even if
        # a "subscription" route somehow ended up recorded, default_adapter must not
        # invent an adapter for it.
        adapter = cli.default_adapter({"id": "gemini", "route": "subscription"})
        self.assertEqual("opencode", adapter["adapter"]["provider"])

    def test_resolve_runtime_picks_claude_code_runtime_for_subscription(self):
        from esc_orchestrator.runtime import ClaudeCodeRuntime
        with TemporaryDirectory() as temp:
            runtime = cli.resolve_runtime({"id": "claude", "route": "subscription"}, Path(temp) / "registry.yaml", "http://fake")
            self.assertIsInstance(runtime, ClaudeCodeRuntime)

    def test_resolve_runtime_picks_codex_runtime_for_openai_subscription(self):
        from esc_orchestrator.runtime import CodexRuntime
        with TemporaryDirectory() as temp:
            runtime = cli.resolve_runtime({"id": "openai", "route": "subscription"}, Path(temp) / "registry.yaml", "http://fake")
            self.assertIsInstance(runtime, CodexRuntime)

    def test_resolve_runtime_picks_opencode_runtime_for_api_key(self):
        from esc_orchestrator.runtime import OpenCodeRuntime
        with TemporaryDirectory() as temp:
            runtime = cli.resolve_runtime({"id": "openai", "route": "api-key"}, Path(temp) / "registry.yaml", "http://fake")
            self.assertIsInstance(runtime, OpenCodeRuntime)

    def test_connect_provider_api_key_never_checks_for_claude_cli(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            provider = cli.connect_provider(registry, "openai", "api-key")
            self.assertEqual({"id": "openai", "route": "api-key"}, provider)

    def test_connect_provider_subscription_requires_claude_cli_on_path(self):
        original = cli.claude_cli_available
        cli.claude_cli_available = lambda binary="claude": False
        try:
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "not found on PATH"):
                    cli.connect_provider(Path(temp) / "registry.yaml", "claude", "subscription")
        finally:
            cli.claude_cli_available = original

    def test_connect_provider_subscription_succeeds_when_claude_cli_present_and_logged_in(self):
        original_available, original_status = cli.claude_cli_available, cli.claude_auth_status
        cli.claude_cli_available = lambda binary="claude": True
        cli.claude_auth_status = lambda binary="claude": {"loggedIn": True, "subscriptionType": "pro"}
        try:
            with TemporaryDirectory() as temp:
                registry = Path(temp) / "registry.yaml"
                provider = cli.connect_provider(registry, "claude", "subscription")
                self.assertEqual({"id": "claude", "route": "subscription"}, provider)
        finally:
            cli.claude_cli_available, cli.claude_auth_status = original_available, original_status

    def test_connect_provider_subscription_rejects_when_installed_but_not_logged_in(self):
        original_available, original_status = cli.claude_cli_available, cli.claude_auth_status
        cli.claude_cli_available = lambda binary="claude": True
        cli.claude_auth_status = lambda binary="claude": {"loggedIn": False}
        try:
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "not logged in"):
                    cli.connect_provider(Path(temp) / "registry.yaml", "claude", "subscription")
        finally:
            cli.claude_cli_available, cli.claude_auth_status = original_available, original_status

    def test_connect_provider_subscription_rejects_when_auth_status_check_fails(self):
        # claude_auth_status returns None on any failure (CLI missing mid-check,
        # non-zero exit, unparseable output) -- must be treated as not-logged-in,
        # never as "unknown, assume fine."
        original_available, original_status = cli.claude_cli_available, cli.claude_auth_status
        cli.claude_cli_available = lambda binary="claude": True
        cli.claude_auth_status = lambda binary="claude": None
        try:
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "not logged in"):
                    cli.connect_provider(Path(temp) / "registry.yaml", "claude", "subscription")
        finally:
            cli.claude_cli_available, cli.claude_auth_status = original_available, original_status

    def test_connect_provider_subscription_error_names_install_command(self):
        original = cli.claude_cli_available
        cli.claude_cli_available = lambda binary="claude": False
        try:
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "npm install -g @anthropic-ai/claude-code"):
                    cli.connect_provider(Path(temp) / "registry.yaml", "claude", "subscription")
        finally:
            cli.claude_cli_available = original

    def test_connect_provider_codex_subscription_succeeds_when_logged_in(self):
        original_available, original_status = cli.codex_cli_available, cli.codex_auth_status
        cli.codex_cli_available = lambda binary="codex": True
        cli.codex_auth_status = lambda binary="codex": "Logged in using ChatGPT"
        try:
            with TemporaryDirectory() as temp:
                registry = Path(temp) / "registry.yaml"
                provider = cli.connect_provider(registry, "openai", "subscription")
                self.assertEqual({"id": "openai", "route": "subscription"}, provider)
        finally:
            cli.codex_cli_available, cli.codex_auth_status = original_available, original_status

    def test_connect_provider_codex_subscription_rejects_when_not_installed(self):
        original = cli.codex_cli_available
        cli.codex_cli_available = lambda binary="codex": False
        try:
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "npm install -g @openai/codex"):
                    cli.connect_provider(Path(temp) / "registry.yaml", "openai", "subscription")
        finally:
            cli.codex_cli_available = original

    def test_connect_provider_codex_subscription_rejects_when_not_logged_in(self):
        original_available, original_status = cli.codex_cli_available, cli.codex_auth_status
        cli.codex_cli_available = lambda binary="codex": True
        cli.codex_auth_status = lambda binary="codex": None
        try:
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "not logged in"):
                    cli.connect_provider(Path(temp) / "registry.yaml", "openai", "subscription")
        finally:
            cli.codex_cli_available, cli.codex_auth_status = original_available, original_status

    def test_provider_auth_dispatch_connects_api_key_route(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            code, out = run(["provider", "auth", "openai", "--route", "api-key"])
            self.assertEqual(0, code)
            self.assertIn("CONNECTED  openai (api-key)", out)
            self.assertIn("routes through OpenCode", out)
            from esc_exec.registry import active_provider
            self.assertEqual({"id": "openai", "route": "api-key"}, active_provider(registry))

    def test_provider_auth_dispatch_rejects_gemini_entirely(self):
        # gemini isn't in KNOWN_PROVIDERS anymore (removed 2026-07-19, see
        # plan/future/reintroduce-gemini-provider.md) -- argparse itself rejects it as an
        # invalid choice before ever reaching _dispatch_provider, for any route.
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            with self.assertRaises(SystemExit) as ctx:
                with redirect_stderr(io.StringIO()):
                    cli.main(["--db", str(db), "--registry", str(registry), "provider", "auth", "gemini"])
            self.assertEqual(2, ctx.exception.code)

    def test_provider_auth_dispatch_connects_codex_subscription_without_opencode_note(self):
        original_available, original_status = cli.codex_cli_available, cli.codex_auth_status
        cli.codex_cli_available = lambda binary="codex": True
        cli.codex_auth_status = lambda binary="codex": "Logged in using ChatGPT"
        try:
            with TemporaryDirectory() as temp:
                root = Path(temp)
                db, registry = root / "db.sqlite", root / "registry.yaml"

                def run(argv):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                    return code, buffer.getvalue()

                code, out = run(["provider", "auth", "openai", "--route", "subscription"])
                self.assertEqual(0, code)
                self.assertIn("CONNECTED  openai (subscription)", out)
                # regression test: the OpenCode note must follow the chosen route, not
                # whether the provider merely has subscription capability at all
                self.assertNotIn("routes through OpenCode", out)
        finally:
            cli.codex_cli_available, cli.codex_auth_status = original_available, original_status

    def test_provider_auth_dispatch_defaults_openai_to_subscription_route(self):
        # args.route defaults to subscription for any subscription-capable provider
        # when --route isn't passed -- openai must get this now too, not just claude.
        original_available, original_status = cli.codex_cli_available, cli.codex_auth_status
        cli.codex_cli_available = lambda binary="codex": True
        cli.codex_auth_status = lambda binary="codex": "Logged in using ChatGPT"
        try:
            with TemporaryDirectory() as temp:
                root = Path(temp)
                db, registry = root / "db.sqlite", root / "registry.yaml"

                def run(argv):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                    return code, buffer.getvalue()

                code, out = run(["provider", "auth", "openai"])
                self.assertEqual(0, code)
                self.assertIn("CONNECTED  openai (subscription)", out)
        finally:
            cli.codex_cli_available, cli.codex_auth_status = original_available, original_status

    def test_prompt_provider_setup_interactive_connects_api_key_route(self):
        # No real provider is currently known-but-not-subscription-capable (gemini
        # was removed -- see plan/future/reintroduce-gemini-provider.md), so this exercises
        # that branch (skip the route sub-prompt, go straight to api-key) via a
        # synthetic provider patched into both this module and esc_exec.registry
        # (set_provider validates against its own copy of KNOWN_PROVIDERS).
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            responses = iter(["1", "1"])  # connect -> acme (the only option)
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                with patch("esc_orchestrator.escape_ai_cli.KNOWN_PROVIDERS", ("acme",)), \
                     patch("esc_orchestrator.escape_ai_cli.SUBSCRIPTION_CAPABLE_PROVIDERS", ()), \
                     patch("esc_exec.registry.KNOWN_PROVIDERS", ("acme",)):
                    provider = cli.prompt_provider_setup_interactive(registry)
            finally:
                builtins.input = original_input
            self.assertEqual({"id": "acme", "route": "api-key"}, provider)

    def test_prompt_provider_setup_interactive_claude_subscription(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            responses = iter(["1", "1", "1"])  # connect -> claude -> subscription
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            original_available, original_status = cli.claude_cli_available, cli.claude_auth_status
            cli.claude_cli_available = lambda binary="claude": True
            cli.claude_auth_status = lambda binary="claude": {"loggedIn": True, "subscriptionType": "pro"}
            try:
                provider = cli.prompt_provider_setup_interactive(registry)
            finally:
                builtins.input = original_input
                cli.claude_cli_available, cli.claude_auth_status = original_available, original_status
            self.assertEqual({"id": "claude", "route": "subscription"}, provider)

    def test_prompt_provider_setup_interactive_decline_returns_none(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            original_input = builtins.input
            builtins.input = lambda prompt="": "2"
            try:
                self.assertIsNone(cli.prompt_provider_setup_interactive(registry))
            finally:
                builtins.input = original_input

    def test_suggest_answers_via_provider_skips_with_no_provider_connected(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            self.assertEqual({}, cli.suggest_answers_via_provider(registry, Path(temp), ["content"], []))

    def test_suggest_answers_via_provider_skips_for_api_key_route(self):
        from esc_exec.registry import set_provider
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            set_provider(registry, "claude", "api-key")
            self.assertEqual({}, cli.suggest_answers_via_provider(registry, Path(temp), ["content"], []))

    def test_suggest_answers_via_provider_skips_for_non_claude_provider(self):
        from esc_exec.registry import set_provider
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            set_provider(registry, "openai", "api-key")
            self.assertEqual({}, cli.suggest_answers_via_provider(registry, Path(temp), ["content"], []))

    def test_suggest_answers_via_provider_calls_through_for_claude_subscription(self):
        from esc_exec.registry import set_provider
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            original_available = cli.claude_cli_available
            cli.claude_cli_available = lambda binary="claude": True
            try:
                set_provider(registry, "claude", "subscription")
            finally:
                cli.claude_cli_available = original_available

            original_suggest = cli.suggest_onboarding_answers
            calls = []
            def fake_suggest_onboarding_answers(client, repository_path, purpose_ids, frameworks_ids):
                calls.append((repository_path, purpose_ids, frameworks_ids))
                return {"content": {"purpose": "Owns lesson publishing."}}
            cli.suggest_onboarding_answers = fake_suggest_onboarding_answers
            try:
                result = cli.suggest_answers_via_provider(registry, Path(temp), ["content"], ["content"])
            finally:
                cli.suggest_onboarding_answers = original_suggest
            self.assertEqual({"content": {"purpose": "Owns lesson publishing."}}, result)
            self.assertEqual([(Path(temp), ["content"], ["content"])], calls)

    def test_suggest_answers_via_provider_fails_open_on_claude_code_error(self):
        from esc_exec.registry import set_provider
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            original_available = cli.claude_cli_available
            cli.claude_cli_available = lambda binary="claude": True
            try:
                set_provider(registry, "claude", "subscription")
            finally:
                cli.claude_cli_available = original_available

            original_suggest = cli.suggest_onboarding_answers
            def raising_suggest_onboarding_answers(client, repository_path, purpose_ids, frameworks_ids):
                raise cli.ClaudeCodeError("boom")
            cli.suggest_onboarding_answers = raising_suggest_onboarding_answers
            try:
                result = cli.suggest_answers_via_provider(registry, Path(temp), ["content"], [])
            finally:
                cli.suggest_onboarding_answers = original_suggest
            self.assertEqual({}, result)


class CollectAnswerTests(unittest.TestCase):
    def test_blank_input_accepts_the_purpose_suggestion(self):
        question = {"component_id": "content", "field": "purpose", "prompt": "What is the purpose?"}
        answers: dict[str, dict] = {}
        original_input = builtins.input
        builtins.input = lambda prompt="": ""
        try:
            cli._collect_answer(question, answers, {"content": {"purpose": "Owns lesson publishing."}})
        finally:
            builtins.input = original_input
        self.assertEqual("Owns lesson publishing.", answers["content"]["purpose"])

    def test_typed_input_overrides_the_purpose_suggestion(self):
        question = {"component_id": "content", "field": "purpose", "prompt": "What is the purpose?"}
        answers: dict[str, dict] = {}
        original_input = builtins.input
        builtins.input = lambda prompt="": "Something else entirely."
        try:
            cli._collect_answer(question, answers, {"content": {"purpose": "Owns lesson publishing."}})
        finally:
            builtins.input = original_input
        self.assertEqual("Something else entirely.", answers["content"]["purpose"])

    def test_no_suggestion_behaves_as_before(self):
        question = {"component_id": "content", "field": "purpose", "prompt": "What is the purpose?"}
        answers: dict[str, dict] = {}
        original_input = builtins.input
        builtins.input = lambda prompt="": "Owns content."
        try:
            cli._collect_answer(question, answers, None)
        finally:
            builtins.input = original_input
        self.assertEqual("Owns content.", answers["content"]["purpose"])

    def test_purpose_only_suggestion_does_not_leak_into_frameworks_question(self):
        question = {"component_id": "content", "field": "frameworks", "prompt": "Which frameworks?"}
        answers: dict[str, dict] = {}
        responses = iter(["network:ktor", ""])
        original_input = builtins.input
        builtins.input = lambda prompt="": next(responses)
        try:
            cli._collect_answer(question, answers, {"content": {"purpose": "Owns lesson publishing."}})
        finally:
            builtins.input = original_input
        self.assertEqual({"network": "ktor"}, answers["content"]["frameworks"])

    def test_blank_input_accepts_the_frameworks_and_targets_suggestion(self):
        question = {"component_id": "content", "field": "frameworks", "prompt": "Which frameworks?"}
        answers: dict[str, dict] = {}
        responses = iter(["", ""])  # accept frameworks suggestion, then accept targets suggestion
        original_input = builtins.input
        builtins.input = lambda prompt="": next(responses)
        try:
            cli._collect_answer(question, answers, {
                "content": {"frameworks": {"network": "ktor"}, "targets": ["ios"]},
            })
        finally:
            builtins.input = original_input
        self.assertEqual({"network": "ktor"}, answers["content"]["frameworks"])
        self.assertEqual(["ios"], answers["content"]["targets"])

    def test_typed_input_overrides_the_frameworks_suggestion(self):
        question = {"component_id": "content", "field": "frameworks", "prompt": "Which frameworks?"}
        answers: dict[str, dict] = {}
        responses = iter(["database:room", ""])
        original_input = builtins.input
        builtins.input = lambda prompt="": next(responses)
        try:
            cli._collect_answer(question, answers, {"content": {"frameworks": {"network": "ktor"}}})
        finally:
            builtins.input = original_input
        self.assertEqual({"database": "room"}, answers["content"]["frameworks"])

    def test_confidently_empty_frameworks_suggestion_is_shown_and_acceptable(self):
        question = {"component_id": "content", "field": "frameworks", "prompt": "Which frameworks?"}
        answers: dict[str, dict] = {}
        responses = iter(["", ""])
        original_input = builtins.input
        builtins.input = lambda prompt="": next(responses)
        try:
            cli._collect_answer(question, answers, {"content": {"frameworks": {}, "targets": []}})
        finally:
            builtins.input = original_input
        self.assertEqual({}, answers["content"]["frameworks"])
        self.assertEqual([], answers["content"]["targets"])


class SelectMenuTests(unittest.TestCase):
    """select_menu's fallback path -- unittest's stdin/stdout are never a real TTY,
    so these exercise exactly the code path a piped/redirected/scripted invocation
    would hit too, not just tests."""

    def test_isatty_is_false_under_the_test_runner(self):
        # Documents the assumption every other interactive test relies on implicitly.
        self.assertFalse(cli._isatty())

    def test_picks_the_chosen_index(self):
        original_input = builtins.input
        builtins.input = lambda prompt="": "2"
        try:
            self.assertEqual(1, cli.select_menu("Pick one:", ["a", "b", "c"]))
        finally:
            builtins.input = original_input

    def test_blank_input_backs_out(self):
        original_input = builtins.input
        builtins.input = lambda prompt="": ""
        try:
            self.assertIsNone(cli.select_menu("Pick one:", ["a", "b"]))
        finally:
            builtins.input = original_input

    def test_out_of_range_choice_returns_none(self):
        original_input = builtins.input
        builtins.input = lambda prompt="": "9"
        try:
            self.assertIsNone(cli.select_menu("Pick one:", ["a", "b"]))
        finally:
            builtins.input = original_input

    def test_non_numeric_choice_returns_none(self):
        original_input = builtins.input
        builtins.input = lambda prompt="": "banana"
        try:
            self.assertIsNone(cli.select_menu("Pick one:", ["a", "b"]))
        finally:
            builtins.input = original_input

    def test_eof_and_keyboard_interrupt_back_out(self):
        original_input = builtins.input
        for exc in (EOFError, KeyboardInterrupt):
            def raiser(prompt="", exc=exc):
                raise exc
            builtins.input = raiser
            try:
                self.assertIsNone(cli.select_menu("Pick one:", ["a", "b"]))
            finally:
                builtins.input = original_input


class ListUnfinishedOnboardingsTests(unittest.TestCase):
    def test_no_proposals_is_empty(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp) / "db.sqlite")
            self.assertEqual([], store.list_unfinished_onboardings())

    def test_proposal_without_answers_is_unfinished(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp) / "db.sqlite")
            store.save_onboarding_proposal("repo", {"input_digest": "abc", "semantic_questions": []})
            self.assertEqual(["repo"], store.list_unfinished_onboardings())

    def test_proposal_with_applied_answers_is_not_unfinished(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp) / "db.sqlite")
            store.save_onboarding_proposal("repo", {"input_digest": "abc", "semantic_questions": []})
            store.save_onboarding_answers("repo", {}, {"written": []})
            self.assertEqual([], store.list_unfinished_onboardings())

    def test_most_recently_touched_comes_first(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp) / "db.sqlite")
            store.save_onboarding_proposal("first", {"input_digest": "a", "semantic_questions": []})
            store.save_onboarding_proposal("second", {"input_digest": "b", "semantic_questions": []})
            self.assertEqual(["second", "first"], store.list_unfinished_onboardings())


if __name__ == "__main__":
    unittest.main()
