from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.registry import add_route
from esc_exec.yaml_io import write_yaml

from esc_orchestrator.initiative import analyze_task_impact
from esc_orchestrator.store import Store


def _task_document(task_id: str, repository: str, initiative: dict | None) -> dict:
    task = {"id": task_id, "title": "t", "objective": "o", "repository": repository, "status": "ready"}
    if initiative:
        task["initiative"] = initiative
    return {
        "schema_version": 1,
        "task": task,
        "scope": {"components": ["content"]},
        "completion_conditions": ["done"],
    }


def _contracts(task_id: str, repository: str, initiative: dict | None) -> dict:
    return {
        "task": _task_document(task_id, repository, initiative),
        "workspace": {
            "schema_version": 1,
            "workspace": {"id": "w", "kind": "local", "repository": repository, "isolation": "process"},
        },
        "adapter": {
            "schema_version": 1,
            "adapter": {"id": "a", "kind": "agent-runtime", "provider": "opencode", "capabilities": ["sessions"]},
        },
        "policy": {"schema_version": 1, "policy": {"id": "p", "description": "test"}, "permissions": {"read": "allow"}},
    }


class TaskImpactAnalysisTests(unittest.TestCase):
    """
    `_write_task_yaml` mirrors what `generate_multi_repository_workflow` writes to
    disk and `store.submit`/`update_run` mirror what `execute_task`/`Scheduler`
    actually do -- these tests exercise `analyze_task_impact` the way task 7's
    automatic-advancement hook will really call it, not through a synthetic shortcut.
    """

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = self.root / "registry.yaml"
        self.repositories: dict[str, Path] = {}
        self.store = Store(self.root / "db.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def _add_repository(self, repository_id: str) -> Path:
        path = self.root / repository_id
        path.mkdir()
        add_route(self.registry, "repositories", repository_id, path)
        self.repositories[repository_id] = path
        return path

    def _declare_task(self, task_id: str, repository_id: str, initiative: dict | None = None, complete: bool = False) -> None:
        if repository_id not in self.repositories:
            self._add_repository(repository_id)
        contracts = _contracts(task_id, repository_id, initiative)
        _, run_id = self.store.submit(contracts)
        if complete:
            self.store.update_run(run_id, "succeeded")
        task_dir = self.repositories[repository_id] / ".esc-ai" / "workflows" / "active" / task_id
        task_dir.mkdir(parents=True)
        write_yaml(task_dir / "task.yaml", _task_document(task_id, repository_id, initiative))

    def test_task_with_no_initiative_returns_empty_result(self):
        self._declare_task("task-a", "repo", complete=True)
        document = analyze_task_impact(self.store, self.registry, "task-a")
        self.assertIsNone(document["initiative_id"])
        self.assertEqual("repo/task-a", document["completed_task"])
        self.assertEqual([], document["newly_unblocked"])
        self.assertEqual({}, document["still_blocked"])

    def test_completed_task_counts_as_complete_even_if_its_own_task_yaml_is_undiscoverable(self):
        """
        Regression: completed_set used to be built purely from graph-discovered nodes,
        so if the just-completed task's own task.yaml wasn't part of the disk scan for
        any reason, its dependents would incorrectly stay "still blocked" on it forever
        despite the caller explicitly telling us it's done. The premise of this
        function -- "given a completed task" -- must hold regardless of disk state.
        """
        self._add_repository("repo")
        _, run_id = self.store.submit(_contracts("task-a", "repo", {"id": "feature-x"}))
        self.store.update_run(run_id, "succeeded")
        # Deliberately no task.yaml written for task-a -- only task-b's exists.
        self._declare_task("task-b", "repo", {"id": "feature-x", "depends_on": ["repo/task-a"]})
        document = analyze_task_impact(self.store, self.registry, "task-a")
        self.assertEqual(["repo/task-b"], document["newly_unblocked"])
        self.assertEqual({}, document["still_blocked"])

    def test_direct_dependent_becomes_unblocked(self):
        self._declare_task("task-a", "repo", {"id": "feature-x"}, complete=True)
        self._declare_task("task-b", "repo", {"id": "feature-x", "depends_on": ["repo/task-a"]})
        document = analyze_task_impact(self.store, self.registry, "task-a")
        self.assertEqual("feature-x", document["initiative_id"])
        self.assertEqual(["repo/task-b"], document["newly_unblocked"])
        self.assertEqual({}, document["still_blocked"])

    def test_task_still_blocked_reports_remaining_dependencies(self):
        self._declare_task("task-a", "repo", {"id": "feature-x"}, complete=True)
        self._declare_task("task-c", "repo", {"id": "feature-x"})
        self._declare_task("task-b", "repo", {"id": "feature-x", "depends_on": ["repo/task-a", "repo/task-c"]})
        document = analyze_task_impact(self.store, self.registry, "task-a")
        self.assertEqual([], document["newly_unblocked"])
        self.assertEqual({"repo/task-b": ["repo/task-c"]}, document["still_blocked"])

    def test_unrelated_task_in_same_initiative_not_reported(self):
        self._declare_task("task-a", "repo", {"id": "feature-x"}, complete=True)
        self._declare_task("task-d", "repo", {"id": "feature-x"})
        document = analyze_task_impact(self.store, self.registry, "task-a")
        self.assertEqual([], document["newly_unblocked"])
        self.assertEqual({}, document["still_blocked"])

    def test_already_completed_dependent_not_reported(self):
        self._declare_task("task-a", "repo", {"id": "feature-x"}, complete=True)
        self._declare_task("task-b", "repo", {"id": "feature-x", "depends_on": ["repo/task-a"]}, complete=True)
        document = analyze_task_impact(self.store, self.registry, "task-a")
        self.assertEqual([], document["newly_unblocked"])
        self.assertEqual({}, document["still_blocked"])

    def test_missing_task_raises(self):
        with self.assertRaisesRegex(ValueError, "no such task"):
            analyze_task_impact(self.store, self.registry, "does-not-exist")

    def test_cross_repository_dependent_becomes_unblocked(self):
        self._declare_task("task-a", "repo", {"id": "feature-x"}, complete=True)
        self._declare_task("task-b", "second", {"id": "feature-x", "depends_on": ["repo/task-a"]})
        document = analyze_task_impact(self.store, self.registry, "task-a")
        self.assertEqual(["second/task-b"], document["newly_unblocked"])

    def test_writes_output_when_given_a_path(self):
        self._declare_task("task-a", "repo", complete=True)
        output = self.root / "impact.json"
        analyze_task_impact(self.store, self.registry, "task-a", output=output)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
