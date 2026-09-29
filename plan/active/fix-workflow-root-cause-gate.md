# Fix Workflow: the root-cause gate (BLA-43) — Plan

**Status:** Implemented (2026-09-29); not yet merged
**Date:** 2026-09-29
**Objective:** Make `escape-ai fix` a real procedure: a fix cannot be planned or dispatched without a
recorded root cause, the agent implements against that root cause, and the final report says what was wrong,
what changed and how it was validated. Implements the `root_cause` stage from BLA-48's contract, which
`escape-ai fix --help` currently labels "(not yet enforced)". VISION.md: "`fix` requires root-cause capture
before a plan can be produced" and "a verb that routes to a procedure with no real gates behind it is
cosmetic".

## What we found (in code, not assumed)

- `work_type` is **only** written into a task's README. `task.yaml` (the only thing execution reads) has no
  work type, so a `fix` task and a `feature` task are indistinguishable at execution time. The procedure is
  therefore not enforced past planning today; any stage that must hold at execution needs `work_type` in
  `task.yaml`.
- `task-specification.schema.yaml` is `additionalProperties: false` and owned by
  `esc-ai-execution-framework` (the orchestrator's INSTRUCTIONS.md: don't duplicate or silently extend those
  contracts here), so the gate and schema change belong in the engine.
- Three adapters (`claude_code_adapter`, `codex_adapter`, `opencode_adapter`) each build their own
  `_prompt`; none passes anything but the objective, components, architecture docs and paths. A root cause
  stored but not shown to the agent would be decoration.
- `planning_questions` takes no work type, so a `fix` draft asks exactly what a `feature` draft asks.
- Scope discovery already exists (routing suggests components; `multi_repository` is derived), and
  `initiative` plans already span repositories, so "escalate from local to multi-repo without choosing upfront"
  is met by the existing draft/route path; nothing new is needed beyond carrying the root cause to every task.

## Decisions

1. **Who produces the root cause: the operator supplies it, structured.** A `root_cause` answer with
   `statement`, `evidence` (one or more observations: a reproduction, a log line, a `file:line`), and optional
   `reproduction` (a command). AI-assisted investigation ("suggest a root cause") belongs to the `investigate`
   procedure (BLA-44) and is a follow-up, not smuggled into this gate.
2. **The gate checks that a root cause was captured and is well-formed, not that it is true.** A gate cannot
   observe truth. It rejects: missing, empty statement, no evidence, and a statement that just restates the
   objective (a symptom is not a cause). Truth is what `verify` is for: the fix must pass the real gates. This
   limit is stated in the docs and the help text rather than implied away.
3. **Enforced in the engine's workflow generator**, so every caller (CLI, interactive, future MCP) gets it;
   the orchestrator maps "missing" to an `IncompleteError` (exit 2, "answer this first") and "malformed" to
   `InvalidInputError`.
4. **Backward compatible:** `task.work_type` and `root_cause` are optional in the schema; a task without
   them stays valid. Only `work_type: fix` makes `root_cause` mandatory.
5. **One root cause per initiative**, copied to each repository's task (a multi-repo fix has one cause).

## Approach

Engine (`esc-ai-execution-framework`): `esc_exec/root_cause.py` (validate); schema + contract validation
(`task.work_type`, `root_cause`); `planning_questions(work_type=...)` asks the root-cause fields for a fix;
both workflow generators require and persist it and render a "Root cause" README section; `task_context`
carries `work_type` and `root_cause`; the three adapter prompts state it; `procedures.ROOT_CAUSE.maps_to`
points at the real validator (so the "not yet enforced" marker goes away by itself).

Orchestrator: `draft_plan` passes the work type; `apply_plan` supplies the root cause and translates errors;
the interactive planning flow asks the questions; `render_execution_result`/preview show the root cause; the
`fix` help text says what the gate does and does not guarantee.

## Non-goals

- AI-suggested root causes; reproducing the failure automatically; `baseline_capture` (refactor) and
  `grounding_check` (document), which stay "not yet enforced" (BLA-44).
- Verifying a root cause is correct.

## Acceptance (from BLA-43)

- A user can run `escape-ai fix "<problem statement>" -r <repo>` and reach a plan.
- Root-cause analysis is captured before implementation: applying a fix plan without one fails (exit 2).
- Scope is discovered (routing suggestions) and may span repositories.
- Existing validation gates run before completion (unchanged `verify`).
- The final output explains what was wrong, what changed and how it was validated.

## Outcome

Engine `bdcba8e` (571 tests, +35) and orchestrator `22bdfbc` (286 tests, +19), on `feat/bla-43-*` branches.

- `esc_exec/root_cause.py` is the gate; `planning` enforces it (validated before any write, one cause copied to every
  repository's task of a multi-repo fix); `contracts` refuses a `fix` task without one even if `task.yaml` was edited;
  `task.yaml` now records `work_type`; all three adapter prompts carry the cause; `procedures.ROOT_CAUSE` no longer
  says "new", so the `(not yet enforced)` marker disappeared by itself.
- CLI: a fix draft asks for the cause first; applying without one is exit 2 with the shape to supply; the final report
  is what was wrong / what changed (worktree diff) / how validated (verification summary); `fix --help` explains the
  gate and its limit.
- Interactive drift detection can reclassify a task: leaving `fix` discards the cause, becoming `fix` asks for it.

### Things found while building it

- The work type never reached `task.yaml`, so no stage could be enforced at execution time. (Docs: `PYUC-STAGE-06`.)
- A gate on supplied knowledge cannot check truth. It checks presence and well-formedness and says so; `verify` tests
  the cause. (Docs: `PYUC-STAGE-05`.)
- My own slip: inserting a helper between `@translates_engine_errors` and `draft_plan` silently moved the decorator
  onto the helper. A test caught it. A guard test that every public application operation is decorated would make this
  impossible to repeat, and is worth adding.

### Still open

- No AI-suggested root cause (the `investigate` procedure, BLA-44). The operator supplies it.
- Nothing checks that the agent's change actually addresses the recorded cause; only `verify` runs. A "regression test
  added" check would need the baseline/reproduction stage (BLA-44).
- `escape-ai fix` still needs `-r` repositories up front. Routing suggests components, and multi-repo is supported, but
  discovering *which repositories* are affected from the problem statement alone is not built.
- `baseline_capture` and `grounding_check` remain "(not yet enforced)".
