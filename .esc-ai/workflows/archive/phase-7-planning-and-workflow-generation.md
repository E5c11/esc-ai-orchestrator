# Phase 7 — Feature/Fix Planning and Workflow Generation
**Status:** Complete (typed-question scope; multi-turn conversation deliberately deferred)
**Plan:** [`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md) (Phase 7)
**Spans:** esc-ai-execution-framework (the planning engine itself — see its own
tracking doc for full rationale), esc-ai-orchestrator (this repo — CLI wiring)

## Objective

Wire "Plan new work" (previously a stub) into the `escape-ai` CLI: turn a work type,
objective, and repository selection into approved, independently resumable
`.esc-ai/workflows/active/<task-id>/` files, following the exact structural pattern
onboarding already established (thin rendering tested separately from business
logic; non-interactive subcommands alongside the interactive menu item; `Store`-
backed persistence for resumability).

## Deliberate scope boundary

Same reasoning as the execution framework's own tracking doc: the plan's multi-turn,
runtime-mediated planning conversation is gated on the still-open policy-to-tool-grant
enforcement gap. This phase wires up the typed-question substitute
(`esc_exec.planning.planning_questions`) instead — a bounded question set collected
once, non-conversationally — not a live conversation.

## Deliverables

- [x] `esc_orchestrator/store.py`: `plan_drafts`, `plan_pending_answers`,
      `plan_results` tables plus their save/get methods, mirroring the
      `onboarding_proposals`/`onboarding_pending_answers`/`onboarding_answers`
      pattern exactly.
- [x] `esc_orchestrator/escape_ai_cli.py`:
      `draft_plan` resolves each named repository (registering an unregistered path
      the same way onboarding does), routes the objective per repository via
      `route_objective`, builds the typed question set via `planning_questions`, and
      persists the draft;
      `apply_plan` reads the stored draft and provided answers, and — the one
      deliberate write step — calls `generate_single_repository_workflow` for a
      single repository or `generate_multi_repository_workflow` for more than one,
      chaining each repository's task to the previous one in declared order (the
      plan's own worked example, contracts → backend → mobile, is exactly this
      linear shape) rather than asking for a full dependency graph through the CLI;
      non-interactive `plan draft/answer/apply/status` subcommands and menu choice
      "2" in the interactive wizard, both sharing the same operations (mirroring how
      onboarding's interactive and non-interactive paths already share
      `analyze`/`apply_answers`).
- [x] `README.md`'s CLI usage section updated with the new `plan` subcommands.

## Verification

22/22 tests pass (16 prior + 6 new: 3 pure rendering tests with faked data, 2 real
end-to-end non-interactive tests — single-repository and multi-repository — against
real temp Gradle repositories, 1 real end-to-end interactive test with scripted
`input()`). Beyond the test suite, manually ran the actual `escape_ai_cli` module
(not just its tests) through both a single-repository plan and a two-repository
plan against fresh temp repositories:

- Single-repository: `plan draft` → `plan answer` → `plan apply` → `plan status`,
  confirming `task.yaml`/`README.md` land under
  `.esc-ai/workflows/active/feature-export/` with the authored completion conditions
  present in both files, and `status` reports `has_result: True`.
- Multi-repository (`repo-a`, `repo-b`): confirmed `repo-b`'s generated task
  correctly declares `task.initiative.depends_on: [repo-a/feature-cross-repo-a]` —
  a stable repository/task-id reference, never an absolute path — while `repo-a`'s
  own task has no `depends_on` at all (it's first in the chain). Both generated
  `task.yaml` files independently validate as `VALID` against the `task` contract
  via `esc_exec.contracts.validate_contract`.

## Deliberately not built yet

No new HTTP endpoints for planning — this phase's directive scoped the deliverable to
the `escape-ai` CLI itself (mirroring how the CLI's onboarding flow works entirely
in-process against `Store`, no daemon required). Exposing planning over HTTP, if
wanted, is a natural follow-up using the exact same pattern Phase 3/4 already
established for onboarding.
