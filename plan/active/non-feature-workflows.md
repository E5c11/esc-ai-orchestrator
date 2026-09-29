# Plan / Investigate / Refactor / Document workflows (BLA-44) — Plan

**Status:** Implemented (2026-09-29); not yet merged
**Date:** 2026-09-29
**Objective:** Give `plan`, `investigate`, `refactor` and `document` real gates behind their verbs, so each
procedure in BLA-48's contract is enforced rather than merely listed. After BLA-42/43 these are the remaining
verbs whose stages are "(not yet enforced)" or entirely unavailable. VISION.md: "a verb that routes to a
procedure with no real gates behind it is cosmetic".

## What we found (in code, not assumed)

1. **Read-only is not enforced anywhere.** A run's policy is `resolve_default_policy(registry)` — the global
   default `standard-autonomous` (edit/execute/network all allowed) — whatever the work type. An `investigate`
   task can edit files today. The `investigate`/`plan` procedures merely *omit* an `implement` stage; nothing
   stops the agent editing anyway.
2. **The disposable-worktree safety net covers only Claude Code.** `esc_exec.codex_adapter` and
   `opencode_adapter` operate on the live checkout. So "cannot accidentally modify repository state" cannot rest
   on isolation; it needs a check that does not depend on the adapter.
3. **A correct read-only run is reported as a failure.** No diff -> `succeeded-no-changes` -> `escape-ai task
   run` exits 1, and dependents never advance. For read-only work "nothing changed" is the *expected* outcome.
4. **The product of `investigate`/`plan` is never shown.** The agent's final message is saved as
   `summary.json`, but the report prints status and paths only.
5. `plan` and `document` are not in the engine's `WORK_TYPES` at all (the verbs currently exit 2, "BLA-44").
6. Verification (`execute_verification_plan`) already runs real subprocess gates and the scheduler already fails a
   run whose result is not `passed`. `baseline_capture` and `grounding_check` have no implementation.
7. `task.work_type` is now recorded in `task.yaml` (BLA-43), so the runtime can enforce per work type.

## Decisions

- **Enforce in the runtime, not the CLI.** `_AdapterRuntime.execute` sees every submitted task (CLI, `api.py`
  POST /tasks, auto-advance), so a CLI-only guard could be bypassed. The CLI only makes the preview truthful.
- **Read-only = policy forced down + backstop.** (a) The effective policy for `plan`/`investigation` is the
  configured policy with edit, execute and network forced to `deny` (a mapping bug in an adapter then fails
  closed). Bash can write, so `execute` is denied too: an investigation that must *run* the tests is a `fix`
  reproduction or an opt-in follow-up, not silently allowed. (b) Independent of adapter and policy, the runtime
  snapshots the repository (`git` HEAD + status, ignoring escape-ai's own `.esc-ai/runs|worktrees`) before and
  after; any difference fails the run and names the files. It never auto-reverts (that would itself be a
  destructive edit); it reports.
- **Read-only success is `succeeded`.** The "no changes" status is for work that was supposed to change something.
- **The agent's summary is the output**, surfaced in the run result and printed as "Findings" (investigate) or
  "Plan" (plan). It lives in the run record, not in the repository, because the repository must not change.
- **refactor**: `baseline_capture` runs the task's verification plan against the untouched checkout *before*
  dispatch and records it; the run is blocked if there is no check to run (nothing could show behaviour was
  preserved) or the baseline is not green (a refactor cannot be judged against a broken baseline; use `fix`).
  After the agent, the existing `verify` gate decides. Cost: the suite runs twice; stated in the help.
- **document**: `grounding_check` (a) allows only documentation files to change and (b) requires every
  path-like reference in the changed docs to resolve in the repository. Honest limit: it proves references exist,
  not that the prose is accurate. Restricting `document` to docs also bounds what an agent can do under it.
- New engine work types `plan` and `document` (schemas, contract enums); CLI verbs map to them 1:1.

## Delivery slices (each committed and green on its own)

1. Engine work types + `read_only` module (policy, snapshot, violation check) + per-work-type prompt lines.
2. Runtime enforcement + read-only success semantics + findings in the report + verbs `plan`/`document` routed.
3. `refactor`: `baseline_capture`.
4. `document`: `grounding_check`.

## Non-goals

- AI-suggested root causes for `fix` (reuses `investigate`'s output, a follow-up), an opt-in "investigate may run
  read-only commands", non-git repositories (the read-only backstop needs git; it is skipped with a recorded note),
  proving documentation prose is true.

## Acceptance (from BLA-44)

- Each of the four is available through the CLI. Read-only workflows cannot modify the repository: forced policy
  plus a post-run check that fails the run. Each has documented inputs, behaviour and outputs (`--help`, docs).

## Outcome

Engine (branch `feat/bla-44-workflows`): `7a24cd6` work types + read-only primitives, `9db398b`-era fix aside,
the verification-tree fix, `baseline_capture`, `grounding_check`. Orchestrator (same branch name): runtime
enforcement, verification-tree fix, refactor, document. 641 engine tests, 330 orchestrator tests; 3 + 4 import
contracts kept. **No stage of any procedure is "(not yet enforced)" any more** (a test asserts it; the marker
mechanism is kept and tested with a synthetic stage).

| Verb | What is now enforced |
|---|---|
| `plan`, `investigate` | policy forced to read-only at the runtime; before/after repository comparison (content-hashed, HEAD included) fails the run and names files, never reverts; success is `succeeded`; the agent's findings/plan are printed |
| `refactor` | before dispatch: a runnable check must exist and pass, none may already fail, checkout must be clean; after: verify runs the same checks in the changed tree |
| `document` | only documentation may change; every file/dir the changed docs point at must exist (link targets and unmistakable inline-code paths; fences, URLs, hostnames, prose like `read/write` skipped) |

### A defect found on the way, fixed separately (`fix(verification)`)

Verification ran in the **live checkout** while Claude Code edited a **worktree**, so the gates tested code the agent
never touched. Reproduced with a real worktree and real checks: an agent that broke the check was reported
`succeeded`; one that fixed it, `failed`. Fixed by verifying the tree the run's own `run.json` says it edited (plan
still built from the live checkout so an agent cannot rewrite its gates). **This changes behaviour for every work type:
runs that "passed" before because the changes were never tested may now fail, correctly.** Regression tests fail with
the fix reverted.

### Also found

- `execute_verification_plan` reports `passed` when nothing ran. `refactor` now fails closed on that; **`fix`,
  `feature` and `job` still do not** (a plan with no runnable check "verifies"). Follow-up ticket recommended.
- The adapters' `ask` permission is treated as deny, so read-only denies `execute` outright (a shell can write).

### Not done / not verified

- **Nothing here was run against a real provider.** Every test uses a fake adapter against a real git repository and the
  real runtime/scheduler. Whether Claude/Codex/OpenCode honour the reduced policy is exactly what the before/after
  backstop is for, but it has not been observed live.
- `plan` output is only shown when the task is run (`task run`), not in `observe a run` (`run_detail` does not surface
  `summary.json`).
- An `investigate` that needs to *run* something (reproduce a failure) cannot; `fix` owns reproduction. An opt-in
  read-only-commands mode is a possible follow-up.
- The read-only backstop and the refactor clean-checkout guard need git; without it the read-only check is skipped and
  the report says so.
- `fix`'s root cause is still operator-supplied; feeding it from an `investigate` run is not wired.
- Nothing checks that a `refactor` agent did not edit a test to make it pass (the prompt says not to; the help says
  to review the diff).
- Verifying inside a worktree assumes the project builds there (`worktree_inherit` exists for gitignored local files).
