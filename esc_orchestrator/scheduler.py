from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Protocol, Any

from esc_orchestrator.store import Store
from esc_exec.checkpoints import checkpoint_document
from esc_exec.registry import resolve_route
from esc_exec.yaml_io import write_yaml


class Runtime(Protocol):
    def execute(self, contracts: dict[str, Any]) -> Path: ...


def _verification_result(output: Path) -> dict[str, Any] | None:
    """
    Reads the `verification-result.json` a `Runtime.execute()` call should have
    produced (`_AdapterRuntime` always writes one via `execute_verification_plan`).
    Returns None if absent -- a test double that doesn't model verification at all
    (e.g. `FakeRuntime`) still gets the pre-task-5 trust-the-runtime behavior below,
    rather than being forced to fake this file too.
    """
    path = output / "verification-result.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _verification_failure_summary(result: dict[str, Any]) -> str:
    failures = [
        f"{gate['id']}.{check['id']} ({check['status']}, exit_code={check['exit_code']})"
        for gate in result["gates"]
        for check in gate["checks"]
        if check["status"] in {"failed", "error"}
    ]
    return "verification failed: " + ", ".join(failures)


class Scheduler:
    def __init__(self, store: Store, runtime: Runtime, registry: Path):
        self.store, self.runtime, self.registry = store, runtime, registry
        self.queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def submit(self, contracts: dict[str, Any]) -> tuple[str, str]:
        task_id, run_id = self.store.submit(contracts)
        self.queue.put((task_id, run_id))
        return task_id, run_id

    def _work(self):
        while True:
            item = self.queue.get()
            if item is None: return
            task_id, run_id = item
            self.store.update_run(run_id, "running")
            try:
                output = self.runtime.execute(self.store.contracts(task_id))
                result = _verification_result(output)
                if result is not None and result["status"] != "passed":
                    self.store.update_run(
                        run_id, "failed", str(output), _verification_failure_summary(result)
                    )
                else:
                    self.store.update_run(run_id, "succeeded", str(output))
            except Exception as exc:
                error = str(exc)[:1000]
                output_path = None
                try:
                    task = self.store.contracts(task_id)["task"]
                    repository = resolve_route(self.registry, "repositories", task["task"]["repository"])
                    candidate_dir = repository / ".esc-ai" / "runs" / run_id
                    document = checkpoint_document(
                        self.store.contracts(task_id)["task"],
                        run_id=run_id,
                        status="blocked",
                        remaining=["Resolve the recorded blocker and resume the task."],
                        blockers=[error],
                        last_event_sequence=len(self.store.events(run_id)) - 1,
                    )
                    write_yaml(candidate_dir / "checkpoint.yaml", document)
                    output_path = str(candidate_dir)
                except Exception:
                    pass
                self.store.update_run(run_id, "failed", output_path=output_path, error=error)
            finally:
                self.queue.task_done()

    def close(self):
        self.queue.put(None)
        self.thread.join(timeout=5)
