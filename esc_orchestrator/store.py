from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, status TEXT NOT NULL, contracts TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL, output_path TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, sequence INTEGER NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        """)

    def submit(self, contracts: dict[str, Any]) -> tuple[str, str]:
        task_id = contracts["task"]["task"]["id"]
        run_id, timestamp = f"run-{uuid.uuid4().hex}", now()
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO tasks VALUES(?,?,?,?,?)", (task_id, "queued", json.dumps(contracts), timestamp, timestamp))
            self.connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (run_id, task_id, "queued", None, None, timestamp, timestamp))
            self._event(run_id, "run.queued", {"task_id": task_id})
        return task_id, run_id

    def update_run(self, run_id: str, status: str, output_path: str | None = None, error: str | None = None):
        with self.lock, self.connection:
            row = self.connection.execute("SELECT task_id FROM runs WHERE id=?", (run_id,)).fetchone()
            self.connection.execute("UPDATE runs SET status=?,output_path=?,error=?,updated_at=? WHERE id=?", (status, output_path, error, now(), run_id))
            self.connection.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?", (status, now(), row["task_id"]))
            self._event(run_id, f"run.{status}", {"output_path": output_path, "error": error})

    def _event(self, run_id: str, event_type: str, payload: dict[str, Any]):
        sequence = self.connection.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
        self.connection.execute("INSERT INTO events(run_id,sequence,type,payload,created_at) VALUES(?,?,?,?,?)", (run_id, sequence, event_type, json.dumps(payload), now()))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT sequence,type,payload,created_at FROM events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def summary(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run or not run["output_path"]:
            return None
        path = Path(run["output_path"]) / "verification-summary.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return value

    def contracts(self, task_id: str) -> dict[str, Any]:
        return json.loads(self.connection.execute("SELECT contracts FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
