# Escape AI — Phase 1: Framework Composition Protocol
**Status:** In progress
**Plan:** [`../../plan/cohesive-system-integration-and-onboarding.md`](../../plan/cohesive-system-integration-and-onboarding.md) (Phase 0 + Phase 1)
**Spans:** esc-ai-orchestrator, esc-ai-execution-framework, esc-ai-architecture-framework

## Objective

Land the repo-local `.esc-ai/` directory and per-task run-output resolution, then the
framework descriptor/manifest/task-context composition work, so a task context can
identify the exact execution and architecture documents to load without hard-coded
checkout paths. Full rationale and design decisions live in the plan doc; this tracks
only this repo's share of the work and its current status.

## Deliverables

- [ ] Rename `workflows/` -> `.esc-ai/workflows/` in this repo; update
      `workflows/README.md`, `workflows/active/README.md`, `README.md`, and this
      file's own path.
- [ ] Resolve `.esc-ai/runs/<run-id>/` output per task from the target repository
      (`Scheduler._work` / `OpenCodeRuntime.execute`) instead of the single
      process-wide `--output` CLI flag. The orchestrator's own SQLite DB
      (`.orchestrator/orchestrator.db`) stays process-local — only per-run artifacts
      move into the target repository.

## Already done (Phase 0)

- [x] Migration diagnostic for the renamed architecture-framework ID (in
      `esc-ai-execution-framework`'s `registry.py`/`manifests.py`).
- [x] Internal title/reference cleanup in `esc-ai-architecture-framework`.

## Decisions recorded in the plan doc

- Identity/discovery manifests (`esc-execution.yaml`, `esc-index.json`, etc.) stay at
  root; only activity/history (`workflows/`, `runs/`) moves under `.esc-ai/`.
- `.esc-ai/runs/` is gitignored; durable handoff stays the job of checkpoint
  promotion, not committing raw run history.
- Framework compatibility is expressed as compatible-major-version.
- Repositories may be grouped into a named ecosystem in the machine-local catalog;
  live cross-repo coordination stays in the orchestrator's own persistence layer.

## Open follow-up (not yet scheduled)

`OpenCodeAdapter`'s tool permissions are currently hardcoded read-only
(`READ_ONLY_TOOLS` in `esc_exec/opencode_adapter.py`), regardless of what a loaded
`policy.yaml` actually declares — `permissions.edit`/`execute`/`network` are
schema-validated but never consulted when building the tool grant sent to the
runtime. This was an intentional scope boundary for the original read-only OpenCode
spike (see `esc-ai-execution-framework/workflows/archive/opencode-reference-adapter.md`),
but it means the policy contract does not yet enforce anything. Worth a dedicated
workflow before any non-read-only execution path ships, and worth resolving before
wider distribution given how much local system access this project's execution model
implies.
