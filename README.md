# ESC AI Orchestrator

Central control plane for scheduling, executing, observing, and resuming AI-assisted
software tasks across registered repositories and agent runtimes.

This repository implements the portable contracts defined by
`esc-ai-execution-framework`. It does not redefine those schemas or repository routing
conventions.

## Bootstrap architecture

```text
HTTP API -> SQLite task/run/event store -> in-process scheduler -> runtime adapter
                                                               -> OpenCode initially
```

The bootstrap intentionally uses the Python standard library. Store, scheduler, and
runtime are separate boundaries so PostgreSQL, a distributed queue, remote workers,
authentication, and richer APIs can be introduced later.

## Run locally

Install both sibling repositories in editable mode, then:

```bash
esc-orchestrator --port 8042 --opencode http://127.0.0.1:4097
```

Endpoints:

- `GET /health`
- `POST /tasks` — submit a JSON object containing `task`, `workspace`, `adapter`, and
  `policy` portable contracts
- `GET /tasks/{id}`
- `GET /runs/{id}`
- `GET /runs/{id}/events`
- `GET /runs/{id}/summary` — return the bounded `verification-summary.json` when the
  runtime produced one; complete reports remain in the run output directory

Local state lives under `.orchestrator/` and is not committed.

## Test

```bash
python -m unittest discover -v
```
