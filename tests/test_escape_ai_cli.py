import builtins
import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from esc_exec.indexing import generate_indexes
from esc_exec.manifests import component_manifest_path, generate_gradle_manifests, repository_manifest_path
from esc_exec.registry import add_route, set_provider
from esc_exec.roadmap import load_project_roadmap, save_project_roadmap
from esc_exec.yaml_io import load_yaml, write_yaml

from esc_orchestrator import escape_ai_cli as cli
from esc_orchestrator.store import Store


def _make_gradle_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle.kts").write_text(
        'rootProject.name = "repo"\ninclude(":content")\n', encoding="utf-8",
    )
    (root / "content/src/main/kotlin").mkdir(parents=True)
    (root / "content/build.gradle.kts").write_text("", encoding="utf-8")


def _make_multi_component_gradle_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle.kts").write_text(
        'rootProject.name = "repo"\ninclude(":core")\ninclude(":sample")\n', encoding="utf-8",
    )
    for component in (root / "core", root / "sample"):
        (component / "src/main/kotlin").mkdir(parents=True)
        (component / "build.gradle.kts").write_text("", encoding="utf-8")


def _make_gradle_repository_with_unresolved_module(root: Path) -> None:
    """A module Tier 1 can't resolve at all -- no projectDir remap, no directory
    matching the declared path -- so adapter.unresolved() reports it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle.kts").write_text(
        'rootProject.name = "repo"\ninclude(":content")\ninclude(":ghost")\n', encoding="utf-8",
    )
    (root / "content/src/main/kotlin").mkdir(parents=True)
    (root / "content/build.gradle.kts").write_text("", encoding="utf-8")
    (root / "actual-ghost-dir").mkdir()


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
        self.assertNotIn("Dependency chain", rendered)

    def test_render_plan_result_prints_dependency_graph_when_given(self):
        """plan/done/run-outcome-surfacing.md finding #7 (generalized from a single
        chain to a real graph by plan/active/multi-repository-dependency-graph-
        planning.md): a multi-repo plan's resolved dependencies should be visible
        in the apply output itself, including a real branching case, not just a
        chain."""
        result = {
            "ampm-backend": [".esc-ai/workflows/active/task/task.yaml"],
            "ampm-contracts": [".esc-ai/workflows/active/task/task.yaml"],
            "ampm-kmp": [".esc-ai/workflows/active/task/task.yaml"],
        }
        dependency_graph = {
            "ampm-contracts": [], "ampm-backend": ["ampm-contracts"], "ampm-kmp": ["ampm-contracts", "ampm-backend"],
        }
        rendered = cli.render_plan_result(result, dependency_graph)
        self.assertIn("Dependency graph:", rendered)
        self.assertIn("ampm-contracts: no dependencies", rendered)
        self.assertIn("ampm-backend: depends on ampm-contracts", rendered)
        self.assertIn("ampm-kmp: depends on ampm-contracts, ampm-backend", rendered)

    def test_render_plan_result_omits_graph_section_when_none(self):
        rendered = cli.render_plan_result({"repo-a": []}, None)
        self.assertNotIn("Dependency graph", rendered)


class TaskIdSuggestionsTests(unittest.TestCase):
    """plan/done/cli-discoverability.md finding #1: pure unit tests for the
    shared suggestion helper, independent of any CLI dispatch."""

    def test_suggests_prefix_matches(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / ".esc-ai" / "workflows" / "active"
            (active / "feature-cross-repo-a").mkdir(parents=True)
            (active / "feature-cross-repo-b").mkdir(parents=True)
            (active / "unrelated-task").mkdir(parents=True)
            self.assertEqual(
                ["feature-cross-repo-a", "feature-cross-repo-b"],
                cli._task_id_suggestions(root, "feature-cross"),
            )

    def test_no_suggestions_when_nothing_matches(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".esc-ai" / "workflows" / "active" / "other-task").mkdir(parents=True)
            self.assertEqual([], cli._task_id_suggestions(root, "feature-cross"))

    def test_no_suggestions_when_active_directory_is_missing(self):
        with TemporaryDirectory() as temp:
            self.assertEqual([], cli._task_id_suggestions(Path(temp), "feature-cross"))

    def test_exact_match_is_excluded_from_its_own_suggestions(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".esc-ai" / "workflows" / "active" / "feature-cross").mkdir(parents=True)
            self.assertEqual([], cli._task_id_suggestions(root, "feature-cross"))


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

    def test_analyze_of_empty_directory_suggests_scaffold_wizard(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            empty_dir = root / "empty-project"
            empty_dir.mkdir()

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.main(["--db", str(db), "--registry", str(registry), "repository", "analyze", str(empty_dir)])
            self.assertEqual(1, code)
            output = buffer.getvalue()
            self.assertIn("No supported build system detected", output)
            self.assertIn("npx create-next-app@latest", output)

    def test_analyze_of_unregistered_id_suggests_scaffold_wizard(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.main(["--db", str(db), "--registry", str(registry), "repository", "analyze", "not-registered"])
            self.assertEqual(1, code)
            output = buffer.getvalue()
            self.assertIn("isn't a directory and isn't a registered repository", output)
            self.assertIn("npx create-next-app@latest", output)

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

    def test_plan_draft_json_prints_the_real_questions(self):
        """
        plan/done/cli-discoverability.md finding #2: `plan draft` only ever
        printed a bare question count -- `--json` (mirroring `repository analyze
        --json`) should print the real draft, including its questions array.
        """
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
            self.assertNotIn("{", out)  # plain render, not JSON, without --json

            code, out = run(["plan", "draft", "feature-export", str(request_file), "--json"])
            self.assertEqual(0, code, out)
            document = json.loads(out)
            self.assertEqual("feature-export", document["initiative_id"])
            self.assertIsInstance(document["questions"], list)

    def test_plan_status_json_includes_questions_array(self):
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
            run(["plan", "draft", "feature-export", str(request_file)])

            code, out = run(["plan", "status", "feature-export", "--json"])
            self.assertEqual(0, code, out)
            document = json.loads(out)
            self.assertTrue(document["has_draft"])
            self.assertIsInstance(document["questions"], list)

            code, out = run(["plan", "status", "does-not-exist", "--json"])
            self.assertEqual(0, code, out)
            document = json.loads(out)
            self.assertFalse(document["has_draft"])
            self.assertEqual([], document["questions"])

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

    def test_multi_repository_plan_accepts_an_explicit_branching_dependency_graph(self):
        """plan/active/multi-repository-dependency-graph-planning.md: an explicit
        `depends_on` answer produces a real graph (here, repo-c depending on both
        of the other two), not just the declared-order straight chain."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repo_a, repo_b, repo_c = root / "repo-a", root / "repo-b", root / "repo-c"
            for repo in (repo_a, repo_b, repo_c):
                _make_gradle_repository(repo)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            self._onboard(run, "repo-a", repo_a, "Owns repo-a content.")
            self._onboard(run, "repo-b", repo_b, "Owns repo-b content.")
            self._onboard(run, "repo-c", repo_c, "Owns repo-c content.")

            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Cross-repo export.",
                "repositories": ["repo-a", "repo-b", "repo-c"],
            }), encoding="utf-8")
            code, out = run(["plan", "draft", "feature-branch", str(request_file)])
            self.assertEqual(0, code, out)
            self.assertIn("Suggested dependency order (default if left unanswered):", out)

            answers_file = root / "plan-answers.json"
            answers_file.write_text(json.dumps({
                "components": {"repo-a": ["content"], "repo-b": ["content"], "repo-c": ["content"]},
                "depends_on": {"repo-a": [], "repo-b": [], "repo-c": ["repo-a", "repo-b"]},
                "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
            }), encoding="utf-8")
            run(["plan", "answer", "feature-branch", str(answers_file)])

            code, out = run(["plan", "apply", "feature-branch"])
            self.assertEqual(0, code, out)
            self.assertIn("Dependency graph:", out)
            self.assertIn("repo-a: no dependencies", out)
            self.assertIn("repo-b: no dependencies", out)
            self.assertIn("repo-c: depends on repo-a, repo-b", out)

            from esc_exec.yaml_io import load_yaml
            task_a = load_yaml(repo_a / ".esc-ai/workflows/active/feature-branch-repo-a/task.yaml")
            task_b = load_yaml(repo_b / ".esc-ai/workflows/active/feature-branch-repo-b/task.yaml")
            task_c = load_yaml(repo_c / ".esc-ai/workflows/active/feature-branch-repo-c/task.yaml")
            self.assertNotIn("depends_on", task_a["task"]["initiative"])
            self.assertNotIn("depends_on", task_b["task"]["initiative"])
            self.assertEqual(
                ["repo-a/feature-branch-repo-a", "repo-b/feature-branch-repo-b"],
                task_c["task"]["initiative"]["depends_on"],
            )


