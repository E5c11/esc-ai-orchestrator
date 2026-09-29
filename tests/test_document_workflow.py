"""BLA-44 slice 4: `document` -- the grounding_check stage, end to end through the real runtime and scheduler."""
import json
import unittest
from pathlib import Path

from esc_exec.worktree import ensure_worktree, finalize_worktree, worktree_branch
from esc_orchestrator import escape_ai_cli as cli
from esc_orchestrator.entrypoints.cli.render import render_execution_result
from esc_orchestrator.runtime import _AdapterRuntime
from esc_orchestrator.scheduler import Scheduler
from tests.test_orchestrator import contracts
from tests.test_read_only_workflows import _Case
from tests.test_verification_tree import _finish


class _WriteFiles:
    """An agent that writes `files` ({path: text}) -- into a disposable worktree (Claude-style) or straight into the
    live checkout (Codex/OpenCode-style)."""

    def __init__(self, repository: Path, files: dict[str, str], worktree: bool = True):
        self.repository, self.files, self.worktree = repository, files, worktree

    def execute(self, task_path, workspace_path, adapter_path, policy_path):
        root = ensure_worktree(self.repository, "task-1") if self.worktree else self.repository
        for name, text in self.files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        if not self.worktree:
            return _finish(self.repository, {})
        kept = finalize_worktree(self.repository, "task-1", "docs")
        return _finish(self.repository, {"worktree": {"branch": worktree_branch("task-1"), "kept": kept}})


class DocumentTests(_Case):
    def setUp(self):
        super().setUp()
        (self.repo / "docs").mkdir(exist_ok=True)
        (self.repo / "docs" / "guide.md").write_text("# Guide\n")
        self.init_git()

    def document(self, adapter):
        runtime = _AdapterRuntime()
        runtime.adapter, runtime.registry = adapter, self.registry
        scheduler = Scheduler(self.store, runtime, self.registry)
        task_contracts = contracts()
        task_contracts["task"]["scope"] = {"components": ["content"]}
        task_contracts["task"]["task"]["work_type"] = "document"
        task_contracts["workspace"]["workspace"]["kind"] = "worktree"
        try:
            _, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()
        finally:
            scheduler.close()
        return run_id, self.store.get_run(run_id)

    def blockers(self, run_id):
        return self.store.output_yaml(run_id, "checkpoint.yaml")["progress"]["blockers"]

    def test_documentation_that_points_at_real_files_passes_and_records_what_was_checked(self):
        docs = "# Export\n\nSee `content/Main.kt` and [the guide](guide.md); code lives under `content/`.\n"
        run_id, run = self.document(_WriteFiles(self.repo, {"docs/export.md": docs}))
        self.assertEqual("succeeded", run["status"], run.get("error"))
        record = json.loads((Path(run["output_path"]) / "grounding-check.json").read_text())
        self.assertEqual({"documents": ["docs/export.md"], "references_checked": 3, "blockers": []}, record)

    def test_a_reference_to_a_file_that_does_not_exist_fails_the_run_naming_document_and_line(self):
        docs = "# Export\n\nIt is implemented in `content/Excel.kt`.\n"
        run_id, run = self.document(_WriteFiles(self.repo, {"docs/export.md": docs}))
        self.assertEqual("failed", run["status"])
        self.assertEqual(["docs/export.md:3: `content/Excel.kt` does not exist in the repository"], self.blockers(run_id))
        self.assertIn("content/Excel.kt", run["error"])

    def test_changing_a_source_file_fails_the_run_even_if_the_documentation_is_fine(self):
        files = {"docs/export.md": "# Export\n", "content/Main.kt": "fun main() { changed() }\n"}
        run_id, run = self.document(_WriteFiles(self.repo, files))
        self.assertEqual("failed", run["status"])
        self.assertEqual(["a document run changed a file that is not documentation: content/Main.kt"], self.blockers(run_id))

    def test_documentation_may_cite_a_file_the_same_change_adds(self):
        files = {"docs/export.md": "See [the diagram](diagram.md) and `docs/diagram.md`.\n", "docs/diagram.md": "# Diagram\n"}
        run_id, run = self.document(_WriteFiles(self.repo, files))
        self.assertEqual("succeeded", run["status"], run.get("error"))

    def test_examples_in_code_blocks_and_ordinary_prose_are_not_treated_as_missing_files(self):
        docs = "Reads and writes via `read/write` over `TCP/IP`.\n\n```\ncat src/does/not/Exist.kt\n```\n"
        run_id, run = self.document(_WriteFiles(self.repo, {"docs/export.md": docs}))
        self.assertEqual("succeeded", run["status"], run.get("error"))

    def test_a_run_that_edited_the_live_checkout_is_checked_too(self):
        run_id, run = self.document(_WriteFiles(self.repo, {"docs/export.md": "See `content/Missing.kt`.\n"}, worktree=False))
        self.assertEqual("failed", run["status"])
        self.assertIn("content/Missing.kt", self.blockers(run_id)[0])

    def test_a_live_run_that_touches_code_fails(self):
        files = {"docs/export.md": "# Export\n", "content/Main.kt": "changed\n"}
        run_id, run = self.document(_WriteFiles(self.repo, files, worktree=False))
        self.assertEqual("failed", run["status"])
        self.assertIn("content/Main.kt", self.blockers(run_id)[0])

    def test_work_that_was_uncommitted_before_a_live_run_is_not_blamed_on_it(self):
        (self.repo / "content" / "Main.kt").write_text("fun main() { wip() }\n")
        run_id, run = self.document(_WriteFiles(self.repo, {"docs/export.md": "# Export\n"}, worktree=False))
        self.assertEqual("succeeded", run["status"], run.get("error"))

    def test_a_run_that_documents_nothing_is_reported_as_changing_nothing(self):
        run_id, run = self.document(_WriteFiles(self.repo, {}))
        self.assertEqual("succeeded-no-changes", run["status"])

    def test_verification_still_runs_after_the_grounding_check(self):
        from esc_exec.manifests import component_manifest_path
        from esc_exec.yaml_io import write_yaml
        write_yaml(component_manifest_path(self.repo, "content").parent / "esc-verification-profile.yaml", {
            "schema_version": 1, "profile": {"id": "content-verification", "component": "content"},
            "gates": {"focused": [], "component": [], "impact": [], "final": [{"id": "always-fails", "command": ["false"]}]},
        })
        run_id, run = self.document(_WriteFiles(self.repo, {"docs/export.md": "# Export\n"}))
        self.assertEqual("failed", run["status"])
        self.assertIn("verification failed: final.always-fails", run["error"])

    def test_the_report_says_what_was_checked_and_that_it_is_not_a_truth_check(self):
        report = render_execution_result({
            "run_id": "r", "attempt": 1, "status": "succeeded",
            "grounding": {"documents": ["docs/a.md", "docs/b.md"], "references_checked": 5, "blockers": []},
        })
        self.assertIn("Grounding: 2 documentation file(s), 5 file reference(s) checked -- all exist", report)
        self.assertIn("does not check that the prose is accurate", report)
        self.assertNotIn("Grounding:", render_execution_result({"run_id": "r", "attempt": 1, "status": "failed", "grounding": None}))

    def test_help_documents_inputs_behaviour_outputs_and_the_limit(self):
        text = next(a for a in cli.build_parser()._subparsers._group_actions).choices["document"].format_help()
        for expected in ("grounding_check gate", "only documentation may change", "Input:", "Output:", "EXIST, not that the prose", "read the diff"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
