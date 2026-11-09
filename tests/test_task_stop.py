import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from esc_exec.processes import process_alive
from esc_exec.worktree import ensure_worktree, worktree_branch, worktree_path
from esc_orchestrator.application.runs import stop_task
from esc_orchestrator.store import Store


def _contracts(task_id: str = "task-1") -> dict:
    return {"task": {"task": {"id": task_id, "repository": "repo"}}}


def _spawn_tree() -> tuple[subprocess.Popen, int]:
    script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(child.pid, flush=True); time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    return parent, int(parent.stdout.readline().strip())


class StoreRunPidTests(unittest.TestCase):
    def test_an_old_database_gains_the_pid_column(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "old.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE runs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL, output_path TEXT, "
                "error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO runs VALUES('run-old','task-0','succeeded',NULL,NULL,'t','t')")
            connection.commit()
            connection.close()
            store = Store(path)
            self.assertIsNone(store.get_run("run-old")["pid"])
            _, run_id = store.submit(_contracts())
            store.set_run_pid(run_id, 4242)
            self.assertEqual(4242, store.get_run(run_id)["pid"])

    def test_a_cancelled_run_is_not_overwritten_by_a_later_status(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp) / "db.sqlite")
            task_id, run_id = store.submit(_contracts())
            store.update_run(run_id, "running")
            store.update_run(run_id, "cancelled", error="stopped")
            store.update_run(run_id, "failed", error="agent killed")
            self.assertEqual("cancelled", store.get_run(run_id)["status"])
            self.assertEqual("cancelled", store.get_task(task_id)["status"])
            self.assertIn("run.status-ignored", [event["type"] for event in store.events(run_id)])


class StopTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "T")):
            subprocess.run(["git", *args], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.repo, check=True)
        self.store = Store(root / "db.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def _branch_exists(self, task_id: str) -> bool:
        return bool(subprocess.run(
            ["git", "branch", "--list", worktree_branch(task_id)], cwd=self.repo, capture_output=True, text=True,
        ).stdout.strip())

    def test_stops_the_run_process_tree_cancels_and_removes_the_worktree(self):
        task_id, run_id = self.store.submit(_contracts())
        self.store.update_run(run_id, "running")
        ensure_worktree(self.repo, task_id)
        parent, child_pid = _spawn_tree()
        try:
            self.store.set_run_pid(run_id, parent.pid)
            result = stop_task(self.store, self.repo, task_id)
            parent.wait(timeout=10)
            deadline = time.monotonic() + 5
            while process_alive(child_pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(result["stopped"])
            self.assertIn(parent.pid, result["signalled"])
            self.assertFalse(process_alive(child_pid))
            self.assertEqual("cancelled", self.store.get_run(run_id)["status"])
            self.assertFalse(worktree_path(self.repo, task_id).exists())
            self.assertFalse(self._branch_exists(task_id))
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            parent.stdout.close()

    def test_keep_worktree_leaves_the_worktree_and_branch(self):
        task_id, run_id = self.store.submit(_contracts())
        self.store.update_run(run_id, "running")
        ensure_worktree(self.repo, task_id)
        result = stop_task(self.store, self.repo, task_id, keep_worktree=True)
        self.assertEqual("kept", result["worktree"])
        self.assertTrue(worktree_path(self.repo, task_id).exists())
        self.assertTrue(self._branch_exists(task_id))

    def test_a_run_without_a_recorded_process_is_still_cancelled(self):
        task_id, run_id = self.store.submit(_contracts())
        self.store.update_run(run_id, "running")
        result = stop_task(self.store, self.repo, task_id)
        self.assertTrue(result["stopped"])
        self.assertIn("no process was recorded", result["process_note"])
        self.assertEqual("cancelled", self.store.get_run(run_id)["status"])

    def test_a_finished_run_is_left_alone(self):
        task_id, run_id = self.store.submit(_contracts())
        self.store.update_run(run_id, "succeeded")
        ensure_worktree(self.repo, task_id)
        result = stop_task(self.store, self.repo, task_id)
        self.assertFalse(result["stopped"])
        self.assertEqual("succeeded", self.store.get_run(run_id)["status"])
        self.assertTrue(worktree_path(self.repo, task_id).exists())


if __name__ == "__main__":
    unittest.main()
