# Provider-Agnostic Gate Execution — Plan

**Status:** Implemented
**Date:** 2026-07-21
**Objective:** escape-ai's own execution-framework code runs a verification gate's
command directly — a plain subprocess call, in the target repository's real
checkout — and captures the real exit code, independent of whichever adapter/provider
did the actual editing. This is the foundation `task-orchestration-and-verification-loop.md`'s
"trust the artifact, not the agent" design depends on; nothing downstream of it
(JUnit enrichment, authoritative run status, automatic advancement) can be built
without it existing first.

## Why this is a separate plan

This is task 3 of
[`task-orchestration-and-verification-loop.md`](task-orchestration-and-verification-loop.md),
called out there as having "everything it needs designed" and "no dependencies." It
is substantial enough on its own — workspace resolution, gate/check iteration
semantics, subprocess execution, output persistence, a manual invocation surface —
to warrant its own task breakdown and its own done/active lifecycle, rather than
staying a single line item in a plan that also covers an unrelated task-level
dependency graph (tasks 1/2 there). Splitting it out lets it ship independently of
that graph work, the same reasoning every other standalone plan doc in this
directory already uses.

## What was checked, not assumed

- **`build_verification_plan`** (`esc-ai-execution-framework/esc_exec/task_context.py:146-234`,
  schema at `schemas/verification-plan.schema.json`) already produces exactly 4 gates
  (`focused`/`component`/`impact`/`final`), each with a `status`
  (`ready`/`input-required`/`not-applicable`) and a `checks[]` list. Each check has an
  `id` and a `command` as an **argv array**, e.g. `["./gradlew", ":content:test"]` —
  not a shell string. There is no per-check working directory or `source_format`
  field; the plan is task/repository-scoped, and `source_format` only exists on the
  separate `verification-summary.json` output `summarize_junit` produces. Checks may
  carry `requires: [...]` for unresolved template params — a gate that isn't fully
  resolved has `status: "input-required"`, not `"ready"`.
- **Nothing executes a gate today.** The only `subprocess`/`Popen` usage anywhere in
  either repository is inside `esc_exec/claude_code_adapter.py` and
  `esc_exec/codex_adapter.py`, exclusively to invoke the AI CLI itself (auth status,
  prompt execution) — never a verification gate command. `contracts.py` validates the
  verification-plan's shape; it doesn't run anything. Whether a task "passed" is
  currently whatever the agent's own final message claims.
- **Today's run flow** (confirmed end to end): `execute_task()`
  (`esc_orchestrator/escape_ai_cli.py:551-579`) builds contracts and calls
  `Scheduler.submit()` (`esc_orchestrator/scheduler.py:18-65`); the worker thread marks
  the run `"running"`, calls `Runtime.execute()`, and on return marks it
  `"succeeded"` — with no independent check in between. `_AdapterRuntime.execute`
  (`esc_orchestrator/runtime.py:16-41`) resolves the repository's absolute path via
  `resolve_route(registry, "repositories", repository_id)`
  (`esc_exec/registry.py:133-155`), calls `build_verification_plan(...)`, writes
  `verification-plan.json`, then hands off to the adapter and returns whatever the
  adapter returns. `resolve_route` is the **only** place an absolute checkout path is
  resolved today; there is no separate "workspace root" concept to reuse or conflict
  with.
- **`summarize_junit`** (`esc-ai-execution-framework/esc_exec/reporting.py:33-110`)
  already exists, is already tested, and is already reachable manually via the
  execution framework's `report` CLI subcommand
  (`esc_exec/cli.py:323`) — but nothing calls it as part of a run's lifecycle. It
  expects a real JUnit XML file path; this plan does not touch it (see Non-goals) —
  locating and feeding it a report is explicitly the next task (task 4) in the parent
  plan.
