# Phase 8 — Integrated Execution Lifecycle
**Status:** Complete (workspace/adapter/policy are placeholder defaults pending real
"Configure system" support — see below)
**Plan:** [`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md) (Phase 8)
**Spans:** esc-ai-execution-framework (instruction-bundle composition, process
metrics — see its own tracking doc), esc-ai-orchestrator (this repo — the bulk:
scheduler wiring, retry, checkpoint promotion, resume views)

## Objective

Connect the planning output of Phase 7 (`.esc-ai/workflows/active/<task-id>/task.yaml`)
to actual execution: submit an approved task through the real `Scheduler`/`Store`/
runtime path the HTTP daemon already used internally, but from the `escape-ai` CLI;
retry a failed attempt without a fresh `submit` failing on a duplicate task id;
promote a transient failure-checkpoint candidate into the durable, committable
`.esc-ai/workflows/active/<task-id>/checkpoint.yaml`; and give a single place to see
active work and its status across every registered repository.

## Deliverables

- [x] `esc_orchestrator/store.py`: new `task_attempts` table plus
      `get_latest_run_for_task`, `record_attempt`, `get_attempt_count`.
      `submit()` changed from a plain `INSERT` into the `tasks` table to an upsert
      (`ON CONFLICT(id) DO UPDATE ...`) — retrying the same task id now always
      succeeds and always creates a fresh `runs` row, instead of raising
      `sqlite3.IntegrityError` on the second attempt.
- [x] `esc_orchestrator/escape_ai_cli.py` — new "Execution and resumption" section:
      `default_workspace`/`default_adapter`/`default_policy` (placeholder, read-only
      policy — see below); `active_work` (cross-repository view: latest run status,
      attempt count, checkpoint presence, for every task known to the `Store`);
      `execute_task` (resolves a task's stored contracts, submits through
      `Scheduler`, waits for completion, records the attempt); `checkpoint_candidate`
      /`promote_checkpoint` (read the transient `.esc-ai/runs/<run-id>/checkpoint.yaml`
      candidate; write it as the durable
      `.esc-ai/workflows/active/<task-id>/checkpoint.yaml` via
      `esc_exec.checkpoints.create_checkpoint`/`update_checkpoint`).
      `onboarding_process_metrics`/`planning_process_metrics` call the execution
      framework's new `process_metrics()` with timestamps already sitting in the
      `Store`, so `repository status`/`plan status` now report a `process_metrics`
      field alongside their existing output.
      Non-interactive `task run <repo> <task> [--yes]` and
      `task promote-checkpoint <repo> <task> [--yes]`, plus `resume [--json]`, and a
      new interactive "Resume active work" menu item (choice "3") that browses
      active tasks, executes one, and offers checkpoint promotion in one flow rather
      than as separate menu items.
      Every write path (`--yes` on `task run`, `--yes` on `promote-checkpoint`)
      follows the same preview-then-explicit-apply boundary already established for
      onboarding/planning: without `--yes` nothing is submitted or written.
- [x] `tests/test_escape_ai_cli.py`: new `ExecutionRenderingTests` (5 pure rendering
      tests) and `ExecutionAndResumptionTests` (4 tests using fake runtimes injected
      in place of a real OpenCode adapter — `_FakeFailingRuntime`,
      `_FakeSucceedingRuntime`), most importantly
      `test_execute_retry_promote_and_resume_view`, which drives the full cycle:
      submit → fail → checkpoint candidate appears → promote → retry → succeed →
      resume view reflects the final state.
- [x] `README.md`: documented `resume`, `task run`, `task promote-checkpoint`, and
      the preview-then-`--yes` pattern they follow, including the explicit
      placeholder-configuration caveat.

## Verification

31/31 tests pass (22 prior + 9 new). Beyond the automated suite, ran the actual
`escape_ai_cli` module (not just its tests) end to end against a fresh temp Gradle
repository: `repository add/analyze/answer/apply` → `plan draft/answer/apply` →
`resume` (shows the task with 0 attempts, never run) → `task run` without `--yes`
(prints the preview, including the "not a finished permission story" placeholder
note, and exits without submitting anything).

Because no OpenCode server was running in this environment (confirmed via
`curl -m 2 http://127.0.0.1:4097/project/current` returning no response), the
`--yes` execution path and `promote-checkpoint --yes` were additionally verified
directly against `execute_task`/`active_work`/`checkpoint_candidate`/
`promote_checkpoint` in a standalone script (not the test framework) using an
injected fake runtime that fails once and then succeeds — confirming, outside of
`unittest`, that: a failed attempt is recorded and surfaced in `resume`'s view with
`checkpoint_present: True`; the candidate promotes to a real
`.esc-ai/workflows/active/<task-id>/checkpoint.yaml` file; retrying the same task id
does not raise `IntegrityError`; and a successful second attempt clears
`checkpoint_present` and updates the resume view's status and attempt count. This
is real verification of the CLI's own functions, but it is not the same as
confirming behavior against a live OpenCode server — that remains unverified in
this environment.

## Deliberately not built yet ("Configure system" placeholder)

`default_workspace`/`default_adapter`/`default_policy` are hard-coded placeholders:
a workspace rooted at the repository path, the `OpenCodeAdapter`/
`DEFAULT_OPENCODE_SERVER`, and a conservative read-only policy. There is no real
"Configure system" flow yet to let a repository declare its own workspace, choose an
adapter, or grant broader permissions — `task run`'s preview output says this
explicitly ("placeholder defaults ... pending real Configure system support ...
this is not a finished permission story") rather than presenting it as finished
configuration. Building a real configuration flow is future work, not part of this
phase's scope.
