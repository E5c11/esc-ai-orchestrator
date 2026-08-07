import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from esc_exec.dependencies import generate_dependency_graph
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import component_manifest_path, component_manifest_relative_path, repository_manifest_path
from esc_exec.registry import add_route
from esc_exec.task_context import generate_gradle_verification_profile
from esc_exec.yaml_io import load_yaml, write_yaml
from esc_orchestrator.api import server
from esc_orchestrator.runtime import _AdapterRuntime
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.store import Store


class FakeRuntime:
    def __init__(
        self, output_root: Path, verification_status: str | None = None, permission_denials: list | None = None,
        worktree_kept: bool | None = None,
    ):
        """
        `verification_status`, when set, writes a `verification-result.json` the
        way a real `_AdapterRuntime` (via `execute_verification_plan`) would --
        exercising Scheduler's independent-verification status wiring. Left unset
        by default so existing tests keep exercising the pre-task-5 "no
        verification-result.json produced" case (e.g. an adapter whose task has no
        verification profile at all), which must still resolve to "succeeded".

        `permission_denials`, when set, writes a `permission-denials.json` the
        way a real ClaudeCodeAdapter run would when the hard-deny list (or any
        other Claude Code permission check) blocked a tool call -- exercising
        layer 6's third checkpoint trigger (see
        plan/future/pre-flight-consent-and-bounded-autonomy.md).

        `worktree_kept`, when set, writes a real `bindings.worktree.kept` the way
        the worktree-isolated Claude Code adapter would (see
        plan/done/run-outcome-surfacing.md) -- exercising Scheduler's
        succeeded-no-changes wiring. Left unset by default so every existing test
        keeps exercising the "no worktree binding at all" case, which must still
        resolve to "succeeded" (see `_run_produced_changes`'s "unknown, not no
        change" default).
        """
        self.output_root = output_root
        self.verification_status = verification_status
        self.permission_denials = permission_denials
        self.worktree_kept = worktree_kept

    def execute(self, contracts):
        path = self.output_root / contracts["task"]["task"]["id"]
        path.mkdir(parents=True)
        if self.worktree_kept is not None:
            (path / "run.json").write_text(json.dumps({
                "bindings": {"worktree": {"branch": "esc-ai-task-task-1", "kept": self.worktree_kept}},
            }))
        else:
            (path / "run.json").write_text("{}")
        (path / "verification-summary.json").write_text(
            json.dumps({"schema_version": 1, "verification": {"status": "passed"}})
        )
        (path / "task-context.json").write_text(json.dumps({"schema_version": 1, "task": {"id": "task-1"}}))
        (path / "verification-plan.json").write_text(json.dumps({"schema_version": 1, "task_id": "task-1"}))
        (path / "run-metrics.json").write_text(json.dumps({"schema_version": 1, "execution": {"tool_calls": 2}}))
        if self.verification_status is not None:
            (path / "verification-result.json").write_text(json.dumps({
                "schema_version": 1,
                "task_id": "task-1",
                "generated_at": "2026-07-23T00:00:00Z",
                "status": self.verification_status,
                "gates": [{
                    "id": "final",
                    "outcome": "completed",
                    "checks": [{
                        "id": "test",
                        "command": ["true"],
                        "status": "passed" if self.verification_status == "passed" else "failed",
                        "exit_code": 0 if self.verification_status == "passed" else 1,
                        "duration_ms": 10,
                        "stdout_path": None,
                        "stderr_path": None,
                        "report_path": None,
                    }],
                }],
            }))
        if self.permission_denials is not None:
            (path / "permission-denials.json").write_text(json.dumps({
                "schema_version": 1, "task_id": "task-1", "generated_at": "2026-07-24T00:00:00Z",
                "denials": self.permission_denials,
            }))
        return path


class FailingRuntime:
    def execute(self, contracts):
        raise RuntimeError("provider unavailable")


def contracts():
    return {
        "task": {
            "schema_version": 1,
            "task": {
                "id": "task-1",
                "title": "Test",
                "objective": "Test scheduling",
                "repository": "repo",
                "status": "ready",
            },
            "scope": {"components": ["core"]},
            "completion_conditions": ["done"],
        },
        "workspace": {
            "schema_version": 1,
            "workspace": {
                "id": "w",
                "kind": "local",
                "repository": "repo",
                "isolation": "process",
            },
        },
        "adapter": {
            "schema_version": 1,
            "adapter": {
                "id": "a",
                "kind": "agent-runtime",
                "provider": "opencode",
                "capabilities": ["sessions"],
            },
        },
        "policy": {
            "schema_version": 1,
            "policy": {"id": "p", "description": "test"},
            "permissions": {"read": "allow"},
        },
    }


def _make_gradle_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle.kts").write_text(
        'rootProject.name = "repo"\ninclude(":content")\n', encoding="utf-8",
    )
    (root / "content/src/main/kotlin").mkdir(parents=True)
    (root / "content/build.gradle.kts").write_text("", encoding="utf-8")


