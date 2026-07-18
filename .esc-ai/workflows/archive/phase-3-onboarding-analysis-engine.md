# Phase 3 — Onboarding Analysis Engine
**Status:** Complete
**Plan:** [`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md) (Phase 3)
**Spans:** esc-ai-execution-framework (the analysis engine itself), esc-ai-orchestrator (this repo — statefulness on top)

## Objective

Add statefulness to `esc-ai-execution-framework`'s new read-only `analyze_repository`:
persist each repository's latest onboarding proposal and input digest, and expose
analyze/fetch over the existing HTTP API, ahead of the unified `escape-ai` CLI
(Phase 6).

## Deliverables

- [x] `esc_orchestrator/store.py`: new `onboarding_proposals` table
      (`repository_id` primary key, `input_digest`, `proposal`, timestamps) and
      `save_onboarding_proposal`/`get_onboarding_proposal` methods, following the
      existing `submit`/`update_run` lock-and-connection pattern.
- [x] `esc_orchestrator/api.py`: `server()` now takes a `registry: Path` parameter.
      `POST /repositories/{id}/analyze` resolves the repository via `resolve_route`,
      calls `esc_exec.onboarding.analyze_repository`, persists the result, and returns
      it (404 if the repository ID isn't registered). `GET /repositories/{id}/proposal`
      returns the latest persisted proposal (404 if none yet).
- [x] `esc_orchestrator/cli.py`: threads `args.registry` into the new `server()`
      parameter at its one real call site.
- [x] `README.md`'s endpoint list updated with both new routes.

## Test coverage

2 new tests in `tests/test_orchestrator.py`: the analyze → persist → fetch round trip
against a real minimal Gradle fixture, confirming a repeated analyze against unchanged
inputs reproduces the same `input_digest`; and an unregistered-repository 404. 5/5 tests
pass overall (run with `PYTHONPATH` pointed at the sibling `esc-ai-execution-framework`
checkout, since `esc_exec` isn't installed in this environment).
