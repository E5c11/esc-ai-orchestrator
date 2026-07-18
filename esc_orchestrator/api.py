from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from esc_exec.onboarding import analyze_repository, apply_onboarding_answers
from esc_exec.registry import resolve_route


def server(scheduler, store, host: str, port: int, registry: Path):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path == "/health": return self.send(200, {"status": "ok"})
            parts = self.path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "tasks": value = store.get_task(parts[1])
            elif len(parts) == 2 and parts[0] == "runs": value = store.get_run(parts[1])
            elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "events": value = store.events(parts[1])
            elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "summary": value = store.summary(parts[1])
            elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "context": value = store.output_document(parts[1], "task-context.json")
            elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "verification-plan": value = store.output_document(parts[1], "verification-plan.json")
            elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "checkpoint": value = store.output_yaml(parts[1], "checkpoint.yaml")
            elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "metrics": value = store.output_document(parts[1], "run-metrics.json")
            elif len(parts) == 3 and parts[0] == "repositories" and parts[2] == "proposal":
                record = store.get_onboarding_proposal(parts[1])
                value = record["proposal"] if record else None
            elif len(parts) == 3 and parts[0] == "repositories" and parts[2] == "answers":
                record = store.get_onboarding_answers(parts[1])
                value = record["result"] if record else None
            else: return self.send(404, {"error": "not found"})
            return self.send(200 if value else 404, value or {"error": "not found"})
        def do_POST(self):
            parts = self.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "repositories" and parts[2] == "analyze":
                repository_id = parts[1]
                try:
                    repository_path = resolve_route(registry, "repositories", repository_id)
                except (KeyError, FileNotFoundError) as exc:
                    return self.send(404, {"error": str(exc)})
                try:
                    proposal = analyze_repository(repository_path, registry)
                except (OSError, ValueError) as exc:
                    return self.send(400, {"error": str(exc)})
                store.save_onboarding_proposal(repository_id, proposal)
                return self.send(200, proposal)
            if len(parts) == 3 and parts[0] == "repositories" and parts[2] == "answers":
                repository_id = parts[1]
                try:
                    repository_path = resolve_route(registry, "repositories", repository_id)
                except (KeyError, FileNotFoundError) as exc:
                    return self.send(404, {"error": str(exc)})
                record = store.get_onboarding_proposal(repository_id)
                if record is None:
                    return self.send(404, {"error": f"no onboarding proposal for `{repository_id}`; analyze first"})
                try:
                    answers = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                    result = apply_onboarding_answers(repository_path, record["proposal"], answers, registry)
                except (OSError, ValueError) as exc:
                    return self.send(400, {"error": str(exc)})
                store.save_onboarding_answers(repository_id, answers, result)
                return self.send(200, result)
            if self.path != "/tasks": return self.send(404, {"error": "not found"})
            try:
                value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                task_id, run_id = scheduler.submit(value)
                self.send(202, {"task_id": task_id, "run_id": run_id})
            except Exception as exc: self.send(400, {"error": str(exc)})
        def log_message(self, format, *args): pass
    return ThreadingHTTPServer((host, port), Handler)