class OrchestratorTests(unittest.TestCase):
    def test_repository_analyze_and_proposal_endpoints(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            add_route(registry, "repositories", "repo", repository_dir)
            httpd = server(None, store, "127.0.0.1", 0, registry)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                urlopen(Request(base + "/repositories/repo/proposal"))
                self.fail("expected HTTPError")
            except Exception as exc:
                self.assertEqual(404, exc.code)

            first = json.loads(urlopen(Request(base + "/repositories/repo/analyze", method="POST", data=b"")).read())
            self.assertEqual("repo", first["repository"]["id"])
            self.assertEqual("create", next(entry["action"] for entry in first["files"] if entry["path"] == ".esc-ai/esc-execution.yaml"))

            fetched = json.loads(urlopen(base + "/repositories/repo/proposal").read())
            self.assertEqual(first["input_digest"], fetched["input_digest"])

            second = json.loads(urlopen(Request(base + "/repositories/repo/analyze", method="POST", data=b"")).read())
            self.assertEqual(first["input_digest"], second["input_digest"])

            httpd.shutdown()
            httpd.server_close()
            thread.join()

    def test_repository_answers_endpoint(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            _make_gradle_repository(repository_dir)
            add_route(registry, "repositories", "repo", repository_dir)
            httpd = server(None, store, "127.0.0.1", 0, registry)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                answers_before_analyze = json.dumps({"content": {"purpose": "Owns content."}}).encode()
                try:
                    urlopen(Request(base + "/repositories/repo/answers", method="POST", data=answers_before_analyze))
                    self.fail("expected HTTPError")
                except Exception as exc:
                    self.assertEqual(404, exc.code)

                urlopen(Request(base + "/repositories/repo/analyze", method="POST", data=b""))

                answers = json.dumps({"content": {"purpose": "Owns content."}}).encode()
                result = json.loads(
                    urlopen(Request(base + "/repositories/repo/answers", method="POST", data=answers)).read()
                )
                self.assertIn(".esc-ai/esc-execution.yaml", result["written"])
                manifest_path = component_manifest_path(repository_dir, "content")
                self.assertTrue(manifest_path.is_file())
                self.assertIn("Owns content.", manifest_path.read_text(encoding="utf-8"))
                self.assertIn(".esc-ai/INSTRUCTIONS.md", result["workflow_inheritance"]["created"])
                self.assertTrue((repository_dir / ".esc-ai" / "INSTRUCTIONS.md").is_file())
                self.assertTrue((repository_dir / ".esc-ai" / "workflows" / "README.md").is_file())

                fetched = json.loads(urlopen(base + "/repositories/repo/answers").read())
                self.assertEqual(result["written"], fetched["written"])
                self.assertEqual(result["workflow_inheritance"], fetched["workflow_inheritance"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()

    def test_analyze_unregistered_repository_returns_404(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            httpd = server(None, store, "127.0.0.1", 0, registry)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                urlopen(Request(base + "/repositories/unknown/analyze", method="POST", data=b""))
                self.fail("expected HTTPError")
            except Exception as exc:
                self.assertEqual(404, exc.code)
            httpd.shutdown()
            httpd.server_close()
            thread.join()

    def test_failed_run_retains_checkpoint_candidate(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FailingRuntime(), registry)
            _, run_id = scheduler.submit(contracts())
            scheduler.queue.join()
            run = store.get_run(run_id)
            checkpoint = store.output_yaml(run_id, "checkpoint.yaml")
            self.assertEqual("failed", run["status"])
            self.assertEqual("blocked", checkpoint["checkpoint"]["status"])
            self.assertEqual(["provider unavailable"], checkpoint["progress"]["blockers"])
            scheduler.close()

    def test_scheduler_persists_lifecycle_and_events(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FakeRuntime(root / "runs"), registry)
            task_id, run_id = scheduler.submit(contracts())
            scheduler.queue.join()
            self.assertEqual("succeeded", store.get_task(task_id)["status"])
            self.assertEqual("succeeded", store.get_run(run_id)["status"])
            self.assertEqual(
                ["run.queued", "run.running", "run.succeeded"],
                [event["type"] for event in store.events(run_id)],
            )
            scheduler.close()

    def test_verification_result_passed_still_succeeds(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FakeRuntime(root / "runs", verification_status="passed"), registry)
            task_id, run_id = scheduler.submit(contracts())
            scheduler.queue.join()
            self.assertEqual("succeeded", store.get_run(run_id)["status"])
            self.assertIsNone(store.get_run(run_id)["error"])
            scheduler.close()

    def test_permission_denial_marks_run_waiting_approval_not_succeeded(self):
        """
        Layer 6: an adapter that reports done, with verification either absent
        or passed, but at least one permission-denied tool call, must not be
        recorded as a plain "succeeded" -- see
        plan/future/pre-flight-consent-and-bounded-autonomy.md.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            denials = [{"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}}]
            scheduler = Scheduler(store, FakeRuntime(root / "runs", permission_denials=denials), registry)
            task_id, run_id = scheduler.submit(contracts())
            scheduler.queue.join()
            run = store.get_run(run_id)
            self.assertEqual("waiting-approval", run["status"])
            self.assertIn("permission denied", run["error"])
            self.assertIn("Bash", run["error"])
            checkpoint_path = Path(run["output_path"]) / "checkpoint.yaml"
            self.assertTrue(checkpoint_path.is_file())
            scheduler.close()

    def test_permission_denial_does_not_auto_advance(self):
        """A permission-denial run must take the same "never auto-advance" path
        a failed run does -- only the plain "succeeded" branch calls
        self._advance."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            denials = [{"tool_name": "Read", "tool_input": {"file_path": ".env"}}]
            scheduler = Scheduler(store, FakeRuntime(root / "runs", permission_denials=denials), registry)
            with patch.object(Scheduler, "_advance") as advance:
                task_id, run_id = scheduler.submit(contracts())
                scheduler.queue.join()
            self.assertEqual("waiting-approval", store.get_run(run_id)["status"])
            advance.assert_not_called()
            scheduler.close()

    def test_permission_denial_with_no_denials_key_still_succeeds(self):
        """An empty denials list (the normal case -- every real run gets this
        artifact, most have nothing in it) must not be treated as a denial."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FakeRuntime(root / "runs", permission_denials=[]), registry)
            task_id, run_id = scheduler.submit(contracts())
            scheduler.queue.join()
            self.assertEqual("succeeded", store.get_run(run_id)["status"])
            scheduler.close()

    def _write_task_yaml(
        self, repository_dir: Path, task_id: str, initiative: dict, components: list[str] | None = None,
    ) -> None:
        task_dir = repository_dir / ".esc-ai" / "workflows" / "active" / task_id
        task_dir.mkdir(parents=True)
        write_yaml(task_dir / "task.yaml", {
            "schema_version": 1,
            "task": {
                "id": task_id, "title": task_id, "objective": task_id, "repository": "repo", "status": "ready",
                "initiative": initiative,
            },
            "scope": {"components": components or ["core"]},
            "completion_conditions": ["done"],
        })

    def test_advances_newly_unblocked_task_automatically(self):
        """
        Task 7: task-b's task.yaml declares depends_on ["repo/task-1"] under the same
        initiative -- once task-1 (task-b's dependency) completes clean, Scheduler must
        submit task-b itself, with no second explicit submit() call from the test.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            self._write_task_yaml(repository_dir, "task-b", {"id": "feature-x", "depends_on": ["repo/task-1"]})

            scheduler = Scheduler(store, FakeRuntime(root / "runs"), registry)
            task_a_contracts = contracts()
            task_a_contracts["task"]["task"]["initiative"] = {"id": "feature-x"}
            scheduler.submit(task_a_contracts)
            scheduler.queue.join()

            self.assertEqual("succeeded", store.get_task("task-1")["status"])
            self.assertEqual("succeeded", store.get_task("task-b")["status"])
            scheduler.close()

    def test_no_op_run_gets_succeeded_no_changes_status(self):
        """
        plan/done/run-outcome-surfacing.md finding #9: a run whose worktree was
        never kept (no real diff) must not be stamped "succeeded" -- that status
        means real completed work everywhere else in this codebase.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FakeRuntime(root / "runs", worktree_kept=False), registry)
            task_id, run_id = scheduler.submit(contracts())
            scheduler.queue.join()

            self.assertEqual("succeeded-no-changes", store.get_run(run_id)["status"])
            self.assertEqual("succeeded-no-changes", store.get_task(task_id)["status"])
            scheduler.close()

    def test_kept_worktree_still_gets_ordinary_succeeded_status(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FakeRuntime(root / "runs", worktree_kept=True), registry)
            task_id, run_id = scheduler.submit(contracts())
            scheduler.queue.join()

            self.assertEqual("succeeded", store.get_run(run_id)["status"])
            scheduler.close()

    def test_no_op_run_does_not_auto_advance_dependent_task(self):
        """
        The correctness half of finding #9: a no-op run must never unblock a
        dependent task whose depends_on assumed real work had landed.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            self._write_task_yaml(repository_dir, "task-b", {"id": "feature-x", "depends_on": ["repo/task-1"]})

            scheduler = Scheduler(store, FakeRuntime(root / "runs", worktree_kept=False), registry)
            task_a_contracts = contracts()
            task_a_contracts["task"]["task"]["initiative"] = {"id": "feature-x"}
            scheduler.submit(task_a_contracts)
            scheduler.queue.join()

            self.assertEqual("succeeded-no-changes", store.get_task("task-1")["status"])
            self.assertIsNone(store.get_task("task-b"))  # never submitted
            scheduler.close()

    def test_does_not_resubmit_a_task_with_existing_store_history(self):
        """
        task-b was already run once before task-1 (its declared dependency) ever
        completed -- execute_task doesn't enforce depends_on, so this is legitimate,
        out-of-order human action, not a bug. Task 7 must never silently resubmit a
        task any prior history already exists for, even once it becomes "unblocked."
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            self._write_task_yaml(repository_dir, "task-b", {"id": "feature-x", "depends_on": ["repo/task-1"]})

            scheduler = Scheduler(store, FailingRuntime(), registry)
            task_b_contracts = contracts()
            task_b_contracts["task"]["task"]["id"] = "task-b"
            scheduler.submit(task_b_contracts)
            scheduler.queue.join()
            scheduler.close()
            first_run = store.get_latest_run_for_task("task-b")
            self.assertEqual("failed", first_run["status"])

            scheduler = Scheduler(store, FakeRuntime(root / "runs"), registry)
            task_a_contracts = contracts()
            task_a_contracts["task"]["task"]["initiative"] = {"id": "feature-x"}
            scheduler.submit(task_a_contracts)
            scheduler.queue.join()

            self.assertEqual("succeeded", store.get_task("task-1")["status"])
            self.assertEqual(first_run["id"], store.get_latest_run_for_task("task-b")["id"])
            self.assertEqual("failed", store.get_task("task-b")["status"])
            scheduler.close()

    def test_submit_if_new_is_race_safe_under_concurrent_workers(self):
        """
        Task 8: Store's existing threading.Lock has never actually been exercised by
        more than one thread. Twenty threads race to submit_if_new the *same*
        task_id concurrently -- exactly one may win (get a real task_id/run_id back);
        the rest must see the row the winner already created and no-op, never a
        second row for the same task_id.
        """
        with TemporaryDirectory() as temp:
            store = Store(Path(temp) / "db.sqlite")
            results: list[tuple[str, str] | None] = []
            results_lock = threading.Lock()

            def attempt():
                result = store.submit_if_new(contracts())
                with results_lock:
                    results.append(result)

            threads = [threading.Thread(target=attempt) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            successes = [result for result in results if result is not None]
            self.assertEqual(1, len(successes))
            self.assertEqual(20, len(results))
            row_count = store.connection.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE task_id=?", ("task-1",)
            ).fetchone()["n"]
            self.assertEqual(1, row_count)

    def test_submit_if_new_is_race_safe_across_processes(self):
        """
        Parallel task dispatch (plan/done/headless-backdoor-mode.md's follow-on):
        dispatching several ready tasks via parallel subagents means separate OS
        processes racing to submit_if_new, not just threads within one Scheduler.
        The thread-level test above doesn't cover this -- Store's threading.RLock
        only ever serializes *this process*. Confirmed as a real bug empirically
        (a standalone repro script, 20 attempts x 8 real subprocesses): with the
        plain `with self.lock, self.connection:` pattern, SQLite's default deferred
        transaction lets a bare SELECT run lock-free, so two separate processes
        could both see "no row" and race to INSERT -- sqlite3.IntegrityError on the
        loser, not a clean None. Fixed with an explicit `BEGIN IMMEDIATE` inside
        submit_if_new, which takes SQLite's write lock before the SELECT runs.
        This spawns real subprocesses (not threads) specifically to exercise that
        fix, not just re-check the already-covered thread-level path.
        """
        with TemporaryDirectory() as temp:
            db_path = Path(temp) / "db.sqlite"
            repository_root = Path(__file__).resolve().parent.parent
            racer_script = Path(temp) / "racer.py"
            racer_script.write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(repository_root)!r})\n"
                "from pathlib import Path\n"
                "from esc_orchestrator.store import Store\n"
                f"store = Store(Path({str(db_path)!r}))\n"
                "result = store.submit_if_new({\n"
                "    'task': {'schema_version': 1, 'task': {'id': 'shared-task', 'title': 't', 'objective': 'o', 'repository': 'repo', 'status': 'ready'}, 'scope': {'components': ['core']}, 'completion_conditions': ['done']},\n"
                "    'workspace': {'schema_version': 1, 'workspace': {'id': 'w', 'kind': 'local', 'repository': 'repo', 'isolation': 'process'}},\n"
                "    'adapter': {'schema_version': 1, 'adapter': {'id': 'a', 'kind': 'agent-runtime', 'provider': 'opencode', 'capabilities': ['sessions']}},\n"
                "    'policy': {'schema_version': 1, 'policy': {'id': 'p', 'description': 'test'}, 'permissions': {'read': 'allow'}},\n"
                "})\n"
                "print('WON' if result is not None else 'LOST')\n"
            )
            for attempt in range(5):
                processes = [
                    subprocess.Popen(
                        [sys.executable, str(racer_script)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    )
                    for _ in range(6)
                ]
                outputs = [process.communicate(timeout=30)[0] for process in processes]
                wins = sum(1 for output in outputs if "WON" in output)
                self.assertEqual(1, wins, f"attempt {attempt}: outputs={outputs}")
                db_path.unlink()

    def test_worker_pool_processes_many_tasks_concurrently_without_corruption(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FakeRuntime(root / "runs"), registry, workers=5)
            task_ids = []
            for index in range(20):
                task_contracts = contracts()
                task_contracts["task"]["task"]["id"] = f"task-{index}"
                task_id, _ = scheduler.submit(task_contracts)
                task_ids.append(task_id)
            scheduler.queue.join()
            for task_id in task_ids:
                self.assertEqual("succeeded", store.get_task(task_id)["status"])
            scheduler.close()

    def test_diamond_dependency_advancement_never_double_submits_shared_downstream_task(self):
        """
        Task 8: task-d depends on both task-a and task-b. Under a real multi-worker
        pool, task-a and task-b can finish on two different threads at roughly the
        same moment, and each independently discovers task-d as newly unblocked -- a
        genuine check-then-act race the single-worker model never had a chance to
        hit. A `threading.Barrier` forces that overlap deterministically rather than
        hoping for it. `submit_if_new`'s atomic check-and-insert must ensure task-d
        is submitted -- and therefore runs -- exactly once.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            # generate_multi_repository_workflow writes every declared task's
            # task.yaml upfront, root tasks included -- task-a/task-b need their own
            # on-disk declarations too, or task-b's completion analysis can't see
            # task-a already succeeded (and vice versa) once neither is the specific
            # task an analyze_task_impact call is itself about.
            self._write_task_yaml(repository_dir, "task-a", {"id": "feature-x"})
            self._write_task_yaml(repository_dir, "task-b", {"id": "feature-x"})
            self._write_task_yaml(
                repository_dir, "task-d", {"id": "feature-x", "depends_on": ["repo/task-a", "repo/task-b"]},
            )

            barrier = threading.Barrier(2)

            class RendezvousRuntime:
                def __init__(self, output_root: Path):
                    self.output_root = output_root

                def execute(self, task_contracts):
                    task_id = task_contracts["task"]["task"]["id"]
                    if task_id in ("task-a", "task-b"):
                        barrier.wait(timeout=5)
                    path = self.output_root / task_id
                    path.mkdir(parents=True)
                    return path

            scheduler = Scheduler(store, RendezvousRuntime(root / "runs"), registry, workers=5)
            task_a = contracts()
            task_a["task"]["task"]["id"] = "task-a"
            task_a["task"]["task"]["initiative"] = {"id": "feature-x"}
            task_b = contracts()
            task_b["task"]["task"]["id"] = "task-b"
            task_b["task"]["task"]["initiative"] = {"id": "feature-x"}
            scheduler.submit(task_a)
            scheduler.submit(task_b)
            scheduler.queue.join()

            self.assertEqual("succeeded", store.get_task("task-a")["status"])
            self.assertEqual("succeeded", store.get_task("task-b")["status"])
            self.assertEqual("succeeded", store.get_task("task-d")["status"])
            row_count = store.connection.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE task_id=?", ("task-d",)
            ).fetchone()["n"]
            self.assertEqual(1, row_count)
            scheduler.close()

    def test_verification_result_failed_marks_run_failed_not_agent_self_report(self):
        """
        The runtime itself never raised (the agent's own self-report was "done") --
        only the independently-executed verification-result says otherwise. The run
        must still land as "failed", per plan/active/task-orchestration-and-
        verification-loop.md task 5: the verified artifact is authoritative, not
        whatever the agent's own final message claimed. Task 6: this must also route
        into the same checkpoint mechanism an uncaught exception uses, since a
        not-clean verified result is exactly the kind of blocker a human needs to
        review before the task can be considered resolved.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FakeRuntime(root / "runs", verification_status="failed"), registry)
            task_id, run_id = scheduler.submit(contracts())
            scheduler.queue.join()
            run = store.get_run(run_id)
            self.assertEqual("failed", store.get_task(task_id)["status"])
            self.assertEqual("failed", run["status"])
            self.assertIn("final.test", run["error"])
            self.assertEqual(
                ["run.queued", "run.running", "run.failed"],
                [event["type"] for event in store.events(run_id)],
            )
            checkpoint = store.output_yaml(run_id, "checkpoint.yaml")
            self.assertEqual("blocked", checkpoint["checkpoint"]["status"])
            self.assertEqual(run_id, checkpoint["checkpoint"]["run_id"])
            self.assertEqual(1, len(checkpoint["progress"]["blockers"]))
            self.assertIn("final.test", checkpoint["progress"]["blockers"][0])
            scheduler.close()

    def test_adapter_runtime_executes_verification_plan_independently(self):
        """
        `_AdapterRuntime.execute` -- the thing every real (non-test-double) runtime
        actually uses -- must call `execute_verification_plan` itself, producing
        `verification-result.json` in the adapter's own run_dir, regardless of what
        the adapter reports. Exercises the real `build_verification_plan` ->
        `execute_verification_plan` pipeline end to end (fake adapter, real
        onboarded repository), unlike the Scheduler tests above which fake the
        whole `Runtime` and never reach this code.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            repository_dir = root / "repo-checkout"
            (repository_dir / "content").mkdir(parents=True)
            write_yaml(repository_manifest_path(repository_dir), {
                "schema_version": 1,
                "repository": {"id": "repo", "type": "gradle-multi-project", "purpose": "test"},
                "components": [{"id": "content", "manifest": component_manifest_relative_path("content")}],
            })
            write_yaml(component_manifest_path(repository_dir, "content"), {
                "schema_version": 1,
                "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "Owns content"},
                "build": {"system": "gradle", "project": ":content"},
                "paths": {"source": "src/main", "tests": "src/test"},
            })
            generate_indexes(repository_dir)
            generate_dependency_graph(repository_dir)
            generate_gradle_verification_profile(repository_dir, "content")
            generate_indexes(repository_dir)
            generate_dependency_graph(repository_dir)

            registry = root / "registry.yaml"
            add_route(registry, "repositories", "repo", repository_dir)

            class FakeAdapter:
                def execute(self, task_path, workspace_path, adapter_path, policy_path):
                    run_dir = repository_dir / ".esc-ai" / "runs" / "run-fake"
                    run_dir.mkdir(parents=True)
                    return run_dir

            runtime = _AdapterRuntime()
            runtime.adapter = FakeAdapter()
            runtime.registry = registry

            run_dir = runtime.execute({
                "task": {
                    "schema_version": 1,
                    "task": {"id": "task-1", "title": "t", "objective": "o", "repository": "repo", "status": "ready"},
                    "scope": {"components": ["content"]},
                    "completion_conditions": ["done"],
                },
                "workspace": {
                    "schema_version": 1,
                    "workspace": {"id": "w", "kind": "local", "repository": "repo", "isolation": "process"},
                },
                "adapter": {
                    "schema_version": 1,
                    "adapter": {"id": "a", "kind": "agent-runtime", "provider": "opencode", "capabilities": ["sessions"]},
                },
                "policy": {
                    "schema_version": 1,
                    "policy": {"id": "p", "description": "test"},
                    "permissions": {"read": "allow"},
                },
            })

            self.assertTrue((run_dir / "verification-plan.json").is_file())
            result_path = run_dir / "verification-result.json"
            self.assertTrue(result_path.is_file())
            result = json.loads(result_path.read_text(encoding="utf-8"))
            # component gate's `./gradlew` doesn't exist in this fixture -- a real
            # missing-executable, not a mock, deterministically surfaces as "error"
            # and stops the plan there. The point of this test is that escape-ai's
            # own code ran the command at all, not that it happened to pass.
            self.assertEqual("failed", result["status"])

    def _register_architecture_framework(self, root: Path, registry: Path, documents: list[dict]) -> None:
        framework_root = root / "architecture-framework"
        framework_root.mkdir()
        (framework_root / "index.json").write_text(
            json.dumps({"generated": "2026-01-01T00:00:00Z", "count": len(documents), "documents": documents}),
            encoding="utf-8",
        )
        add_route(registry, "frameworks", "esc-ai-architecture-framework", framework_root)

    def _repository_with_component(
        self, repository_dir: Path, component_id: str, profile_ids: list[str] | None = None,
        passing_verification: bool = False,
    ) -> None:
        (repository_dir / component_id).mkdir(parents=True, exist_ok=True)
        manifest_path = repository_manifest_path(repository_dir)
        existing = {"components": []}
        if manifest_path.is_file():
            existing = load_yaml(manifest_path)
        existing.setdefault("schema_version", 1)
        existing.setdefault("repository", {"id": "repo", "type": "gradle-multi-project", "purpose": "test"})
        existing["components"] = [
            entry for entry in existing["components"] if entry["id"] != component_id
        ] + [{"id": component_id, "manifest": component_manifest_relative_path(component_id)}]
        write_yaml(manifest_path, existing)
        manifest = {
            "schema_version": 1,
            "component": {
                "id": component_id, "type": "gradle-module", "path": component_id, "purpose": f"Owns {component_id}",
            },
            "build": {"system": "gradle", "project": f":{component_id}"},
            "paths": {"source": "src/main", "tests": "src/test"},
        }
        if profile_ids:
            manifest["architecture"] = {"profile_ids": profile_ids}
        if passing_verification:
            # A real, always-exit-0 command (`true`) instead of
            # generate_gradle_verification_profile's `./gradlew`, which doesn't
            # exist in this sandbox and would fail regardless of the coverage gate
            # -- this component needs to genuinely succeed end to end.
            manifest["paths"]["verification_profile"] = "esc-verification-profile.yaml"
            write_yaml(component_manifest_path(repository_dir, component_id).parent / "esc-verification-profile.yaml", {
                "schema_version": 1,
                "profile": {"id": f"{component_id}-verification", "component": component_id},
                "gates": {
                    "focused": [], "component": [], "impact": [],
                    "final": [{"id": "smoke", "command": ["true"]}],
                },
            })
        write_yaml(component_manifest_path(repository_dir, component_id), manifest)
        generate_indexes(repository_dir)
        generate_dependency_graph(repository_dir)

    def test_coverage_gate_allows_clean_architecture_coverage(self):
        """
        Task 1 of plan/active/headless-backdoor-mode.md: a component whose declared
        architecture.profile_ids all resolve to a real, `active` document must proceed
        normally -- the gate only blocks incomplete coverage, never complete coverage.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            self._repository_with_component(repository_dir, "content", profile_ids=["ORCH-BE-FEAT"])
            generate_gradle_verification_profile(repository_dir, "content")
            generate_indexes(repository_dir)
            generate_dependency_graph(repository_dir)
            add_route(registry, "repositories", "repo", repository_dir)
            self._register_architecture_framework(root, registry, [
                {"id": "ORCH-BE-FEAT", "path": "feature-orchestrators/backend/feature.md",
                 "layer": "feature-orchestrators", "requires": [], "status": "active"},
            ])

            class RecordingAdapter:
                def __init__(self):
                    self.called = False

                def execute(self, task_path, workspace_path, adapter_path, policy_path):
                    self.called = True
                    run_dir = repository_dir / ".esc-ai" / "runs" / "run-clean"
                    run_dir.mkdir(parents=True)
                    return run_dir

            runtime = _AdapterRuntime()
            adapter = RecordingAdapter()
            runtime.adapter = adapter
            runtime.registry = registry
            scheduler = Scheduler(store, runtime, registry)
            task_contracts = contracts()
            task_contracts["task"]["scope"] = {"components": ["content"]}
            task_id, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()

            self.assertTrue(adapter.called)
            scheduler.close()

    def test_coverage_gate_blocks_missing_architecture_doc(self):
        """
        A component declaring a profile_id with no matching document at all must
        stop the run before the adapter is ever invoked, and checkpoint with a
        blocker naming the missing doc.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            self._repository_with_component(repository_dir, "content", profile_ids=["ORCH-BE-FEAT"])
            add_route(registry, "repositories", "repo", repository_dir)
            self._register_architecture_framework(root, registry, [])

            class RecordingAdapter:
                def __init__(self):
                    self.called = False

                def execute(self, task_path, workspace_path, adapter_path, policy_path):
                    self.called = True
                    run_dir = repository_dir / ".esc-ai" / "runs" / "run-should-not-happen"
                    run_dir.mkdir(parents=True)
                    return run_dir

            runtime = _AdapterRuntime()
            adapter = RecordingAdapter()
            runtime.adapter = adapter
            runtime.registry = registry
            scheduler = Scheduler(store, runtime, registry)
            task_contracts = contracts()
            task_contracts["task"]["scope"] = {"components": ["content"]}
            task_id, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()

            self.assertFalse(adapter.called)
            run = store.get_run(run_id)
            self.assertEqual("failed", run["status"])
            checkpoint = store.output_yaml(run_id, "checkpoint.yaml")
            self.assertEqual("blocked", checkpoint["checkpoint"]["status"])
            self.assertEqual(1, len(checkpoint["progress"]["blockers"]))
            self.assertIn("ORCH-BE-FEAT", checkpoint["progress"]["blockers"][0])
            self.assertIn("does not exist", checkpoint["progress"]["blockers"][0])
            scheduler.close()

    def test_coverage_gate_blocks_stub_architecture_doc(self):
        """
        A component declaring a profile_id that resolves but is still `status: stub`
        (not yet promoted `active`) must stop the run the same way a missing doc
        does -- `stub` means "not yet selectable," not "good enough."
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            self._repository_with_component(repository_dir, "content", profile_ids=["ORCH-BE-FEAT"])
            add_route(registry, "repositories", "repo", repository_dir)
            self._register_architecture_framework(root, registry, [
                {"id": "ORCH-BE-FEAT", "path": "feature-orchestrators/backend/feature.md",
                 "layer": "feature-orchestrators", "requires": [], "status": "stub"},
            ])

            class RecordingAdapter:
                def __init__(self):
                    self.called = False

                def execute(self, task_path, workspace_path, adapter_path, policy_path):
                    self.called = True
                    run_dir = repository_dir / ".esc-ai" / "runs" / "run-should-not-happen"
                    run_dir.mkdir(parents=True)
                    return run_dir

            runtime = _AdapterRuntime()
            adapter = RecordingAdapter()
            runtime.adapter = adapter
            runtime.registry = registry
            scheduler = Scheduler(store, runtime, registry)
            task_contracts = contracts()
            task_contracts["task"]["scope"] = {"components": ["content"]}
            task_id, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()

            self.assertFalse(adapter.called)
            run = store.get_run(run_id)
            self.assertEqual("failed", run["status"])
            checkpoint = store.output_yaml(run_id, "checkpoint.yaml")
            self.assertEqual(1, len(checkpoint["progress"]["blockers"]))
            self.assertIn("ORCH-BE-FEAT", checkpoint["progress"]["blockers"][0])
            self.assertIn("stub", checkpoint["progress"]["blockers"][0])
            scheduler.close()

    def test_coverage_gate_also_applies_to_auto_advanced_tasks(self):
        """
        The gate lives in _AdapterRuntime.execute, which every task goes through
        regardless of how it was submitted -- an auto-advanced dependent (task 7)
        must be gated exactly like a directly-submitted task, not just the first,
        human/script-submitted task in an initiative.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            self._repository_with_component(repository_dir, "content", passing_verification=True)
            self._repository_with_component(repository_dir, "api", profile_ids=["ORCH-BE-FEAT"])
            add_route(registry, "repositories", "repo", repository_dir)
            self._register_architecture_framework(root, registry, [])
            self._write_task_yaml(
                repository_dir, "task-b", {"id": "feature-x", "depends_on": ["repo/task-1"]}, components=["api"],
            )

            class RecordingAdapter:
                def __init__(self):
                    self.calls = []

                def execute(self, task_path, workspace_path, adapter_path, policy_path):
                    task_id = load_yaml(task_path)["task"]["id"]
                    self.calls.append(task_id)
                    run_dir = repository_dir / ".esc-ai" / "runs" / f"run-{task_id}"
                    run_dir.mkdir(parents=True)
                    return run_dir

            runtime = _AdapterRuntime()
            adapter = RecordingAdapter()
            runtime.adapter = adapter
            runtime.registry = registry
            scheduler = Scheduler(store, runtime, registry)
            task_contracts = contracts()
            task_contracts["task"]["scope"] = {"components": ["content"]}
            task_contracts["task"]["task"]["initiative"] = {"id": "feature-x"}
            scheduler.submit(task_contracts)
            scheduler.queue.join()

            # task-1 (clean, no architecture.profile_ids) ran; task-b (auto-advanced,
            # missing doc) never reached the adapter at all.
            self.assertEqual(["task-1"], adapter.calls)
            self.assertEqual("succeeded", store.get_task("task-1")["status"])
            self.assertEqual("failed", store.get_task("task-b")["status"])
            checkpoint = store.output_yaml(store.get_latest_run_for_task("task-b")["id"], "checkpoint.yaml")
            self.assertIn("ORCH-BE-FEAT", checkpoint["progress"]["blockers"][0])
            scheduler.close()

    def test_environment_prerequisite_gate_blocks_unsatisfied_prerequisite(self):
        """
        plan/active/pre-flight-doctor-and-gate-prerequisites.md: a verification
        gate check declaring an unsatisfied `prerequisites` entry must stop the run
        before the adapter is ever invoked -- same shape as the architecture
        coverage gate above, just a different pre-dispatch check.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            self._repository_with_component(repository_dir, "content")
            write_yaml(component_manifest_path(repository_dir, "content").parent / "esc-verification-profile.yaml", {
                "schema_version": 1,
                "profile": {"id": "content-verification", "component": "content"},
                "gates": {
                    "focused": [], "component": [], "impact": [],
                    "final": [{
                        "id": "smoke", "command": ["true"],
                        "prerequisites": [{"kind": "env", "name": "ESC_AI_TEST_MISSING_TOKEN_XYZ"}],
                    }],
                },
            })
            manifest = load_yaml(component_manifest_path(repository_dir, "content"))
            manifest["paths"]["verification_profile"] = "esc-verification-profile.yaml"
            write_yaml(component_manifest_path(repository_dir, "content"), manifest)
            generate_indexes(repository_dir)
            generate_dependency_graph(repository_dir)
            add_route(registry, "repositories", "repo", repository_dir)

            class RecordingAdapter:
                def __init__(self):
                    self.called = False

                def execute(self, task_path, workspace_path, adapter_path, policy_path):
                    self.called = True
                    run_dir = repository_dir / ".esc-ai" / "runs" / "run-should-not-happen"
                    run_dir.mkdir(parents=True)
                    return run_dir

            runtime = _AdapterRuntime()
            adapter = RecordingAdapter()
            runtime.adapter = adapter
            runtime.registry = registry
            scheduler = Scheduler(store, runtime, registry)
            task_contracts = contracts()
            task_contracts["task"]["scope"] = {"components": ["content"]}
            task_id, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()

            self.assertFalse(adapter.called)
            run = store.get_run(run_id)
            self.assertEqual("failed", run["status"])
            checkpoint = store.output_yaml(run_id, "checkpoint.yaml")
            self.assertEqual("blocked", checkpoint["checkpoint"]["status"])
            self.assertEqual(1, len(checkpoint["progress"]["blockers"]))
            self.assertIn("ESC_AI_TEST_MISSING_TOKEN_XYZ", checkpoint["progress"]["blockers"][0])
            self.assertIn("unreachable", checkpoint["progress"]["blockers"][0])
            scheduler.close()

    def test_environment_prerequisite_gate_allows_satisfied_prerequisite(self):
        """A satisfied prerequisite must never block the run -- the gate only
        stops on a real gap, never on the mere presence of a declaration."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            self._repository_with_component(repository_dir, "content")
            write_yaml(component_manifest_path(repository_dir, "content").parent / "esc-verification-profile.yaml", {
                "schema_version": 1,
                "profile": {"id": "content-verification", "component": "content"},
                "gates": {
                    "focused": [], "component": [], "impact": [],
                    "final": [{
                        "id": "smoke", "command": ["true"],
                        "prerequisites": [{"kind": "env", "name": "ESC_AI_TEST_PRESENT_TOKEN_XYZ"}],
                    }],
                },
            })
            manifest = load_yaml(component_manifest_path(repository_dir, "content"))
            manifest["paths"]["verification_profile"] = "esc-verification-profile.yaml"
            write_yaml(component_manifest_path(repository_dir, "content"), manifest)
            generate_indexes(repository_dir)
            generate_dependency_graph(repository_dir)
            add_route(registry, "repositories", "repo", repository_dir)

            class RecordingAdapter:
                def __init__(self):
                    self.called = False

                def execute(self, task_path, workspace_path, adapter_path, policy_path):
                    self.called = True
                    run_dir = repository_dir / ".esc-ai" / "runs" / "run-ok"
                    run_dir.mkdir(parents=True)
                    return run_dir

            runtime = _AdapterRuntime()
            adapter = RecordingAdapter()
            runtime.adapter = adapter
            runtime.registry = registry
            scheduler = Scheduler(store, runtime, registry)
            task_contracts = contracts()
            task_contracts["task"]["scope"] = {"components": ["content"]}
            os.environ["ESC_AI_TEST_PRESENT_TOKEN_XYZ"] = "token"
            try:
                task_id, run_id = scheduler.submit(task_contracts)
                scheduler.queue.join()
            finally:
                del os.environ["ESC_AI_TEST_PRESENT_TOKEN_XYZ"]

            self.assertTrue(adapter.called)
            self.assertEqual("succeeded", store.get_run(run_id)["status"])
            scheduler.close()

    def test_verification_failure_blocker_includes_failure_category(self):
        """
        plan/active/pre-flight-doctor-and-gate-prerequisites.md finding #6: a real
        gate failure's checkpoint blocker should carry the classifier's category,
        not just a bare status/exit_code a human has to re-derive from raw logs.
        """
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            self._repository_with_component(repository_dir, "content")
            write_yaml(component_manifest_path(repository_dir, "content").parent / "esc-verification-profile.yaml", {
                "schema_version": 1,
                "profile": {"id": "content-verification", "component": "content"},
                "gates": {
                    "focused": [], "component": [], "impact": [],
                    "final": [{
                        "id": "smoke",
                        "command": [sys.executable, "-c", "import sys; sys.stderr.write('Connection refused'); sys.exit(1)"],
                    }],
                },
            })
            manifest = load_yaml(component_manifest_path(repository_dir, "content"))
            manifest["paths"]["verification_profile"] = "esc-verification-profile.yaml"
            write_yaml(component_manifest_path(repository_dir, "content"), manifest)
            generate_indexes(repository_dir)
            generate_dependency_graph(repository_dir)
            add_route(registry, "repositories", "repo", repository_dir)

            class RecordingAdapter:
                def execute(self, task_path, workspace_path, adapter_path, policy_path):
                    run_dir = repository_dir / ".esc-ai" / "runs" / "run-1"
                    run_dir.mkdir(parents=True)
                    return run_dir

            runtime = _AdapterRuntime()
            runtime.adapter = RecordingAdapter()
            runtime.registry = registry
            scheduler = Scheduler(store, runtime, registry)
            task_contracts = contracts()
            task_contracts["task"]["scope"] = {"components": ["content"]}
            task_id, run_id = scheduler.submit(task_contracts)
            scheduler.queue.join()

            self.assertEqual("failed", store.get_run(run_id)["status"])
            checkpoint = store.output_yaml(run_id, "checkpoint.yaml")
            self.assertIn("category=connectivity", checkpoint["progress"]["blockers"][0])
            scheduler.close()

    def test_http_submission_and_observation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            registry = root / "registry.yaml"
            repository_dir = root / "repo-checkout"
            repository_dir.mkdir()
            add_route(registry, "repositories", "repo", repository_dir)
            scheduler = Scheduler(store, FakeRuntime(root / "runs"), registry)
            httpd = server(scheduler, store, "127.0.0.1", 0, registry)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            request = Request(
                base + "/tasks",
                data=json.dumps(contracts()).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = json.loads(urlopen(request).read())
            scheduler.queue.join()
            run = json.loads(urlopen(base + f"/runs/{response['run_id']}").read())
            events = json.loads(
                urlopen(base + f"/runs/{response['run_id']}/events").read()
            )
            summary = json.loads(
                urlopen(base + f"/runs/{response['run_id']}/summary").read()
            )
            context = json.loads(urlopen(base + f"/runs/{response['run_id']}/context").read())
            plan = json.loads(urlopen(base + f"/runs/{response['run_id']}/verification-plan").read())
            metrics = json.loads(urlopen(base + f"/runs/{response['run_id']}/metrics").read())
            self.assertEqual("succeeded", run["status"])
            self.assertEqual(3, len(events))
            self.assertEqual("passed", summary["verification"]["status"])
            self.assertEqual("task-1", context["task"]["id"])
            self.assertEqual("task-1", plan["task_id"])
            self.assertEqual(2, metrics["execution"]["tool_calls"])
            httpd.shutdown()
            httpd.server_close()
            thread.join()
            scheduler.close()


if __name__ == "__main__":
    unittest.main()
