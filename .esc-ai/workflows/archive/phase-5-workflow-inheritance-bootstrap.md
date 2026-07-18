# Phase 5 — Workflow Inheritance Bootstrap
**Status:** Complete
**Plan:** [`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md) (Phase 5)
**Spans:** esc-ai-execution-framework (the bootstrap engine itself), esc-ai-orchestrator
(this repo — confirming the existing HTTP surface already carries it)

## Objective

Confirm `esc-ai-execution-framework`'s new `bootstrap_workflow_inheritance` (wired
into `apply_onboarding_answers` as its final step) is visible over the existing
`POST /repositories/{id}/answers` endpoint without any orchestrator code change.

## Deliverables

- [x] No code change needed: `apply_onboarding_answers`'s full result dict —
      including the new `workflow_inheritance` key (`created`/`existing`/
      `advisory_warnings`) — already flows through `api.py`'s `answers` handler
      untouched (`return self.send(200, result)`).
- [x] `tests/test_orchestrator.py`'s existing answers-endpoint test extended to
      assert `workflow_inheritance["created"]` includes `INSTRUCTIONS.md`, that
      `INSTRUCTIONS.md`/`.esc-ai/workflows/README.md` actually exist on disk in the
      checkout afterward, and that the GET endpoint returns the same
      `workflow_inheritance` value as the POST response.

## Test coverage

6/6 tests pass (no new test count — the existing answers-endpoint test was extended
rather than duplicated, since it's the same round-trip being verified more deeply).
Run with `PYTHONPATH` pointed at the sibling `esc-ai-execution-framework` checkout.
