from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Protocol, Any

from esc_orchestrator.store import Store


class Runtime(Protocol):
    def execute(self, contracts: dict[str, Any], output_root: Path) -> Path: ...


class Scheduler:
    def __init__(self, store: Store, runtime: Runtime, output_root: Path):
        self.store, self.runtime, self.output_root = store, runtime, output_root
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
                output = self.runtime.execute(self.store.contracts(task_id), self.output_root)
                self.store.update_run(run_id, "succeeded", str(output))
            except Exception as exc:
                self.store.update_run(run_id, "failed", error=str(exc)[:1000])
            finally:
                self.queue.task_done()

    def close(self):
        self.queue.put(None)
        self.thread.join(timeout=5)
