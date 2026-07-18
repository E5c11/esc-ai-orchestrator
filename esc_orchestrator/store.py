from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from esc_exec.yaml_io import load_yaml


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
        CREATE TABLE IF NOT EXISTS onboarding_proposals(repository_id TEXT PRIMARY KEY, input_digest TEXT NOT NULL, proposal TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS onboarding_answers(repository_id TEXT PRIMARY KEY, answers TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS onboarding_pending_answers(repository_id TEXT PRIMARY KEY, answers TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plan_drafts(initiative_id TEXT PRIMARY KEY, work_type TEXT NOT NULL, objective TEXT NOT NULL, repositories TEXT NOT NULL, routing TEXT NOT NULL, questions TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plan_pending_answers(initiative_id TEXT PRIMARY KEY, answers TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plan_results(initiative_id TEXT PRIMARY KEY, answers TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_attempts(task_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL, updated_at TEXT NOT NULL);
        """)

    def submit(self, contracts: dict[str, Any]) -> tuple[str, str]:
        """
        Upserts the task row (retrying a task resubmits its same task_id -- this
        must not fail as a duplicate) and always inserts a fresh run row, since each
        attempt is its own run regardless of whether the task_id has been seen before.
        """
        task_id = contracts["task"]["task"]["id"]
        run_id, timestamp = f"run-{uuid.uuid4().hex}", now()
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO tasks(id,status,contracts,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, contracts=excluded.contracts, updated_at=excluded.updated_at",
                (task_id, "queued", json.dumps(contracts), timestamp, timestamp),
            )
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

    def get_latest_run_for_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def record_attempt(self, task_id: str) -> int:
        """Increment and return this task's attempt count -- retrying a failed task
        must know it's a retry, not silently look like the first attempt."""
        timestamp = now()
        with self.lock, self.connection:
            row = self.connection.execute("SELECT attempts FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            self.connection.execute(
                "INSERT INTO task_attempts(task_id,attempts,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET attempts=excluded.attempts, updated_at=excluded.updated_at",
                (task_id, attempts, timestamp),
            )
            return attempts

    def get_attempt_count(self, task_id: str) -> int:
        row = self.connection.execute("SELECT attempts FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()
        return row["attempts"] if row else 0

    def events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT sequence,type,payload,created_at FROM events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def summary(self, run_id: str) -> dict[str, Any] | None:
        return self.output_document(run_id, "verification-summary.json")

    def output_document(self, run_id: str, filename: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run or not run["output_path"]:
            return None
        path = Path(run["output_path"]) / filename
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return value

    def output_yaml(self, run_id: str, filename: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run or not run["output_path"]:
            return None
        path = Path(run["output_path"]) / filename
        return load_yaml(path) if path.is_file() else None

    def contracts(self, task_id: str) -> dict[str, Any]:
        return json.loads(self.connection.execute("SELECT contracts FROM tasks WHERE id=?", (task_id,)).fetchone()[0])

    def save_onboarding_proposal(self, repository_id: str, proposal: dict[str, Any]) -> None:
        timestamp = now()
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT created_at FROM onboarding_proposals WHERE repository_id=?", (repository_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else timestamp
            self.connection.execute(
                "INSERT INTO onboarding_proposals(repository_id,input_digest,proposal,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(repository_id) DO UPDATE SET input_digest=excluded.input_digest, proposal=excluded.proposal, updated_at=excluded.updated_at",
                (repository_id, proposal["input_digest"], json.dumps(proposal), created_at, timestamp),
            )

    def get_onboarding_proposal(self, repository_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM onboarding_proposals WHERE repository_id=?", (repository_id,)
        ).fetchone()
        if not row:
            return None
        return {**dict(row), "proposal": json.loads(row["proposal"])}

    def save_onboarding_answers(self, repository_id: str, answers: dict[str, Any], result: dict[str, Any]) -> None:
        timestamp = now()
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT created_at FROM onboarding_answers WHERE repository_id=?", (repository_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else timestamp
            self.connection.execute(
                "INSERT INTO onboarding_answers(repository_id,answers,result,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(repository_id) DO UPDATE SET answers=excluded.answers, result=excluded.result, updated_at=excluded.updated_at",
                (repository_id, json.dumps(answers), json.dumps(result), created_at, timestamp),
            )

    def get_onboarding_answers(self, repository_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM onboarding_answers WHERE repository_id=?", (repository_id,)
        ).fetchone()
        if not row:
            return None
        return {**dict(row), "answers": json.loads(row["answers"]), "result": json.loads(row["result"])}

    def save_pending_answers(self, repository_id: str, answers: dict[str, Any]) -> None:
        """
        Stage answers without applying them -- the explicit answer/apply approval
        boundary the CLI's `repository answer`/`repository apply` subcommands need.
        Distinct from save_onboarding_answers, which records an already-applied result.
        """
        timestamp = now()
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT created_at FROM onboarding_pending_answers WHERE repository_id=?", (repository_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else timestamp
            self.connection.execute(
                "INSERT INTO onboarding_pending_answers(repository_id,answers,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(repository_id) DO UPDATE SET answers=excluded.answers, updated_at=excluded.updated_at",
                (repository_id, json.dumps(answers), created_at, timestamp),
            )

    def get_pending_answers(self, repository_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM onboarding_pending_answers WHERE repository_id=?", (repository_id,)
        ).fetchone()
        if not row:
            return None
        return {**dict(row), "answers": json.loads(row["answers"])}

    def save_plan_draft(self, initiative_id: str, work_type: str, objective: str, repositories: list[str], routing: dict[str, Any], questions: list[dict[str, Any]]) -> None:
        timestamp = now()
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT created_at FROM plan_drafts WHERE initiative_id=?", (initiative_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else timestamp
            self.connection.execute(
                "INSERT INTO plan_drafts(initiative_id,work_type,objective,repositories,routing,questions,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(initiative_id) DO UPDATE SET work_type=excluded.work_type, objective=excluded.objective, "
                "repositories=excluded.repositories, routing=excluded.routing, questions=excluded.questions, updated_at=excluded.updated_at",
                (initiative_id, work_type, objective, json.dumps(repositories), json.dumps(routing), json.dumps(questions), created_at, timestamp),
            )

    def get_plan_draft(self, initiative_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM plan_drafts WHERE initiative_id=?", (initiative_id,)).fetchone()
        if not row:
            return None
        return {
            **dict(row),
            "repositories": json.loads(row["repositories"]),
            "routing": json.loads(row["routing"]),
            "questions": json.loads(row["questions"]),
        }

    def save_plan_pending_answers(self, initiative_id: str, answers: dict[str, Any]) -> None:
        timestamp = now()
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT created_at FROM plan_pending_answers WHERE initiative_id=?", (initiative_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else timestamp
            self.connection.execute(
                "INSERT INTO plan_pending_answers(initiative_id,answers,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(initiative_id) DO UPDATE SET answers=excluded.answers, updated_at=excluded.updated_at",
                (initiative_id, json.dumps(answers), created_at, timestamp),
            )

    def get_plan_pending_answers(self, initiative_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM plan_pending_answers WHERE initiative_id=?", (initiative_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "answers": json.loads(row["answers"])}

    def save_plan_result(self, initiative_id: str, answers: dict[str, Any], result: dict[str, Any]) -> None:
        timestamp = now()
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT created_at FROM plan_results WHERE initiative_id=?", (initiative_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else timestamp
            self.connection.execute(
                "INSERT INTO plan_results(initiative_id,answers,result,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(initiative_id) DO UPDATE SET answers=excluded.answers, result=excluded.result, updated_at=excluded.updated_at",
                (initiative_id, json.dumps(answers), json.dumps(result), created_at, timestamp),
            )

    def get_plan_result(self, initiative_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM plan_results WHERE initiative_id=?", (initiative_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "answers": json.loads(row["answers"]), "result": json.loads(row["result"])}

