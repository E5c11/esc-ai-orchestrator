import json
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.request import Request, urlopen

from esc_orchestrator.api import server
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.store import Store


class FakeRuntime:
    def execute(self, contracts, output_root):
        path = output_root / contracts["task"]["task"]["id"]
        path.mkdir(parents=True)
        (path / "run.json").write_text("{}")
        return path


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


class OrchestratorTests(unittest.TestCase):
    def test_scheduler_persists_lifecycle_and_events(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            scheduler = Scheduler(store, FakeRuntime(), root / "runs")
            task_id, run_id = scheduler.submit(contracts())
            scheduler.queue.join()
            self.assertEqual("succeeded", store.get_task(task_id)["status"])
            self.assertEqual("succeeded", store.get_run(run_id)["status"])
            self.assertEqual(
                ["run.queued", "run.running", "run.succeeded"],
                [event["type"] for event in store.events(run_id)],
            )
            scheduler.close()

    def test_http_submission_and_observation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "db.sqlite")
            scheduler = Scheduler(store, FakeRuntime(), root / "runs")
            httpd = server(scheduler, store, "127.0.0.1", 0)
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
            self.assertEqual("succeeded", run["status"])
            self.assertEqual(3, len(events))
            httpd.shutdown()
            httpd.server_close()
            thread.join()
            scheduler.close()


if __name__ == "__main__":
    unittest.main()
