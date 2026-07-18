# Phase 4 — Human-Assisted Manifest/Profile Construction
**Status:** Complete
**Plan:** [`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md) (Phase 4)
**Spans:** esc-ai-architecture-framework (profile-doc-map.json export),
esc-ai-execution-framework (the answer/apply engine itself), esc-ai-orchestrator
(this repo — statefulness on top)

## Objective

Add statefulness to `esc-ai-execution-framework`'s new `apply_onboarding_answers` —
the first onboarding step that writes to a repository — over the existing HTTP API,
matching the analyze/proposal pattern from Phase 3.

## Deliverables

- [x] `esc_orchestrator/store.py`: new `onboarding_answers` table (`repository_id`
      primary key, `answers`, `result`, timestamps) and
      `save_onboarding_answers`/`get_onboarding_answers`, following the existing
      `onboarding_proposals` pattern.
- [x] `esc_orchestrator/api.py`: `POST /repositories/{id}/answers` resolves the
      repository, requires a proposal to already exist (404 if not — analyze first),
      calls `esc_exec.onboarding.apply_onboarding_answers` with the stored proposal,
      persists the result, and returns it. `GET /repositories/{id}/answers` returns
      the latest applied result (404 if none yet). The existing
      `POST /repositories/{id}/analyze` call site now also passes `registry`, so it
      picks up Phase 4's `profile_id_suggestions` for free.
- [x] `README.md`'s endpoint list updated with both new routes.

## Test coverage

1 new test in `tests/test_orchestrator.py`: answers-before-analyze returns 404;
analyze then answers writes a real manifest with the authored purpose, verifiable on
disk; the GET endpoint returns the same result. 6/6 tests pass overall (run with
`PYTHONPATH` pointed at the sibling `esc-ai-execution-framework` checkout).

## Deliberately not built yet

The plan's API/state-machine boundaries section describes a full typed onboarding
state machine (`onboarding.detected` → `awaiting-semantic-input` →
`awaiting-write-approval` → `validating` → `ready`/`incomplete`). This round is a
working answer/apply endpoint a later phase can wrap in that state machine — not the
state machine itself. Not a shortcut that needs tearing out later: the endpoint's
shape (resolve → require a proposal → apply → persist) is exactly what each state
transition would call into.