- **The mechanism this plan must *not* use** — adapter `capabilities`
  (`schemas/adapter.schema.yaml`, e.g. `capabilities: [sessions, events, tools,
  permissions]`) — was already tried and explicitly rejected in the parent plan
  (`task-orchestration-and-verification-loop.md`'s "Resolved (2026-07-19), and
  corrected same day" note): observing a gate's exit code through an adapter's own
  tool-execution log is adapter-specific plumbing, and this needs to work identically
  regardless of which CLI edited the code.
- **Downstream consumers this must stay compatible with, not wire into:**
  `checkpoint_document()`/`promote_checkpoint()` (`esc_exec/checkpoints.py:28-39+`,
  `escape_ai_cli.py:598-614`) and the `task_attempts` table (`store.py:33,79-95`)
  already exist and are the eventual consumers of a verified-clean/not-clean result
  (parent plan's tasks 5/6). This plan produces the result; it does not wire it into
  `Store`/`Scheduler` as the authoritative run status — that wiring is task 5, which
  depends on this one but is not part of it.

## Design

### A new pure function: `execute_verification_plan`

Add to `esc-ai-execution-framework/esc_exec/`, alongside `build_verification_plan` and
`summarize_junit` (same module or a new sibling, e.g. `verification_execution.py`) —
it belongs in the execution framework, not the orchestrator, for the same reason
those two do: it's a pure function of a plan document and a filesystem path, testable
with a `tmp_path` fixture and fake commands, with no orchestrator/adapter/Scheduler
dependency.

```text
execute_verification_plan(plan: dict, workspace_root: Path, run_dir: Path) -> dict
```

- Iterates `plan["gates"]` in `plan["strategy"]["order"]`. A gate whose `status` is
  not `"ready"` is skipped and recorded with an explicit `"skipped"` result (not
  silently omitted, and not conflated with `"passed"`) — this reuses the vocabulary
  the schema already establishes rather than inventing a new one.
- For a `"ready"` gate, runs each check's `command` (the existing argv array — no
  shell string, no shell injection surface) via `subprocess.run(..., cwd=workspace_root,
  capture_output=True, text=True, timeout=<configurable>)`.
- Captures real exit code, wall-clock duration, and stdout/stderr **to files** under
  `run_dir/logs/<gate-id>-<check-id>.{stdout,stderr}.log` — not inlined into the
  result JSON, mirroring the existing `verification-summary.json` convention of
  referencing a `full_report` path rather than embedding potentially-large content.
- Respects `strategy.stop_on_failure` (currently always `true` per schema): the first
  nonzero exit code stops execution of any remaining gates/checks. Everything not yet
  attempted is recorded as `"not-run"`, distinct from `"skipped"` (not-ready) and
  `"failed"` (ran, nonzero exit).
- Returns (and the caller writes to `run_dir/verification-result.json`) a structured
  document: per gate, per check — `id`, `command`, `exit_code`, `status`
  (`passed`/`failed`/`skipped`/`not-run`/`error`), `duration_ms`, `stdout_path`,
  `stderr_path`. `"error"` covers the command itself failing to launch (bad
  executable, permission denied) — surfaced distinctly from a clean nonzero exit, not
  crashed past.
- Each check's result dict includes a `report_path: null` placeholder field — the
  extension point task 4 fills in once it exists, so this plan doesn't have to guess
  at JUnit report locations itself (see Non-goals).

### Manual invocation surface

Expose this as a new execution-framework CLI subcommand (mirrors the precedent
`report` already set for `summarize_junit`) — e.g. `esc-exec verify <plan-path>
<workspace-root> <run-dir>` — so it's independently runnable and scriptable before
task 5 wires it into `Scheduler` automatically. This deliberately does **not** touch
`escape_ai_cli.py`/`runtime.py`/`Scheduler` yet; automatic wiring into the run
lifecycle as the authoritative status is task 5's job, not this one's, so this plan
doesn't have to design two integration points (manual + automatic) before task 5's
design is settled.

## Non-goals

- Do not parse JUnit XML here — `summarize_junit` already exists and is untouched;
  locating and feeding it a real report path is task 4, which explicitly depends on
  this one.
- Do not wire this result into `Store.update_run`/`Scheduler` as the run's
  authoritative status — that's task 5. This plan only makes the independently-verified
  result exist and be inspectable; task 5 decides how it replaces agent self-report.
- Do not add a worker pool or any concurrency change — that's task 8, unrelated to
  this plan.
- Do not depend on any adapter's `capabilities`/events/tool-execution log — already
  tried and rejected (see above); this must work identically across every adapter.
- Do not invent new sandboxing, permission gating, or environment isolation beyond
  what already exists — the command runs with the same trust boundary as the
  `escape-ai`/`esc-exec` process itself; that's a pre-existing trust boundary, not a
  new one this plan needs to design.
- Do not re-run anything the agent already ran internally while iterating — this is
  not "run checks twice" in the wasteful sense; the agent's own internal test runs
  were never authoritative, so there's no duplicate work being eliminated, only a
  first real authoritative execution being added.

## Open questions

1. **Per-check timeout.** A hung gate command must not block a run forever. Lean:
   a configurable timeout with a generous default (exact number needs a real gate's
   typical runtime as evidence, not a guess); a timeout is recorded as `"error"`, not
   silently retried.
2. **stdout/stderr storage.** Lean: separate log files under `run_dir/logs/`, per
   above — gate output (e.g. a full test suite log) can be large, and the existing
   `verification-summary.json`'s `full_report` path convention already establishes
   the pattern to reuse rather than reinvent.
3. **Skip vocabulary.** Lean: reuse and extend the gate `status` vocabulary the
   schema already defines (`skipped` for not-ready gates, `not-run` for
   stopped-before-reached checks) rather than introducing an unrelated set of terms.
4. **Where the manual CLI command lives.** Lean: execution-framework CLI only for
   now (`esc-exec verify ...`), matching the `report` command's precedent; exposing
   it through the orchestrator's `escape-ai` CLI is deferred to task 5, so this plan
   isn't building two integration surfaces before that design is settled.
5. **Execution environment.** Lean: inherit the current process environment as-is —
   no new env-scrubbing or sandboxing invented here (see Non-goals); if that turns
   out to be insufficient, it's evidence for a future policy-driven change, not
   something to guess at now.

## Task breakdown (ordered by dependency)

1. ~~Define `schemas/verification-result.schema.json`~~ — done 2026-07-21 in
   `esc-ai-execution-framework`: schema (`schemas/verification-result.schema.json`),
   registered as a full contract kind in `esc_exec/contracts.py`
   (`CONTRACT_FORMATS`/`REQUIRED`/`ENUMS` plus a structural validation block mirroring
   `verification-plan`'s — fixed 4-gate order, per-check `status`/`exit_code`/
   `duration_ms` null-consistency, workspace-relative path checks, and a derived
   top-level `status` that must match the worst check outcome), a passing example
   (`examples/contracts/verification-result.json`), and regression tests in
   `tests/test_contracts.py` (`374 -> 377` tests passing). No `execute_verification_plan`
   code yet — this task was schema/contract only, per the Design section above.
2. ~~Implement `execute_verification_plan(plan, workspace_root, run_dir)`~~ — done
   2026-07-21 in `esc-ai-execution-framework/esc_exec/verification_execution.py`.
   Iterates `plan["strategy"]["order"]`; a gate whose `status != "ready"` is recorded
   `"skipped"`; a `"ready"` gate's checks run via real `subprocess.run(cwd=workspace_root,
   capture_output=True, text=True, timeout=...)`, capturing exit code and duration;
   stdout/stderr go to `run_dir/logs/<gate>-<check>.{stdout,stderr}.log` (workspace-
   relative paths, enforced by raising if `run_dir` isn't inside `workspace_root`);
   the first non-`"passed"` check sets a `stopped` flag that marks everything else
   `"not-run"`, across gates too, matching `stop_on_failure`. A launch failure
   (`OSError`, e.g. missing executable) or `subprocess.TimeoutExpired` both surface as
   check status `"error"` with a null `exit_code`, never an uncaught exception.
   Writes and returns a `verification-result` document.
3. ~~Unit tests with fake commands~~ — done 2026-07-21,
   `tests/test_verification_execution.py`, 6 tests: all-gates-pass (incl. schema
   validation of the output); a failing check stops remaining checks in its own gate
   and all later gates; an `input-required` gate is skipped and reported, not
   silently dropped; a missing executable surfaces as `"error"`; a real timeout
   (1s limit against a 5s sleep) surfaces as `"error"`; `run_dir` outside
   `workspace_root` raises `ValueError`. Full suite: 377 -> 383 passing.
4. ~~Add the manual CLI subcommand~~ — done 2026-07-21 as `esc-exec verification
   execute <repository> <plan> <run-dir> [--timeout-seconds N]`
   (`esc_exec/cli.py`), grouped under the existing `verification` command family
   alongside `profile`/`plan` rather than a new top-level `verify` command, matching
   how this CLI already organizes related subcommands. Exit code is `1` when the
   verified result is `"failed"`, `0` otherwise, mirroring the existing
   `architecture check` command's convention. Manually verified end-to-end against a
   real registered repository (`route add` -> `verification execute`), not just the
   unit tests.
5. ~~Confirm the per-check result shape leaves a clean extension point
   (`report_path`)~~ — done: every check result includes `report_path: null`, filled
   in by task 4, not guessed at here.

Depends on nothing else in either active plan. Unblocks tasks 4, 5, 6, and (via 5) 7
in `task-orchestration-and-verification-loop.md`.

## Status: complete (2026-07-21)

`esc-ai-execution-framework`: 377 -> 383 tests passing.