class TopLevelMenuLoopTests(unittest.TestCase):
    """
    Regression: run_interactive used to run exactly one action and propagate its
    return code straight out of main() -- so completing (or even just failing)
    a single onboarding silently ended the whole session instead of returning to
    the home screen. It now loops until the user explicitly backs out.
    """

    def test_menu_returns_after_a_failed_action_instead_of_exiting(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            store = Store(root / "db.sqlite")

            responses = iter([
                "1",                              # Onboard a repository
                str(root / "does-not-exist-yet"),  # triggers the scaffold-wizard dead end
                "6",                               # Validate the system -- nothing registered yet
                "",                                # blank -> back out of the menu
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("Nothing found at", output)
            self.assertIn("No registered repositories to validate.", output)
            self.assertEqual(3, output.count("What would you like to do?"))


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
                "",                   # component confirmation: include all
                "2",                  # decline the "connect an AI provider?" offer (Yes/No menu)
                "Owns content.",      # answer to the purpose question
                "1",                  # confirm apply (Yes/No menu)
                "no",                 # decline "plan new work now?"
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
            self.assertIn("Repository map for `repo`", output)
            self.assertIn("purpose: Owns content.", output)

    def test_pressing_enter_after_apply_jumps_into_prefilled_planning(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            responses = iter([
                str(repository_dir),  # repository path
                "",                   # component confirmation: include all
                "2",                  # decline connect offer
                "Owns content.",      # purpose
                "1",                  # confirm apply
                "",                   # blank -> jump into planning, pre-filled with this repo
                # no "Repositories:" prompt -- pre-filled with the repo just onboarded
                "Add CSV export.",    # objective
                "feature-export",     # initiative id
                "1",                  # work type: feature
                "content",            # components question answer
                "No admin UI.",       # scope_boundary
                "Export works",       # completion_conditions
                "",                   # rollout_needs
                "1",                  # confirm plan apply
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
            self.assertNotIn("Repositories (comma-separated", output)
            self.assertIn("Planned.", output)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "feature-export"
            self.assertTrue((task_dir / "task.yaml").is_file())

    def test_empty_directory_suggests_scaffold_wizard(self):
        # Case 1 from plan/done/scaffold-new-or-empty-repository.md: a real
        # directory exists but no adapter detects a build system in it.
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            empty_dir = root / "empty-project"
            empty_dir.mkdir()
            store = Store(root / "db.sqlite")

            responses = iter([str(empty_dir)])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(1, code)
            output = buffer.getvalue()
            self.assertIn("No supported build system detected", output)
            self.assertIn("npx create-next-app@latest", output)
            self.assertNotIn("Traceback", output)

    def test_nonexistent_path_suggests_scaffold_wizard(self):
        # Case 2: nothing exists at that path at all, and it isn't a registered
        # repository ID either -- collapses into the same answer as case 1.
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            store = Store(root / "db.sqlite")

            responses = iter([str(root / "does-not-exist-yet")])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(1, code)
            output = buffer.getvalue()
            self.assertIn("Nothing found at", output)
            self.assertIn("npx create-next-app@latest", output)

    def test_ai_suggested_purpose_is_accepted_with_blank_input(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            original_suggest = cli.suggest_answers_via_provider
            cli.suggest_answers_via_provider = lambda registry, repository_path, purpose_ids, frameworks_ids, resume_session_id=None: {
                "content": {"purpose": "Owns lesson publishing."}
            }
            responses = iter([str(repository_dir), "", "2", "", "1", "no"])  # include all, decline connect offer, then blank -- accept the (mocked) suggestion
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
            cli.suggest_answers_via_provider = lambda registry, repository_path, purpose_ids, frameworks_ids, resume_session_id=None: {
                "content": {"purpose": "Owns lesson publishing."}
            }
            responses = iter([str(repository_dir), "", "2", "Actually owns something else.", "1", "no"])
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
                "",                   # component confirmation: include all
                "1", "1", "1",        # connect offer: yes -> claude -> subscription
                "",                   # accept the suggestion
                "1",                  # confirm apply (Yes)
                "no",                 # decline "plan new work now?"
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

            responses = iter([str(repository_dir), "", "2", "Owns content.", "1", "no"])
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

            responses = iter([str(repository_dir), "", "2", "Owns content.", "2"])
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
            first_responses = iter([str(repository_dir), "", "2", "Owns content.", "2"])
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
            second_responses = iter(["1", "", "2", "Owns content.", "1", "no"])  # pick unfinished repo -> include all -> decline connect offer -> answer -> apply -> decline plan
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

            first_responses = iter([str(repository_dir), "", "2", "Owns content.", "1", "no"])
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


class ComponentConfirmationTests(unittest.TestCase):
    """plan/active/generic-multi-component-detection.md design section 4 --
    always-shown confirmation step, wired into run_onboarding_interactive."""

    def test_excluding_a_component_writes_no_manifest_for_it(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_multi_component_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            responses = iter([
                str(repository_dir),  # repository path
                "2",                  # exclude component #2 (sample)
                "2",                  # decline connect offer
                "Owns core.",         # purpose for the remaining component
                "1",                  # confirm apply
                "no",                 # decline "plan new work now?"
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
            self.assertIn("2 component(s) found", buffer.getvalue())
            self.assertTrue(component_manifest_path(repository_dir, "core").is_file())
            self.assertFalse(component_manifest_path(repository_dir, "sample").is_file())
            repository = load_yaml(repository_manifest_path(repository_dir))
            self.assertEqual(["sample"], repository["excluded_components"])

    def test_cancelling_the_confirmation_step_writes_nothing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")

            responses = iter([str(repository_dir)])
            original_input = builtins.input

            def fake_input(prompt=""):
                try:
                    return next(responses)
                except StopIteration:
                    raise EOFError()

            builtins.input = fake_input
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertEqual(0, code)
            self.assertIn("Cancelled", buffer.getvalue())
            self.assertFalse((repository_dir / ".esc-ai" / "esc-execution.yaml").exists())


class ModuleResolutionInteractiveTests(unittest.TestCase):
    """plan/active/generic-multi-component-detection.md design section 3 --
    generic AI-fallback module-resolution turn, wired into
    run_onboarding_interactive ahead of the confirmation step."""

    def test_unresolved_module_is_ai_resolved_and_onboarded(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository_with_unresolved_module(repository_dir)
            store = Store(root / "db.sqlite")
            set_provider(registry, "claude", "subscription")

            original_suggest = cli.suggest_unresolved_components
            cli.suggest_unresolved_components = lambda client, repository_path, unresolved: {
                "resolved": {":ghost": "actual-ghost-dir"}, "session_id": "ses-1",
            }
            original_groundable = cli.suggest_answers_via_provider
            cli.suggest_answers_via_provider = lambda *args, **kwargs: {}

            responses = iter([
                str(repository_dir),  # repository path
                "",                   # component confirmation: include all
                "Owns content.",      # purpose: content
                "Owns the ghost.",    # purpose: ghost
                "1",                  # confirm apply
                "no",                 # decline "plan new work now?"
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_onboarding_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.suggest_unresolved_components = original_suggest
                cli.suggest_answers_via_provider = original_groundable

            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("could not be resolved to a real directory", output)
            self.assertIn(":ghost -> actual-ghost-dir", output)
            manifest = load_yaml(repository_manifest_path(repository_dir))
            self.assertIn("ghost", [c["id"] for c in manifest["components"]])
            self.assertEqual({":ghost": "actual-ghost-dir"}, manifest["resolved_components"])

    def test_unresolved_module_without_a_provider_is_skipped_with_explanation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository_with_unresolved_module(repository_dir)
            store = Store(root / "db.sqlite")

            responses = iter([
                str(repository_dir),  # repository path
                "",                   # component confirmation: include all (only "content" is offered)
                "2",                  # decline connect offer
                "Owns content.",      # purpose: content
                "1",                  # confirm apply
                "no",                 # decline "plan new work now?"
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
            self.assertIn("could not be resolved to a real directory: :ghost", output)
            self.assertIn("Connect an AI provider", output)
            self.assertFalse(component_manifest_path(repository_dir, "ghost").is_file())


class OfferLocalArchitectureNoteInteractiveTests(unittest.TestCase):
    """plan/active/planning-consistency-checks.md design section 2."""

    def _setup(self, temp, profile_ids=("PAT-X",)):
        root = Path(temp)
        registry = root / "registry.yaml"
        repository_dir = root / "repo-checkout"
        _make_gradle_repository(repository_dir)
        generate_gradle_manifests(repository_dir)
        manifest_path = component_manifest_path(repository_dir, "content")
        manifest = load_yaml(manifest_path)
        manifest["component"]["purpose"] = "Owns content."
        if profile_ids:
            manifest["architecture"] = {"profile_ids": list(profile_ids)}
        write_yaml(manifest_path, manifest)
        generate_indexes(repository_dir)

        framework_dir = root / "framework-checkout"
        framework_dir.mkdir()
        (framework_dir / "index.json").write_text(json.dumps({"documents": [
            {"id": "PAT-X", "path": "patterns/x.md", "tags": ["x"], "requires": [], "layer": "patterns"},
        ]}), encoding="utf-8")
        add_route(registry, "frameworks", "esc-ai-architecture-framework", framework_dir)
        return registry, repository_dir

    def test_no_provider_connected_returns_empty_without_calling_out(self):
        with TemporaryDirectory() as temp:
            registry, repository_dir = self._setup(temp)
            notes = cli.offer_local_architecture_note_interactive(registry, repository_dir, "Add background jobs.", ["content"])
        self.assertEqual([], notes)

    def test_framework_route_unresolvable_returns_empty(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            generate_gradle_manifests(repository_dir)
            manifest_path = component_manifest_path(repository_dir, "content")
            manifest = load_yaml(manifest_path)
            manifest["component"]["purpose"] = "Owns content."
            write_yaml(manifest_path, manifest)
            generate_indexes(repository_dir)
            set_provider(registry, "claude", "subscription")
            notes = cli.offer_local_architecture_note_interactive(registry, repository_dir, "Add background jobs.", ["content"])
        self.assertEqual([], notes)

    def test_covered_result_returns_empty(self):
        with TemporaryDirectory() as temp:
            registry, repository_dir = self._setup(temp)
            set_provider(registry, "claude", "subscription")
            original = cli.suggest_architecture_coverage_gap
            cli.suggest_architecture_coverage_gap = lambda client, framework_root, objective, documents: {
                "covered": True, "reasoning": None, "suggested_title": None,
            }
            try:
                notes = cli.offer_local_architecture_note_interactive(registry, repository_dir, "Add a REST endpoint.", ["content"])
            finally:
                cli.suggest_architecture_coverage_gap = original
        self.assertEqual([], notes)

    def test_gap_declined_returns_empty_and_writes_no_file(self):
        with TemporaryDirectory() as temp:
            registry, repository_dir = self._setup(temp)
            set_provider(registry, "claude", "subscription")
            original = cli.suggest_architecture_coverage_gap
            cli.suggest_architecture_coverage_gap = lambda client, framework_root, objective, documents: {
                "covered": False, "reasoning": "Not covered.", "suggested_title": "Background Jobs",
            }
            responses = iter(["2"])  # decline the "draft a note?" confirm
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                notes = cli.offer_local_architecture_note_interactive(registry, repository_dir, "Add background jobs.", ["content"])
            finally:
                builtins.input = original_input
                cli.suggest_architecture_coverage_gap = original
        self.assertEqual([], notes)
        self.assertFalse((repository_dir / ".esc-ai" / "local-architecture").exists())

    def test_gap_accepted_writes_note_and_returns_its_path(self):
        with TemporaryDirectory() as temp:
            registry, repository_dir = self._setup(temp)
            set_provider(registry, "claude", "subscription")
            original = cli.suggest_architecture_coverage_gap
            cli.suggest_architecture_coverage_gap = lambda client, framework_root, objective, documents: {
                "covered": False, "reasoning": "Not covered.", "suggested_title": "Background Jobs",
            }
            responses = iter(["1", "Use a queue-backed worker."])  # accept, then describe the guidance
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                notes = cli.offer_local_architecture_note_interactive(registry, repository_dir, "Add background jobs.", ["content"])
            finally:
                builtins.input = original_input
                cli.suggest_architecture_coverage_gap = original
            self.assertEqual([".esc-ai/local-architecture/background-jobs.md"], notes)
            text = (repository_dir / notes[0]).read_text(encoding="utf-8")
            self.assertIn("status: stub", text)
            self.assertIn("Use a queue-backed worker.", text)


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
                "repo",                  # repositories
                "Add CSV export.",       # objective
                "feature-export",        # initiative id
                "1",                     # work type: feature
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

    def test_offered_local_architecture_note_flows_into_the_written_task(self):
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

            original = cli.offer_local_architecture_note_interactive
            cli.offer_local_architecture_note_interactive = lambda registry, repository_path, objective, components: [
                ".esc-ai/local-architecture/background-jobs.md"
            ]
            responses = iter([
                "repo", "Add background jobs.", "feature-jobs", "1", "content",
                "No admin UI.", "Jobs run reliably", "", "1",
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.offer_local_architecture_note_interactive = original

            self.assertEqual(0, code)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "feature-jobs"
            readme = (task_dir / "README.md").read_text()
            self.assertIn("## Local architecture notes (unreviewed)", readme)
            self.assertIn(".esc-ai/local-architecture/background-jobs.md", readme)
            task_document = load_yaml(task_dir / "task.yaml")
            self.assertNotIn("local_architecture_notes", task_document)

    def _onboard(self, root, registry, repository_dir):
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

    def test_no_provider_connected_skips_the_drift_check_silently(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")
            self._onboard(root, registry, repository_dir)

            responses = iter([
                "repo", "Fix the broken login page.", "login-fix", "2",
                "content", "No new auth providers.", "Login works again.", "",
                "1",  # confirm apply -- no drift-check prompt in between
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
            self.assertIn("Planned.", buffer.getvalue())
            self.assertNotIn("grown from", buffer.getvalue())
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "login-fix"
            self.assertEqual("fix", load_yaml(task_dir / "task.yaml").get("task", {}).get("work_type", "fix"))

    def test_drift_detected_and_accepted_reclassifies_the_task(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")
            self._onboard(root, registry, repository_dir)
            set_provider(registry, "claude", "subscription")

            original_drift = cli.suggest_work_type_drift
            cli.suggest_work_type_drift = lambda client, repository_path, work_type, objective, scope_boundary, completion_conditions: {
                "drifted": True, "suggested_work_type": "feature",
                "reasoning": "This adds a new password-reset flow, not just a correction.",
            }

            responses = iter([
                "repo", "Fix the broken login page.", "login-fix", "2",
                "2",  # decline "Talk through this plan with AI first?"
                "content", "No new auth providers.", "Login works again.", "",
                "1",  # reclassify as `feature`
                "1",  # confirm apply
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.suggest_work_type_drift = original_drift

            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("grown from `fix` into `feature`", output)
            self.assertIn("password-reset flow", output)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "login-fix"
            self.assertIn("**Work type:** feature", (task_dir / "README.md").read_text())

    def test_drift_detected_and_declined_keeps_the_original_type(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")
            self._onboard(root, registry, repository_dir)
            set_provider(registry, "claude", "subscription")

            original_drift = cli.suggest_work_type_drift
            cli.suggest_work_type_drift = lambda client, repository_path, work_type, objective, scope_boundary, completion_conditions: {
                "drifted": True, "suggested_work_type": "feature", "reasoning": "Looks like new behavior.",
            }

            responses = iter([
                "repo", "Fix the broken login page.", "login-fix", "2",
                "2",  # decline "Talk through this plan with AI first?"
                "content", "No new auth providers.", "Login works again.", "",
                "2",  # keep it as `fix`
                "1",  # confirm apply
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.suggest_work_type_drift = original_drift

            self.assertEqual(0, code)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "login-fix"
            self.assertIn("**Work type:** fix", (task_dir / "README.md").read_text())

    def test_no_drift_reported_proceeds_without_a_prompt(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")
            self._onboard(root, registry, repository_dir)
            set_provider(registry, "claude", "subscription")

            original_drift = cli.suggest_work_type_drift
            cli.suggest_work_type_drift = lambda client, repository_path, work_type, objective, scope_boundary, completion_conditions: {
                "drifted": False, "suggested_work_type": None, "reasoning": None,
            }

            responses = iter([
                "repo", "Fix the broken login page.", "login-fix", "2",
                "2",  # decline "Talk through this plan with AI first?"
                "content", "No new auth providers.", "Login works again.", "",
                "1",  # confirm apply -- no drift-check prompt in between
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.suggest_work_type_drift = original_drift

            self.assertEqual(0, code)
            self.assertNotIn("grown from", buffer.getvalue())
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "login-fix"
            self.assertIn("**Work type:** fix", (task_dir / "README.md").read_text())

    def test_chat_about_it_is_not_offered_for_multi_repository_plans(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            second_dir = root / "repo-two-checkout"
            _make_gradle_repository(repository_dir)
            _make_gradle_repository(second_dir)
            store = Store(root / "db.sqlite")
            self._onboard(root, registry, repository_dir)
            add_route(registry, "repositories", "repo-two", second_dir)

            responses = iter([
                "repo,repo-two", "Add a shared audit log.", "audit-log", "1",  # work type: feature
                "content",  # repo components
                "",         # repo depends_on (no predecessor)
                "content",  # repo-two components
                "",         # repo-two depends_on (blank -- accepts no explicit dependency)
                "No admin UI.", "Log entries recorded", "",
                "2",  # decline apply -- just checking the menu offered here
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input

            self.assertNotIn(cli.CHAT_ABOUT_IT_OPTION, buffer.getvalue())

    def test_form_conversation_result_skips_already_answered_static_questions(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")
            self._onboard(root, registry, repository_dir)
            set_provider(registry, "claude", "subscription")

            original = cli.run_form_driven_planning_conversation_interactive
            cli.run_form_driven_planning_conversation_interactive = lambda registry, repository_path, objective: {
                "work_type": "feature",
                "objective": "Add background job scheduling.",
                "components": ["content"],
                "scope_boundary": "No cron UI.",
                "completion_conditions": ["Jobs run on schedule"],
                "rollout_needs": "Feature-flagged.",
            }

            responses = iter([
                "repo", "Add jobs.", "feature-jobs",
                str(len(cli.WORK_TYPES) + 1),  # "chat about it"
                "1",  # confirm apply -- no other question prompts should be consumed
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.run_form_driven_planning_conversation_interactive = original

            self.assertEqual(0, code)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "feature-jobs"
            readme = (task_dir / "README.md").read_text()
            self.assertIn("**Work type:** feature", readme)
            self.assertIn("Add background job scheduling.", readme)
            self.assertIn("Jobs run on schedule", readme)
            self.assertIn("Feature-flagged.", readme)

    def test_form_conversation_missing_a_field_falls_back_to_the_plain_question(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")
            self._onboard(root, registry, repository_dir)
            set_provider(registry, "claude", "subscription")

            original = cli.run_form_driven_planning_conversation_interactive
            cli.run_form_driven_planning_conversation_interactive = lambda registry, repository_path, objective: {
                "work_type": "feature",
                "objective": "Add background job scheduling.",
                # components/scope_boundary/completion_conditions/rollout_needs never captured
            }

            responses = iter([
                "repo", "Add jobs.", "feature-jobs", str(len(cli.WORK_TYPES) + 1),  # "chat about it"
                "content", "No cron UI.", "Jobs run on schedule", "",  # the fallback static questions
                "1",  # confirm apply
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.run_form_driven_planning_conversation_interactive = original

            self.assertEqual(0, code)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "feature-jobs"
            readme = (task_dir / "README.md").read_text()
            self.assertIn("Jobs run on schedule", readme)

    def test_chat_about_it_with_no_work_type_confirmed_falls_back_to_the_menu(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            store = Store(root / "db.sqlite")
            self._onboard(root, registry, repository_dir)
            set_provider(registry, "claude", "subscription")

            original = cli.run_form_driven_planning_conversation_interactive
            cli.run_form_driven_planning_conversation_interactive = lambda registry, repository_path, objective: None

            responses = iter([
                "repo", "Add jobs.", "feature-jobs", str(len(cli.WORK_TYPES) + 1),  # "chat about it"
                "1",  # fallback five-item menu: feature
                "2",  # decline "Talk through this plan with AI first?" (old scope conversation, still offered)
                "content", "No cron UI.", "Jobs run on schedule", "",
                "1",  # confirm apply
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_planning_interactive(store, registry)
            finally:
                builtins.input = original_input
                cli.run_form_driven_planning_conversation_interactive = original

            self.assertEqual(0, code)
            task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / "feature-jobs"
            self.assertIn("**Work type:** feature", (task_dir / "README.md").read_text())


class RunFormDrivenPlanningConversationInteractiveTests(unittest.TestCase):
    def test_no_provider_connected_returns_none_immediately(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                form = cli.run_form_driven_planning_conversation_interactive(registry, repository_dir, "Add jobs.")
        self.assertIsNone(form)
        self.assertIn("pick a work type from the list instead", buffer.getvalue())

    def test_converges_and_confirmed_returns_the_full_form(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            generate_gradle_manifests(repository_dir)
            manifest_path = component_manifest_path(repository_dir, "content")
            manifest = load_yaml(manifest_path)
            manifest["component"]["purpose"] = "Owns content."
            write_yaml(manifest_path, manifest)
            generate_indexes(repository_dir)
            set_provider(registry, "claude", "subscription")

            original = cli.suggest_form_turn
            cli.suggest_form_turn = lambda client, repository_path, message, objective, suggested_components, resume_session_id=None: {
                "reply": "Got it, that's everything.",
                "form": {
                    "work_type": "feature", "objective": "Add jobs.", "components": ["content"],
                    "scope_boundary": "No UI.", "completion_conditions": ["Jobs run"], "rollout_needs": "",
                },
                "session_id": "ses-1", "threshold": None,
            }
            responses = iter(["1"])  # confirm "Use this and finish up?"
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                form = cli.run_form_driven_planning_conversation_interactive(registry, repository_dir, "Add jobs.")
            finally:
                builtins.input = original_input
                cli.suggest_form_turn = original

        self.assertEqual("feature", form["work_type"])
        self.assertEqual(["Jobs run"], form["completion_conditions"])

    def test_blank_line_early_stops_with_whatever_was_captured(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            generate_gradle_manifests(repository_dir)
            manifest_path = component_manifest_path(repository_dir, "content")
            manifest = load_yaml(manifest_path)
            manifest["component"]["purpose"] = "Owns content."
            write_yaml(manifest_path, manifest)
            generate_indexes(repository_dir)
            set_provider(registry, "claude", "subscription")

            original = cli.suggest_form_turn
            cli.suggest_form_turn = lambda client, repository_path, message, objective, suggested_components, resume_session_id=None: {
                "reply": "What kind of change is this?", "form": {}, "session_id": "ses-1", "threshold": None,
            }
            responses = iter([""])  # blank line -- stop immediately
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                form = cli.run_form_driven_planning_conversation_interactive(registry, repository_dir, "Add jobs.")
            finally:
                builtins.input = original_input
                cli.suggest_form_turn = original

        self.assertIsNone(form)

    def test_hard_threshold_stops_the_loop(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            generate_gradle_manifests(repository_dir)
            manifest_path = component_manifest_path(repository_dir, "content")
            manifest = load_yaml(manifest_path)
            manifest["component"]["purpose"] = "Owns content."
            write_yaml(manifest_path, manifest)
            generate_indexes(repository_dir)
            set_provider(registry, "claude", "subscription")

            original = cli.suggest_form_turn
            cli.suggest_form_turn = lambda client, repository_path, message, objective, suggested_components, resume_session_id=None: {
                "reply": "Still talking...", "form": {"work_type": "feature"}, "session_id": "ses-1", "threshold": "hard",
            }
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    form = cli.run_form_driven_planning_conversation_interactive(registry, repository_dir, "Add jobs.")
            finally:
                cli.suggest_form_turn = original

        self.assertEqual({"work_type": "feature"}, form)
        self.assertIn("wrapping up now", buffer.getvalue())


class ConfirmWorkTypeDriftInteractiveTests(unittest.TestCase):
    def test_no_provider_returns_work_type_unchanged_without_calling_out(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            result = cli.confirm_work_type_drift_interactive(registry, root, "fix", "obj", "scope", ["done"])
        self.assertEqual("fix", result)

    def test_wrong_provider_route_returns_work_type_unchanged(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            set_provider(registry, "claude", "api-key")
            result = cli.confirm_work_type_drift_interactive(registry, root, "fix", "obj", "scope", ["done"])
        self.assertEqual("fix", result)


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

    def test_confirm_step_shows_current_and_proposed_roadmap_before_asking(self):
        # ai-conversation-primitive.md open question 7, resolved by
        # plan/done/project-vision-and-direction.md design 3: this confirm used to
        # ask a yes/no question about an unseen change.
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            set_provider(registry, "claude", "subscription")
            save_project_roadmap(repository_dir, "repo", "Old purpose.", "Old stage.", "Old direction.")

            compaction_payload = json.dumps({
                "progress": {"completed": [], "decisions": [], "remaining": [], "open_questions": []},
                "roadmap": {
                    "purpose": "New purpose.", "current_stage": "New stage.",
                    "direction": "New direction.", "durable_decisions": [],
                },
            })
            fake_client = _FakeConversationClient([
                _conversation_stream("What format should the export use?"),
                _conversation_stream(compaction_payload),
            ])

            responses = iter(["1", "", "1"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer), patch("esc_orchestrator.escape_ai_cli.ClaudeCodeClient", return_value=fake_client):
                    cli.run_planning_conversation_interactive(registry, repository_dir, "repo", "init-1", "Add CSV export.")
            finally:
                builtins.input = original_input

            output = buffer.getvalue()
            self.assertIn("Current roadmap:", output)
            self.assertIn("Old purpose.", output)
            self.assertIn("Proposed roadmap:", output)
            self.assertIn("New purpose.", output)

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

    def test_render_execution_preview_states_full_autonomy_policy(self):
        # Since plan/done/pre-flight-consent-and-bounded-autonomy.md's category-level
        # grant landed, resolve_default_policy() is no longer read-only-by-placeholder
        # -- the preview's trailing Policy line should say so, not call it a
        # placeholder. No policy_document is passed here, so this exercises the
        # generic (no-named-profile-known) branch of that line.
        task_document = {
            "task": {"objective": "Add CSV export."},
            "scope": {"components": ["content"]},
        }
        rendered = cli.render_execution_preview("repo", "feature-export", task_document)
        self.assertIn("repo/feature-export", rendered)
        self.assertIn("content", rendered)
        self.assertIn("full autonomy", rendered)

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

    def test_render_execution_preview_without_policy_omits_scope_line(self):
        task_document = {"task": {"objective": "Add CSV export."}, "scope": {"components": ["content"]}}
        rendered = cli.render_execution_preview("repo", "feature-export", task_document)
        self.assertNotIn("Scope:", rendered)

    def test_render_execution_preview_first_time_explains_scope(self):
        task_document = {"task": {"objective": "Add CSV export."}, "scope": {"components": ["content"]}}
        policy = {"permissions": {"read": "allow", "edit": "allow"}}
        rendered = cli.render_execution_preview("repo", "feature-export", task_document, policy_document=policy)
        self.assertIn("Scope: this run will be granted -- read, edit.", rendered)
        self.assertNotIn("already consented", rendered)

    def test_render_execution_preview_already_consented_for_matching_prior_grant(self):
        task_document = {"task": {"objective": "Add CSV export."}, "scope": {"components": ["content"]}}
        policy = {"permissions": {"read": "allow", "edit": "allow"}}
        prior = {"granted_categories": ["edit", "read"], "granted_at": "2026-07-24T00:00:00Z"}
        rendered = cli.render_execution_preview(
            "repo", "feature-export", task_document, policy_document=policy, prior_consent_record=prior,
        )
        self.assertIn("already consented on 2026-07-24T00:00:00Z", rendered)

    def test_render_execution_preview_reexplains_scope_when_categories_widen(self):
        task_document = {"task": {"objective": "Add CSV export."}, "scope": {"components": ["content"]}}
        policy = {"permissions": {"read": "allow", "edit": "allow", "execute": "allow"}}
        prior = {"granted_categories": ["read"], "granted_at": "2026-07-24T00:00:00Z"}
        rendered = cli.render_execution_preview(
            "repo", "feature-export", task_document, policy_document=policy, prior_consent_record=prior,
        )
        self.assertNotIn("already consented", rendered)
        self.assertIn("Scope: this run will be granted -- read, edit, execute.", rendered)

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


class _FakeVerificationFailingRuntime:
    """Never raises -- the adapter itself reports done -- but writes a
    verification-result.json whose independently-executed gates came back
    not-clean, exercising task 6's checkpoint trigger end to end through the CLI."""

    def __init__(self, output_root: Path):
        self.output_root = output_root

    def execute(self, contracts):
        path = self.output_root / f"{contracts['task']['task']['id']}-verify-fail-run"
        path.mkdir(parents=True, exist_ok=True)
        (path / "verification-result.json").write_text(json.dumps({
            "schema_version": 1,
            "task_id": contracts["task"]["task"]["id"],
            "generated_at": "2026-07-23T00:00:00Z",
            "status": "failed",
            "gates": [{
                "id": "final",
                "outcome": "completed",
                "checks": [{
                    "id": "test",
                    "command": ["true"],
                    "status": "failed",
                    "exit_code": 1,
                    "duration_ms": 10,
                    "stdout_path": None,
                    "stderr_path": None,
                    "report_path": None,
                }],
            }],
        }))
        return path


class _FakePermissionDenialRuntime:
    """Never raises and never fails verification -- but writes a
    permission-denials.json the way a real ClaudeCodeAdapter run would when
    the hard-deny list blocked a tool call, exercising layer 6's checkpoint
    trigger end to end through the CLI (see
    plan/future/pre-flight-consent-and-bounded-autonomy.md)."""

    def __init__(self, output_root: Path):
        self.output_root = output_root

    def execute(self, contracts):
        path = self.output_root / f"{contracts['task']['task']['id']}-denial-run"
        path.mkdir(parents=True, exist_ok=True)
        (path / "permission-denials.json").write_text(json.dumps({
            "schema_version": 1,
            "task_id": contracts["task"]["task"]["id"],
            "generated_at": "2026-07-24T00:00:00Z",
            "denials": [{"tool_name": "Bash", "tool_input": {"command": "rm -rf build/"}}],
        }))
        return path


class _FakeWorktreeSucceedingRuntime:
    """
    Simulates what a real ClaudeCodeAdapter.execute run against
    workspace.kind == "worktree" does: create the task's worktree via the real
    `esc_exec.worktree` module (so this exercises real git, not a mock),
    optionally leave an edit in it, then finalize (commit + keep-if-diff) --
    and record the resulting `bindings.worktree` in a real run.json the same
    shape ClaudeCodeAdapter.execute writes, so checkpoint_candidate's
    worktree-aware branch has something real to read.
    """

    def __init__(self, output_root: Path, repository: Path, leave_a_diff: bool):
        self.output_root, self.repository, self.leave_a_diff = output_root, repository, leave_a_diff

    def execute(self, contracts):
        from esc_exec.worktree import ensure_worktree, finalize_worktree, worktree_branch
        task_id = contracts["task"]["task"]["id"]
        worktree = ensure_worktree(self.repository, task_id)
        if self.leave_a_diff:
            (worktree / "agent-output.txt").write_text("agent output\n", encoding="utf-8")
        kept = finalize_worktree(self.repository, task_id, f"escape-ai task {task_id}")
        path = self.output_root / f"{task_id}-worktree-run"
        path.mkdir(parents=True, exist_ok=True)
        (path / "run.json").write_text(json.dumps({
            "bindings": {"worktree": {"branch": worktree_branch(task_id), "kept": kept}},
        }), encoding="utf-8")
        return path


def _git_init(repository: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, check=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("add", "-A")
    run("commit", "-q", "-m", "initial")


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
            candidate = cli.checkpoint_candidate(store, repository_dir, "feature-export")
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

    def test_verification_failure_produces_promotable_checkpoint(self):
        """
        Task 6: a run whose adapter reported success but whose independently-
        executed verification result came back not-clean must produce the same
        reviewable/promotable checkpoint candidate an uncaught exception does --
        exercised here through the real CLI checkpoint_candidate/promote_checkpoint/
        active_work flow, not just at the Scheduler unit level (see
        tests/test_orchestrator.py).
        """
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

            result = cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"}, runtime=_FakeVerificationFailingRuntime(root / "runs"),
            )
            self.assertEqual("failed", result["status"])
            self.assertIn("final.test", result["error"])

            items = cli.active_work(store, registry)
            self.assertEqual("failed", items[0]["latest_run_status"])
            self.assertTrue(items[0]["checkpoint_present"])

            candidate = cli.checkpoint_candidate(store, repository_dir, "feature-export")
            self.assertEqual("blocked", candidate["checkpoint"]["status"])
            self.assertEqual(1, len(candidate["progress"]["blockers"]))
            self.assertIn("final.test", candidate["progress"]["blockers"][0])
            durable_path = cli.promote_checkpoint(repository_dir, "feature-export", candidate)
            self.assertTrue(durable_path.is_file())
            from esc_exec.contracts import validate_contract
            from esc_exec.model import ManifestState
            self.assertEqual(ManifestState.VALID, validate_contract("checkpoint", durable_path).state)

    def test_permission_denial_produces_promotable_checkpoint(self):
        """
        Layer 6: a run with a permission denial but no verification failure
        must produce the same reviewable/promotable checkpoint candidate a
        verification failure or uncaught exception does -- exercised here
        through the real CLI checkpoint_candidate/promote_checkpoint/
        active_work flow, mirroring
        test_verification_failure_produces_promotable_checkpoint above.
        """
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

            result = cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"}, runtime=_FakePermissionDenialRuntime(root / "runs"),
            )
            self.assertEqual("waiting-approval", result["status"])
            self.assertIn("permission denied", result["error"])

            items = cli.active_work(store, registry)
            self.assertEqual("waiting-approval", items[0]["latest_run_status"])
            self.assertTrue(items[0]["checkpoint_present"])

            candidate = cli.checkpoint_candidate(store, repository_dir, "feature-export")
            self.assertEqual("blocked", candidate["checkpoint"]["status"])
            self.assertEqual(1, len(candidate["progress"]["blockers"]))
            self.assertIn("permission denied", candidate["progress"]["blockers"][0])
            durable_path = cli.promote_checkpoint(repository_dir, "feature-export", candidate)
            self.assertTrue(durable_path.is_file())
            from esc_exec.contracts import validate_contract
            from esc_exec.model import ManifestState
            self.assertEqual(ManifestState.VALID, validate_contract("checkpoint", durable_path).state)

    def test_task_impact_and_auto_advancement_for_cross_repository_task(self):
        """
        Task 2/7: a multi-repository initiative's second task (repo-b) depends_on the
        first (repo-a). Once repo-a's task actually completes (through real
        Store/Scheduler execution, not a synthetic shortcut), task 7's event-driven
        automatic advancement submits repo-b's task itself -- no second execute_task
        call needed -- and it runs to completion within the same execute_task call
        (Scheduler.submit puts it on the same queue execute_task's queue.join() drains).
        Also covers `plan ready` (parallel-dispatch discovery, headless-backdoor-
        mode.md's follow-on): only repo-a is ready before anything runs (repo-b
        depends on it), and nothing is ready once both have run.
        `task impact` on repo-a afterward correctly shows nothing left to unblock, since
        repo-b already ran; `store.get_task` confirms it actually happened rather than
        merely being reported as possible.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "db.sqlite"
            registry = root / "registry.yaml"
            repo_a = root / "repo-a"
            repo_b = root / "repo-b"
            _make_gradle_repository(repo_a)
            _make_gradle_repository(repo_b)
            store = Store(db)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            for repo_id, repository_dir in (("repo-a", repo_a), ("repo-b", repo_b)):
                run(["repository", "add", repo_id, str(repository_dir)])
                run(["repository", "analyze", repo_id, "--json"])
                answers_file = root / f"{repo_id}-answers.json"
                answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
                run(["repository", "answer", repo_id, str(answers_file)])
                code, out = run(["repository", "apply", repo_id])
                self.assertEqual(0, code, out)

            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Cross-repo export.",
                "repositories": ["repo-a", "repo-b"],
            }), encoding="utf-8")
            run(["plan", "draft", "feature-cross", str(request_file)])
            answers_file = root / "plan-answers.json"
            answers_file.write_text(json.dumps({
                "components": {"repo-a": ["content"], "repo-b": ["content"]},
                "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
            }), encoding="utf-8")
            run(["plan", "answer", "feature-cross", str(answers_file)])
            code, out = run(["plan", "apply", "feature-cross"])
            self.assertEqual(0, code, out)
            # plan/done/run-outcome-surfacing.md finding #7: the resolved dependency
            # graph is printed in the apply output itself, not just discoverable by
            # reading task.yaml's depends_on by hand. No explicit "depends_on"
            # answer was supplied above, so this falls back to the declared-order
            # chain (plan/active/multi-repository-dependency-graph-planning.md).
            self.assertIn("Dependency graph:", out)
            self.assertIn("repo-a: no dependencies", out)
            self.assertIn("repo-b: depends on repo-a", out)

            # Parallel dispatch (headless-backdoor-mode.md follow-on): before anything
            # runs, only repo-a's task is ready -- repo-b's depends_on it.
            code, out = run(["plan", "ready", "feature-cross"])
            self.assertEqual(0, code, out)
            self.assertEqual(["repo-a/feature-cross-repo-a"], json.loads(out))

            # Before repo-a's task has ever run, Store has no record of it yet.
            code, out = run(["task", "impact", "feature-cross-repo-a"])
            self.assertEqual(1, code, out)
            self.assertIn("no such task", out)

            result = cli.execute_task(
                store, registry, "repo-a", repo_a, "feature-cross-repo-a",
                {"id": "claude", "route": "api-key"}, runtime=_FakeSucceedingRuntime(root / "runs"),
            )
            self.assertEqual("succeeded", result["status"])

            # repo-b's task was never explicitly submitted -- task 7 auto-advanced it.
            self.assertEqual("succeeded", store.get_task("feature-cross-repo-b")["status"])

            code, out = run(["task", "impact", "feature-cross-repo-a"])
            self.assertEqual(0, code, out)
            document = json.loads(out)
            self.assertEqual("feature-cross", document["initiative_id"])
            self.assertEqual([], document["newly_unblocked"])
            self.assertEqual({}, document["still_blocked"])

            # Both tasks have Store history now (one directly, one auto-advanced) --
            # nothing left ready to dispatch.
            code, out = run(["plan", "ready", "feature-cross"])
            self.assertEqual(0, code, out)
            self.assertEqual([], json.loads(out))

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
            self.assertIn("full autonomy", out)
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

    def test_task_doctor_reports_clean_for_a_satisfied_task(self):
        """
        plan/active/pre-flight-doctor-and-gate-prerequisites.md: `task doctor` runs
        the same pre-dispatch checks `task run` would, without ever building a
        run -- a task with no declared prerequisites (the common case today) and
        complete architecture coverage should report clean.
        """
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

            code, out = run(["task", "doctor", "repo", "feature-export"])
            self.assertEqual(0, code)
            self.assertIn("CLEAN", out)

    def test_task_doctor_reports_unsatisfied_prerequisite_without_dispatching(self):
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

            profile_path = repository_dir / ".esc-ai" / "components" / "content" / "esc-verification-profile.yaml"
            profile = load_yaml(profile_path)
            profile["gates"]["final"][0]["prerequisites"] = [
                {"kind": "env", "name": "ESC_AI_TEST_CLI_MISSING_TOKEN_XYZ"},
            ]
            write_yaml(profile_path, profile)

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

            code, out = run(["task", "doctor", "repo", "feature-export"])
            self.assertEqual(1, code)
            self.assertIn("BLOCKED", out)
            self.assertIn("ESC_AI_TEST_CLI_MISSING_TOKEN_XYZ", out)

    def test_task_doctor_unknown_task_id_is_invalid(self):
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
            code, out = run(["task", "doctor", "repo", "does-not-exist"])
            self.assertEqual(1, code)
            self.assertIn("INVALID", out)

    def test_task_doctor_unknown_task_id_suggests_real_ones(self):
        """
        plan/done/cli-discoverability.md finding #1: the documented multi-repo
        convention is `<initiative-id>-<repository-id>` -- a user who guesses
        just the initiative ID should be told what the real task IDs are,
        instead of a flat "not found".
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repo_a, repo_b = root / "repo-a", root / "repo-b"
            _make_gradle_repository(repo_a)
            _make_gradle_repository(repo_b)

            def run(argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
                return code, buffer.getvalue()

            for repo_id, repository_dir in (("repo-a", repo_a), ("repo-b", repo_b)):
                run(["repository", "add", repo_id, str(repository_dir)])
                run(["repository", "analyze", repo_id, "--json"])
                answers_file = root / f"{repo_id}-answers.json"
                answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
                run(["repository", "answer", repo_id, str(answers_file)])
                run(["repository", "apply", repo_id])
            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Cross-repo export.",
                "repositories": ["repo-a", "repo-b"],
            }), encoding="utf-8")
            run(["plan", "draft", "feature-cross", str(request_file)])
            answers_file = root / "plan-answers.json"
            answers_file.write_text(json.dumps({
                "components": {"repo-a": ["content"], "repo-b": ["content"]},
                "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
            }), encoding="utf-8")
            run(["plan", "answer", "feature-cross", str(answers_file)])
            run(["plan", "apply", "feature-cross"])

            # A user guesses just the initiative ID, not knowing the real
            # convention -- exactly the real dogfooding case.
            code, out = run(["task", "doctor", "repo-a", "feature-cross"])
            self.assertEqual(1, code)
            self.assertIn("INVALID", out)
            self.assertIn("did you mean: feature-cross-repo-a", out)

            code, out = run(["task", "run", "repo-a", "feature-cross"])
            self.assertEqual(1, code)
            self.assertIn("did you mean: feature-cross-repo-a", out)

    def test_task_doctor_unknown_task_id_with_no_candidates_has_no_suggestion(self):
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
            code, out = run(["task", "doctor", "repo", "does-not-exist"])
            self.assertEqual(1, code)
            self.assertIn("INVALID", out)
            self.assertNotIn("did you mean", out)

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


class WorktreeCheckpointFlowTests(unittest.TestCase):
    """
    See plan/future/pre-flight-consent-and-bounded-autonomy.md layer 4: a
    succeeded run that kept a worktree (a real diff) still needs a review step
    before merging -- checkpoint_candidate synthesizes one, promote_checkpoint
    merges on --yes. Real git throughout (via _FakeWorktreeSucceedingRuntime),
    not mocked -- these are the same esc_exec.worktree calls
    ClaudeCodeAdapter.execute makes for real.
    """

    def _setup(self, temp: Path):
        root = Path(temp)
        db = root / "db.sqlite"
        registry = root / "registry.yaml"
        repository_dir = root / "repo-checkout"
        _make_gradle_repository(repository_dir)
        _git_init(repository_dir)
        store = Store(db)

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
        return root, store, registry, repository_dir

    def test_succeeded_run_with_no_diff_gets_no_changes_status_and_a_candidate(self):
        """
        plan/done/run-outcome-surfacing.md: a run that kept no worktree (no real
        diff) must not be indistinguishable from a real completed run -- it gets
        a distinct status and a synthesized, reviewable candidate instead of
        checkpoint_candidate raising "no checkpoint candidate found."
        """
        with TemporaryDirectory() as temp:
            root, store, registry, repository_dir = self._setup(temp)
            result = cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"},
                runtime=_FakeWorktreeSucceedingRuntime(root / "runs", repository_dir, leave_a_diff=False),
            )
            self.assertEqual("succeeded-no-changes", result["status"])
            candidate = cli.checkpoint_candidate(store, repository_dir, "feature-export")
            self.assertTrue(candidate["no_changes"])
            self.assertEqual("ready-to-resume", candidate["checkpoint"]["status"])
            rendered = cli.render_checkpoint_candidate(candidate, repository_dir)
            self.assertIn("produced no changes", rendered)
            outcome = cli.promote_checkpoint(repository_dir, "feature-export", candidate)
            self.assertIsNone(outcome)

    def test_succeeded_run_with_a_diff_produces_a_reviewable_candidate(self):
        with TemporaryDirectory() as temp:
            root, store, registry, repository_dir = self._setup(temp)
            result = cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"},
                runtime=_FakeWorktreeSucceedingRuntime(root / "runs", repository_dir, leave_a_diff=True),
            )
            self.assertEqual("succeeded", result["status"])
            candidate = cli.checkpoint_candidate(store, repository_dir, "feature-export")
            self.assertEqual("ready-to-resume", candidate["checkpoint"]["status"])
            self.assertTrue(candidate["worktree_merge_only"])
            rendered = cli.render_checkpoint_candidate(candidate, repository_dir)
            self.assertIn("Worktree diff:", rendered)
            self.assertIn("agent-output.txt", rendered)

    def test_promoting_a_worktree_only_candidate_merges_and_returns_none(self):
        with TemporaryDirectory() as temp:
            root, store, registry, repository_dir = self._setup(temp)
            cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"},
                runtime=_FakeWorktreeSucceedingRuntime(root / "runs", repository_dir, leave_a_diff=True),
            )
            candidate = cli.checkpoint_candidate(store, repository_dir, "feature-export")
            outcome = cli.promote_checkpoint(repository_dir, "feature-export", candidate)
            self.assertIsNone(outcome)
            self.assertTrue((repository_dir / "agent-output.txt").is_file())
            from esc_exec.worktree import worktree_path
            self.assertFalse(worktree_path(repository_dir, "feature-export").is_dir())
            # No durable checkpoint was written -- nothing to record, the merge
            # was the whole point.
            from esc_exec.checkpoints import checkpoint_path
            self.assertFalse(checkpoint_path(repository_dir, "feature-export").is_file())

    def test_promote_checkpoint_cli_reports_merge_not_a_checkpoint_path(self):
        with TemporaryDirectory() as temp:
            root, store, registry, repository_dir = self._setup(temp)
            db = root / "db.sqlite"
            cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"},
                runtime=_FakeWorktreeSucceedingRuntime(root / "runs", repository_dir, leave_a_diff=True),
            )
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.main([
                    "--db", str(db), "--registry", str(registry),
                    "task", "promote-checkpoint", "repo", "feature-export", "--yes",
                ])
            self.assertEqual(0, code)
            self.assertIn("Merged worktree back", buffer.getvalue())


class _FakeConsentRecordingRuntime:
    """Writes a `bindings.consent` shaped exactly like ClaudeCodeAdapter.execute's
    real one, without needing a real claude binary -- for testing the
    orchestrator-side prior_consent()/render_execution_preview wiring in
    isolation from the adapter that originates the data."""

    def __init__(self, output_root: Path, granted_categories: list[str]):
        self.output_root, self.granted_categories = output_root, granted_categories

    def execute(self, contracts):
        task_id = contracts["task"]["task"]["id"]
        path = self.output_root / f"{task_id}-consent-run"
        path.mkdir(parents=True, exist_ok=True)
        (path / "run.json").write_text(json.dumps({
            "bindings": {"consent": {"granted_categories": self.granted_categories, "granted_at": "2026-07-24T00:00:00Z"}},
        }), encoding="utf-8")
        return path


class PriorConsentTests(unittest.TestCase):
    def test_prior_consent_none_for_task_with_no_runs(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp) / "db.sqlite")
            self.assertIsNone(cli.prior_consent(store, "never-run"))

    def test_prior_consent_none_when_latest_run_has_no_consent_binding(self):
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

            cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"}, runtime=_FakeSucceedingRuntime(root / "runs"),
            )
            self.assertIsNone(cli.prior_consent(store, "feature-export"))

    def test_second_dispatch_shows_already_consented_after_first_recorded_it(self):
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

            categories = cli.granted_categories(cli.resolve_default_policy(registry))
            self.assertIsNone(cli.prior_consent(store, "feature-export"))
            cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"},
                runtime=_FakeConsentRecordingRuntime(root / "runs", categories),
            )
            recorded = cli.prior_consent(store, "feature-export")
            self.assertEqual(sorted(categories), sorted(recorded["granted_categories"]))

            task_document = cli.load_yaml(
                repository_dir / ".esc-ai" / "workflows" / "active" / "feature-export" / "task.yaml"
            )
            rendered = cli.render_execution_preview(
                "repo", "feature-export", task_document, None, cli.resolve_default_policy(registry), recorded,
            )
            self.assertIn("already consented on 2026-07-24T00:00:00Z", rendered)


def _onboard_and_plan_single_repository(db: Path, registry: Path, repository_dir: Path) -> None:
    """Shared bootstrap for the interactive-menu-completeness tests below: onboard
    one Gradle repository and plan+apply a single-repository task named
    `feature-export`, exactly as `test_execute_retry_promote_and_resume_view` does,
    so `feature-export`'s task.yaml exists and can be dispatched."""
    _make_gradle_repository(repository_dir)

    def run(argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
        return code, buffer.getvalue()

    run(["repository", "add", "repo", str(repository_dir)])
    run(["repository", "analyze", "repo", "--json"])
    answers_file = db.parent / "answers.json"
    answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
    run(["repository", "answer", "repo", str(answers_file)])
    run(["repository", "apply", "repo"])
    request_file = db.parent / "request.json"
    request_file.write_text(json.dumps({
        "work_type": "feature", "objective": "Add CSV export.", "repositories": ["repo"],
    }), encoding="utf-8")
    run(["plan", "draft", "feature-export", str(request_file)])
    plan_answers_file = db.parent / "plan-answers.json"
    plan_answers_file.write_text(json.dumps({
        "components": {"repo": ["content"]}, "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
    }), encoding="utf-8")
    run(["plan", "answer", "feature-export", str(plan_answers_file)])
    run(["plan", "apply", "feature-export"])


class RegisteredRepositoryIdsTests(unittest.TestCase):
    def test_empty_registry_has_no_ids(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            self.assertEqual([], cli.registered_repository_ids(registry))

    def test_returns_sorted_ids(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            (root / "z-repo").mkdir()
            (root / "a-repo").mkdir()
            cli.add_route(registry, "repositories", "z-repo", root / "z-repo")
            cli.add_route(registry, "repositories", "a-repo", root / "a-repo")
            self.assertEqual(["a-repo", "z-repo"], cli.registered_repository_ids(registry))


class ValidateSystemTests(unittest.TestCase):
    def test_no_registered_repositories_returns_empty(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            self.assertEqual({}, cli.validate_system(registry))
            self.assertEqual("No registered repositories to validate.", cli.render_system_validation({}))

    def test_unresolvable_repository_reports_as_a_string_not_a_crash(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            cli.add_route(registry, "repositories", "ghost", root / "does-not-exist")
            results = cli.validate_system(registry)
            self.assertIn("ghost", results)
            self.assertIsInstance(results["ghost"], str)
            rendered = cli.render_system_validation(results)
            self.assertIn("INVALID    ghost:", rendered)
            self.assertIn("0/1 repositories fully valid.", rendered)

    def test_real_onboarded_repository_validates_clean(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _onboard_and_plan_single_repository(db, registry, repository_dir)

            results = cli.validate_system(registry)
            self.assertIn("repo", results)
            self.assertTrue(all(result.state.value == "VALID" for result in results["repo"]))
            rendered = cli.render_system_validation(results)
            self.assertIn("1/1 repositories fully valid.", rendered)


class ConfigureSystemRenderingTests(unittest.TestCase):
    """Pure rendering, except render_repository_list which resolves routes against
    a real registry file -- kept in its own test method rather than RenderingTests'
    fake-data-only class."""

    def test_render_provider_status_none(self):
        self.assertEqual("No provider connected yet.", cli.render_provider_status(None))

    def test_render_provider_status_connected(self):
        rendered = cli.render_provider_status({"id": "claude", "route": "subscription"})
        self.assertEqual("Active provider: claude (subscription)", rendered)

    def test_render_policy_status_none_configured(self):
        rendered = cli.render_policy_status(None)
        self.assertIn("No default policy configured yet", rendered)
        self.assertIn(cli.DEFAULT_POLICY_PROFILE_ID, rendered)

    def test_render_policy_status_known_profile(self):
        rendered = cli.render_policy_status("readonly-review")
        self.assertIn("Default policy: `readonly-review`", rendered)
        self.assertIn(cli.POLICY_PROFILES["readonly-review"]["policy"]["description"], rendered)

    def test_render_policy_status_unknown_profile_falls_back(self):
        # A stale registry value from a version whose profile set no longer
        # matches -- surfaced honestly rather than silently swapped for the
        # fallback's own status text.
        rendered = cli.render_policy_status("some-removed-profile")
        self.assertIn("`some-removed-profile` is not a known profile", rendered)
        self.assertIn(cli.DEFAULT_POLICY_PROFILE_ID, rendered)

    def test_render_roadmap_none_set(self):
        self.assertEqual("No roadmap set yet for this repository.", cli.render_roadmap(None))

    def test_render_roadmap_shows_every_field(self):
        existing = {"project_roadmap": {
            "purpose": "A demo app.", "current_stage": "Scaffolding.", "direction": "Add auth next.",
            "durable_decisions": ["Use Kotlin Multiplatform."], "updated_at": "2026-08-05T00:00:00Z",
        }}
        rendered = cli.render_roadmap(existing)
        self.assertIn("Purpose: A demo app.", rendered)
        self.assertIn("Current stage: Scaffolding.", rendered)
        self.assertIn("Direction: Add auth next.", rendered)
        self.assertIn("Durable decisions: Use Kotlin Multiplatform.", rendered)
        self.assertIn("Updated: 2026-08-05T00:00:00Z", rendered)

    def test_render_roadmap_missing_fields_render_as_none(self):
        rendered = cli.render_roadmap({"project_roadmap": {}})
        self.assertIn("Purpose: (none)", rendered)
        self.assertIn("Durable decisions: (none)", rendered)

    def test_render_repository_list_empty(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            self.assertEqual("No repositories registered yet.", cli.render_repository_list([], registry))

    def test_render_repository_list_shows_resolved_path(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            (root / "repo").mkdir()
            cli.add_route(registry, "repositories", "repo", root / "repo")
            rendered = cli.render_repository_list(["repo"], registry)
            self.assertIn("repo ->", rendered)
            self.assertIn(str((root / "repo").resolve()), rendered)

    def test_render_repository_list_flags_unresolvable_route(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            cli.add_route(registry, "repositories", "ghost", root / "does-not-exist")
            rendered = cli.render_repository_list(["ghost"], registry)
            self.assertIn("ghost -> UNRESOLVABLE", rendered)


class ResolveDefaultPolicyTests(unittest.TestCase):
    """plan/done/configure-system-policy-profiles.md: an installation with
    nothing configured must behave exactly as this system always has."""

    def test_unset_falls_back_to_standard_autonomous(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            resolved = cli.resolve_default_policy(registry)
            self.assertEqual("standard-autonomous", resolved["policy"]["id"])
            self.assertEqual(cli.POLICY_PROFILES["standard-autonomous"], resolved)

    def test_configured_profile_is_resolved(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            cli.set_default_policy(registry, "readonly-review")
            resolved = cli.resolve_default_policy(registry)
            self.assertEqual("readonly-review", resolved["policy"]["id"])

    def test_stale_configured_profile_falls_back(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            cli.set_default_policy(registry, "some-removed-profile")
            resolved = cli.resolve_default_policy(registry)
            self.assertEqual("standard-autonomous", resolved["policy"]["id"])

    def test_result_is_a_fresh_copy_each_time(self):
        # POLICY_PROFILES is the canonical in-memory definition -- a caller
        # mutating what it gets back must never corrupt later resolutions.
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            first = cli.resolve_default_policy(registry)
            first["permissions"]["network"] = "deny"
            second = cli.resolve_default_policy(registry)
            self.assertEqual("allow", second["permissions"]["network"])


class ConfigurePolicyInteractiveTests(unittest.TestCase):
    def test_declining_leaves_registry_unset(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            responses = iter(["2"])  # decline
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                cli.configure_policy_interactive(registry)
            finally:
                builtins.input = original_input
            self.assertIsNone(cli.default_policy_id(registry))

    def test_selecting_a_profile_records_it(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            profile_ids = list(cli.POLICY_PROFILES)
            responses = iter(["1", str(profile_ids.index("readonly-review") + 1)])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                cli.configure_policy_interactive(registry)
            finally:
                builtins.input = original_input
            self.assertEqual("readonly-review", cli.default_policy_id(registry))

    def test_reachable_and_changeable_from_the_configure_submenu(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            profile_ids = list(cli.POLICY_PROFILES)
            responses = iter([
                "4",  # Show / select default policy
                "1",  # select a different one now
                str(profile_ids.index("readonly-review") + 1),
                "6",  # back
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                cli.run_configure_interactive(registry)
            finally:
                builtins.input = original_input
            self.assertEqual("readonly-review", cli.default_policy_id(registry))


class ConfigureRoadmapInteractiveTests(unittest.TestCase):
    """plan/done/project-vision-and-direction.md design 1: a direct, human-only
    path to project_roadmap, independent of the AI-mediated planning conversation
    that's the only way to reach it otherwise."""

    def test_no_repositories_registered_prints_message_and_returns(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                cli.configure_roadmap_interactive(registry)
            self.assertIn("No repositories registered yet.", buffer.getvalue())

    def test_declining_leaves_no_roadmap_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            responses = iter(["1", "2"])  # select repo, decline set/update
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                cli.configure_roadmap_interactive(registry)
            finally:
                builtins.input = original_input
            self.assertIsNone(load_project_roadmap(repository_dir))

    def test_setting_fresh_values_saves_them(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            responses = iter([
                "1",               # select repo
                "1",               # confirm set/update
                "A demo app.",     # purpose
                "Scaffolding.",    # current stage
                "Add auth next.",  # direction
                "Use KMP.",        # durable decisions
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                cli.configure_roadmap_interactive(registry)
            finally:
                builtins.input = original_input
            roadmap = load_project_roadmap(repository_dir)["project_roadmap"]
            self.assertEqual("A demo app.", roadmap["purpose"])
            self.assertEqual(["Use KMP."], roadmap["durable_decisions"])

    def test_blank_input_keeps_existing_values(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            save_project_roadmap(repository_dir, "repo", "Old purpose.", "Old stage.", "Old direction.", ["Old decision."])
            responses = iter([
                "1",  # select repo
                "1",  # confirm set/update
                "",   # blank purpose -> keeps old
                "New stage.",
                "",   # blank direction -> keeps old
                "",   # blank decisions -> keeps old
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                cli.configure_roadmap_interactive(registry)
            finally:
                builtins.input = original_input
            roadmap = load_project_roadmap(repository_dir)["project_roadmap"]
            self.assertEqual("Old purpose.", roadmap["purpose"])
            self.assertEqual("New stage.", roadmap["current_stage"])
            self.assertEqual("Old direction.", roadmap["direction"])
            self.assertEqual(["Old decision."], roadmap["durable_decisions"])

    def test_reachable_from_the_configure_submenu(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository_dir = root / "repo"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            responses = iter([
                "5",  # Show / set project roadmap
                "1",  # select repo
                "1",  # confirm set/update
                "A demo app.", "Scaffolding.", "Add auth next.", "",
                "6",  # back
            ])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                cli.run_configure_interactive(registry)
            finally:
                builtins.input = original_input
            roadmap = load_project_roadmap(repository_dir)["project_roadmap"]
            self.assertEqual("A demo app.", roadmap["purpose"])


class RunDetailTests(unittest.TestCase):
    def test_no_runs_yet(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp) / "db.sqlite")
            detail = cli.run_detail(store, "never-run")
            self.assertIsNone(detail["run"])
            self.assertEqual([], detail["events"])
            self.assertIsNone(detail["summary"])
            self.assertIsNone(detail["checkpoint"])
            self.assertEqual(
                "No runs yet for `repo/never-run`.",
                cli.render_run_detail("repo", "never-run", detail),
            )

    def test_succeeded_run_shows_status_and_events_no_checkpoint(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _onboard_and_plan_single_repository(db, registry, repository_dir)
            store = Store(db)

            cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"}, runtime=_FakeSucceedingRuntime(root / "runs"),
            )

            detail = cli.run_detail(store, "feature-export")
            self.assertEqual("succeeded", detail["run"]["status"])
            self.assertIsNone(detail["checkpoint"])
            event_types = [event["type"] for event in detail["events"]]
            self.assertEqual(["run.queued", "run.running", "run.succeeded"], event_types)

            rendered = cli.render_run_detail("repo", "feature-export", detail)
            self.assertIn("run.queued", rendered)
            self.assertIn("run.running", rendered)
            self.assertIn("run.succeeded", rendered)
            self.assertIn("succeeded", rendered)

    def test_failed_run_surfaces_the_checkpoint_candidate(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _onboard_and_plan_single_repository(db, registry, repository_dir)
            store = Store(db)

            cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"}, runtime=_FakeFailingRuntime(),
            )

            detail = cli.run_detail(store, "feature-export")
            self.assertEqual("failed", detail["run"]["status"])
            self.assertIsNotNone(detail["checkpoint"])

            rendered = cli.render_run_detail("repo", "feature-export", detail)
            self.assertIn("Checkpoint candidate from run", rendered)
            self.assertIn("provider unavailable", rendered)


class ConfigureSystemInteractiveTests(unittest.TestCase):
    def test_shows_no_provider_and_no_repositories_then_backs_out(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            responses = iter(["1", "3", "6"])  # show provider, list repos, back
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_configure_interactive(registry)
            finally:
                builtins.input = original_input
            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("No provider connected yet.", output)
            self.assertIn("No repositories registered yet.", output)

    def test_declining_to_connect_leaves_provider_unset(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            responses = iter(["2", "2", "6"])  # connect/switch -> decline -> back
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                cli.run_configure_interactive(registry)
            finally:
                builtins.input = original_input
            self.assertIsNone(cli.active_provider(registry))

    def test_can_connect_a_provider_from_the_submenu(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            # connect/switch -> confirm -> claude -> subscription -> back
            responses = iter(["2", "1", "1", "1", "6"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            original_available, original_status = cli.claude_cli_available, cli.claude_auth_status
            cli.claude_cli_available = lambda binary="claude": True
            cli.claude_auth_status = lambda binary="claude": {"loggedIn": True, "subscriptionType": "pro"}
            try:
                cli.run_configure_interactive(registry)
            finally:
                builtins.input = original_input
                cli.claude_cli_available, cli.claude_auth_status = original_available, original_status
            self.assertEqual({"id": "claude", "route": "subscription"}, cli.active_provider(registry))


class ObserveRunInteractiveTests(unittest.TestCase):
    def test_no_active_work_prints_the_empty_message(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.run_observe_interactive(store, registry)
            self.assertEqual(0, code)
            self.assertIn("No active work found", buffer.getvalue())

    def test_selecting_a_task_shows_its_run_detail(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _onboard_and_plan_single_repository(db, registry, repository_dir)
            store = Store(db)
            cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"}, runtime=_FakeSucceedingRuntime(root / "runs"),
            )

            original_input = builtins.input
            builtins.input = lambda prompt="": "1"  # the only active-work item
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_observe_interactive(store, registry)
            finally:
                builtins.input = original_input
            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("repo/feature-export", output)
            self.assertIn("succeeded", output)


class ResumeInteractiveDoctorAndObserveTests(unittest.TestCase):
    def test_execute_now_on_a_clean_task_prints_no_preflight_noise(self):
        """A task with no declared prerequisites (doctor_check returns no
        blockers) shouldn't get an extra "all clear" line on every single
        "Execute now" -- the check is silent unless it actually finds something,
        same as its own docstring's non-blocking framing."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _onboard_and_plan_single_repository(db, registry, repository_dir)
            store = Store(db)
            cli.set_provider(registry, "claude", "api-key")

            # select the task -> Execute now -> decline the actual dispatch.
            responses = iter(["1", "1", "2"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_resume_interactive(store, registry)
            finally:
                builtins.input = original_input
            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertNotIn("BLOCKED", output)
            self.assertIn("About to execute", output)
            self.assertIn("Cancelled -- nothing was executed.", output)

    def test_execute_now_surfaces_a_real_doctor_blocker_before_dispatch(self):
        """Mirrors test_task_doctor_reports_unsatisfied_prerequisite_without_
        dispatching, but through the guided "Resume active work -> Execute now"
        path instead of the standalone `task doctor` subcommand -- this is the
        exact gap plan/active/interactive-menu-completeness.md design 5 closes:
        the guided path used to go straight to the execution preview with no
        pre-flight warning at all."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _onboard_and_plan_single_repository(db, registry, repository_dir)
            store = Store(db)
            cli.set_provider(registry, "claude", "api-key")

            profile_path = repository_dir / ".esc-ai" / "components" / "content" / "esc-verification-profile.yaml"
            profile = load_yaml(profile_path)
            profile["gates"]["final"][0]["prerequisites"] = [
                {"kind": "env", "name": "ESC_AI_TEST_CLI_MISSING_TOKEN_XYZ"},
            ]
            write_yaml(profile_path, profile)

            # select the task -> Execute now -> decline the actual dispatch.
            responses = iter(["1", "1", "2"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_resume_interactive(store, registry)
            finally:
                builtins.input = original_input
            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("BLOCKED", output)
            self.assertIn("ESC_AI_TEST_CLI_MISSING_TOKEN_XYZ", output)
            self.assertIn("Cancelled -- nothing was executed.", output)

    def test_observe_latest_run_action_shows_run_detail(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _onboard_and_plan_single_repository(db, registry, repository_dir)
            store = Store(db)
            cli.execute_task(
                store, registry, "repo", repository_dir, "feature-export",
                {"id": "claude", "route": "api-key"}, runtime=_FakeSucceedingRuntime(root / "runs"),
            )

            # select the task -> Observe latest run.
            responses = iter(["1", "3"])
            original_input = builtins.input
            builtins.input = lambda prompt="": next(responses)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.run_resume_interactive(store, registry)
            finally:
                builtins.input = original_input
            self.assertEqual(0, code)
            output = buffer.getvalue()
            self.assertIn("run", output)
            self.assertIn("succeeded", output)


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

    def test_default_workspace_requests_worktree_isolation(self):
        # See plan/future/pre-flight-consent-and-bounded-autonomy.md layer 4 --
        # unlike default_policy's deliberately-conservative placeholder, worktree
        # isolation has no compatibility cost once the adapter supports it, so
        # this is the real default, not a stand-in for one.
        workspace = cli.default_workspace("ampm-backend")
        self.assertEqual("worktree", workspace["workspace"]["kind"])
        self.assertEqual("filesystem", workspace["workspace"]["isolation"])
        self.assertEqual("ampm-backend", workspace["workspace"]["repository"])

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


class PolicyDispatchTests(unittest.TestCase):
    """plan/done/configure-system-policy-profiles.md: escape-ai policy show/set,
    the non-interactive equivalent of "Configure system" -> "Show / select
    default policy" -- mandatory for CI/scripts per the same discipline every
    other guided flow in this CLI already follows."""

    def _run(self, db, registry, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
        return code, buffer.getvalue()

    def test_show_with_nothing_configured(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            code, out = self._run(db, registry, ["policy", "show"])
            self.assertEqual(0, code)
            self.assertIn("No default policy configured yet", out)

    def test_set_then_show_round_trips(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            code, out = self._run(db, registry, ["policy", "set", "readonly-review"])
            self.assertEqual(0, code)
            self.assertIn("SET        default policy to `readonly-review`", out)
            code, out = self._run(db, registry, ["policy", "show"])
            self.assertEqual(0, code)
            self.assertIn("Default policy: `readonly-review`", out)

    def test_set_rejects_an_unknown_profile_id(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            with self.assertRaises(SystemExit) as ctx:
                with redirect_stderr(io.StringIO()):
                    cli.main(["--db", str(db), "--registry", str(registry), "policy", "set", "made-up-profile"])
            self.assertEqual(2, ctx.exception.code)

    def test_task_run_preview_reflects_the_configured_default(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            self._run(db, registry, ["repository", "add", "repo", str(repository_dir)])
            self._run(db, registry, ["repository", "analyze", "repo", "--json"])
            answers_file = root / "answers.json"
            answers_file.write_text(json.dumps({"content": {"purpose": "Owns content."}}), encoding="utf-8")
            self._run(db, registry, ["repository", "answer", "repo", str(answers_file)])
            self._run(db, registry, ["repository", "apply", "repo"])
            request_file = root / "request.json"
            request_file.write_text(json.dumps({
                "work_type": "feature", "objective": "Add CSV export.", "repositories": ["repo"],
            }), encoding="utf-8")
            self._run(db, registry, ["plan", "draft", "feature-export", str(request_file)])
            plan_answers_file = root / "plan-answers.json"
            plan_answers_file.write_text(json.dumps({
                "components": {"repo": ["content"]}, "scope_boundary": "", "completion_conditions": ["done"], "rollout_needs": "",
            }), encoding="utf-8")
            self._run(db, registry, ["plan", "answer", "feature-export", str(plan_answers_file)])
            self._run(db, registry, ["plan", "apply", "feature-export"])

            self._run(db, registry, ["policy", "set", "readonly-review"])
            code, out = self._run(db, registry, ["task", "run", "repo", "feature-export"])
            self.assertEqual(0, code)
            self.assertIn("Policy: `readonly-review`", out)
            self.assertIn("Scope: this run will be granted -- read.", out)


class RoadmapDispatchTests(unittest.TestCase):
    """plan/done/project-vision-and-direction.md design 1: escape-ai roadmap
    show/set, the non-interactive equivalent of "Configure system" -> "Show / set
    project roadmap"."""

    def _run(self, db, registry, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["--db", str(db), "--registry", str(registry), *argv])
        return code, buffer.getvalue()

    def test_show_with_nothing_set(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            self._run(db, registry, ["repository", "add", "repo", str(repository_dir)])
            code, out = self._run(db, registry, ["roadmap", "show", "repo"])
            self.assertEqual(0, code)
            self.assertIn("No roadmap set yet for this repository.", out)

    def test_set_then_show_round_trips(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            self._run(db, registry, ["repository", "add", "repo", str(repository_dir)])
            answers_file = root / "roadmap-answers.json"
            answers_file.write_text(json.dumps({
                "purpose": "A demo app.", "current_stage": "Scaffolding.",
                "direction": "Add auth next.", "durable_decisions": ["Use KMP."],
            }), encoding="utf-8")
            code, out = self._run(db, registry, ["roadmap", "set", "repo", str(answers_file)])
            self.assertEqual(0, code)
            self.assertIn("SET        roadmap for `repo`", out)
            code, out = self._run(db, registry, ["roadmap", "show", "repo"])
            self.assertEqual(0, code)
            self.assertIn("Purpose: A demo app.", out)
            self.assertIn("Durable decisions: Use KMP.", out)

    def test_set_with_a_partial_answers_file_keeps_other_fields(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            self._run(db, registry, ["repository", "add", "repo", str(repository_dir)])
            full_answers = root / "full-answers.json"
            full_answers.write_text(json.dumps({
                "purpose": "A demo app.", "current_stage": "Scaffolding.",
                "direction": "Add auth next.", "durable_decisions": ["Use KMP."],
            }), encoding="utf-8")
            self._run(db, registry, ["roadmap", "set", "repo", str(full_answers)])

            partial_answers = root / "partial-answers.json"
            partial_answers.write_text(json.dumps({"current_stage": "Auth in progress."}), encoding="utf-8")
            self._run(db, registry, ["roadmap", "set", "repo", str(partial_answers)])

            code, out = self._run(db, registry, ["roadmap", "show", "repo"])
            self.assertEqual(0, code)
            self.assertIn("Purpose: A demo app.", out)
            self.assertIn("Current stage: Auth in progress.", out)
            self.assertIn("Direction: Add auth next.", out)

    def test_unresolvable_repository_is_invalid(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, registry = root / "db.sqlite", root / "registry.yaml"
            code, out = self._run(db, registry, ["roadmap", "show", "does-not-exist"])
            self.assertEqual(1, code)
            self.assertIn("INVALID", out)


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
