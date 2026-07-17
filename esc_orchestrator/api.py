from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def server(scheduler, store, host: str, port: int):
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
            else: return self.send(404, {"error": "not found"})
            return self.send(200 if value else 404, value or {"error": "not found"})
        def do_POST(self):
            if self.path != "/tasks": return self.send(404, {"error": "not found"})
            try:
                value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                task_id, run_id = scheduler.submit(value)
                self.send(202, {"task_id": task_id, "run_id": run_id})
            except Exception as exc: self.send(400, {"error": str(exc)})
        def log_message(self, format, *args): pass
    return ThreadingHTTPServer((host, port), Handler)
