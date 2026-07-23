import json
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.request import Request, urlopen

from esc_exec.dependencies import generate_dependency_graph
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import component_manifest_path, component_manifest_relative_path, repository_manifest_path
from esc_exec.registry import add_route
from esc_exec.task_context import generate_gradle_verification_profile
from esc_exec.yaml_io import write_yaml
from esc_orchestrator.api import server
from esc_orchestrator.runtime import _AdapterRuntime
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.store import Store


class FakeRuntime:
    def __init__(self, output_root: Path, verification_status: str | None = None):
        """
        `verification_status`, when set, writes a `verification-result.json` the
        way a real `_AdapterRuntime` (via `execute_verification_plan`) would --
        exercising Scheduler's independent-verification status wiring. Left unset
        by default so existing tests keep exercising the pre-task-5 "no
        verification-result.json produced" case (e.g. an adapter whose task has no
        verification profile at all), which must still resolve to "succeeded".
        """
        self.output_root = output_root
        self.verification_status = verification_status

    def execute(self, contracts):
        path = self.output_root / contracts["task"]["task"]["id"]
        path.mkdir(parents=True)
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

    def test_verification_result_failed_marks_run_failed_not_agent_self_report(self):
        """
        The runtime itself never raised (the agent's own self-report was "done") --
        only the independently-executed verification-result says otherwise. The run
        must still land as "failed", per plan/active/task-orchestration-and-
        verification-loop.md task 5: the verified artifact is authoritative, not
        whatever the agent's own final message claimed.
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
