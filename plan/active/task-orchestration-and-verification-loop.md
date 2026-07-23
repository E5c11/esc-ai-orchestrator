# Task Orchestration and Verification Loop — Plan

**Status:** Active
**Date:** 2026-07-19
**Objective:** Close the loop between "escape-ai prepares a bounded task" and "escape-ai
prepares the next one" — a real task-level dependency graph (not a straight line),
independent verification of what an agent actually did (not its own self-report), and
automatic advancement to the next unblocked task when a run is verified clean.

## Why this is a separate plan

Same reasoning as the other standalone plans in this directory: a distinct concern from
both the main plan's phase sequence and from `native-cli-provider-adapters.md`. That
plan is about *which agent executes a task*. This plan is about *what happens before and
after* — how tasks relate to each other across a graph, and how escape-ai knows a task
actually finished cleanly. The two compose: this loop should work the same way
regardless of which adapter a given task uses.

## Why this plan exists

Phase 10 validated that escape-ai can prepare a correct, bounded task package (task
context, verification plan, dependency-aware component scoping) for existing, new, and
cross-repository cases. What it did *not* validate, and what turned out — on inspection —
to not exist at all, is the loop that would make a multi-task initiative actually run
itself: dependency-aware parallel dispatch, independent confirmation that a task
succeeded, and automatic advancement to whatever it was blocking. Today, every one of
those steps is a human running one CLI command at a time and eyeballing the result.

Three concrete things were checked directly against the code (not assumed) while
designing this:

1. **Cross-repository task dependencies are a straight line, not a graph.**
   `generate_multi_repository_workflow`/`apply_plan` chain each declared repository's
   task to exactly the one before it in declared order. There is no branching — two
   independent tasks in the same initiative cannot be expressed as running in parallel,
   only as an arbitrary sequence, even when nothing about the actual work requires that
   ordering. Component-level dependency graphs (`esc-dependencies.json`) already support
   real branching and transitive impact analysis (proven at real scale — 171 edges
   across 28 components in `AMPM`) — task-level dependencies don't have the equivalent.
2. **Nothing executes or independently checks a verification gate.** A repository-wide
   grep for `subprocess`/`Popen`/anything that runs a shell command turned up nothing in
   either `esc-ai-execution-framework` or `esc-ai-orchestrator`. `build_verification_plan`
   produces a *description* of which gate commands apply to a task; the agent is the one
   that actually runs them (via its own execute permission). Whether a task "passed" is,
   today, whatever the agent says it is.
3. **There's already a well-designed, working, but unused answer to the trust
   problem.** `esc_exec/reporting.py::summarize_junit` is a real, tested function that
   deterministically parses an actual JUnit XML report (something the test runner
   itself produced, not something the agent wrote) into a `verification-summary.json`
   whose schema is explicitly `"source_format": "junit-xml"` — a compact summary of a
   real artifact, not a narrative claim. Nothing calls it as part of a run's lifecycle.
   It's a solved sub-problem sitting disconnected from the thing it would solve.

## Two execution modes this loop needs to serve differently

Realized while discussing this: "run several tasks at once" splits into two genuinely
different problems that shouldn't be solved the same way.

- **Interactive, in-session fan-out** — the user is already inside a live agent session
  (Claude Code, or any CLI with an equivalent subagent mechanism) and wants several
  *independent* prepared tasks running concurrently, watched, in that one session. This
  needs no new scheduling infrastructure at all: escape-ai's job is only to prepare
  correct bounded inputs (task-context, verification-plan) for each task; the host
  session's own native subagent/Task mechanism does the actual spawning and parallelism.
  Nothing about `Scheduler` is relevant here.
- **Headless, unattended, durable execution** — no live session watching; a task might
  fail, sit blocked for hours or days, and need resuming later, possibly by a different
  session or a different adapter entirely, possibly kicked off from a script or CI. This
  is what `Scheduler`/`Store`/checkpoints already exist for, and it's the one that
  actually needs the graph-advancement and independent-verification work below, because
  there's no human in the loop to notice a task finished and decide what runs next.

This plan's design targets the headless mode specifically. The interactive mode already
works today, for free, and needs nothing built for it beyond what already exists
(bounded task-context generation).

## Design

### 1. A real task-level dependency graph

Extend the initiative/task shape so a task's `depends_on` can express a genuine graph,
not just "the previous entry in this list" — mirroring the same
nodes/edges/topological-resolution shape `esc-dependencies.json` and
`architecture_lookup.py::resolve_architecture_docs` already use successfully at the
component level. Concretely:

- `initiative.schema.yaml`'s `tasks[].depends_on` already accepts a list of
  `repository/task_id` references with a pattern constraint — the schema doesn't
  actually need to change. What needs to change is `generate_multi_repository_workflow`,
  which currently *ignores* any real graph structure and always chains linearly by
  declared order; it should instead accept and validate an arbitrary depends_on graph
  (already checks for cycles-via-declared-set membership; needs an actual topological
  validation, not just membership checking, to catch real cycles).
- A new `analyze_task_impact`-equivalent (mirroring `dependencies.py::analyze_impact`)
  that, given a completed task, returns which other tasks in the initiative are now
  unblocked (all of their `depends_on` entries verified complete) versus which remain
  blocked.

### 2. Independent, artifact-based verification — trust the artifact, not the agent

The resolution to "can we trust the agent, because there's no point running checks
twice": don't trust the agent's *narrative* about what happened, but don't re-run
anything either — mechanically verify the real artifact the check already produced.

**Resolved (2026-07-19), and corrected same day — must be provider-agnostic, not
adapter-specific.** An earlier draft of this section leaned on `adapter.yaml`'s
`capabilities` list (`events`, `tools`) to observe a gate command's exit code through
the adapter's own tool-execution log. That's OpenCode-specific plumbing, and using it
would mean re-deriving trust differently per adapter — exactly the coupling the dual
subscription/API-key design and the whole provider-adapter plan exist to avoid.

The actually provider-agnostic mechanism is simpler: **escape-ai's own
execution-framework code runs the verification gate command directly** — a plain
subprocess call, in the same workspace the agent just edited — independent of whichever
adapter/provider did the actual work:

- **Baseline, every gate:** escape-ai invokes the gate command itself after the agent
  reports the task done, and captures the real exit code directly. Zero = clean,
  nonzero = not. This works identically no matter which CLI did the editing, because it
  happens entirely outside the adapter, using the same command definitions
  `verification-plan.json` already declares.
- **Enhancement, test gates specifically:** additionally locate the JUnit XML that
  invocation produced and run it through `summarize_junit` (already exists, already
  tested) into a validated `verification-summary.json`, for richer detail (which test
  failed, counts) than a bare exit code gives you.
- Either way, the task's real status is *this independently-executed result*, never
  whatever the agent's own final message claims.
- **This is not "running checks twice" in the wasteful sense the original concern was
  about.** An agent may run tests internally as it iterates and self-corrects — that's
  its own business, not authoritative. There is exactly one authoritative execution of
  each gate, and it's always run by the trusted party (escape-ai), the same way
  regardless of adapter — not a duplicate of anything, since the agent's own internal
  runs were never the check that decided anything.

### 3. Automatic advancement on verified-clean

Once a run's independently-parsed result says clean:

- Use the task-impact analysis from (1) to find tasks now fully unblocked.
- In headless mode, automatically submit those tasks to the scheduler (respecting
  whatever adapter each declares, once `native-cli-provider-adapters.md`'s multi-adapter
  dispatch exists — until then, everything still goes through the one adapter that
  exists today) — **unless the active provider's usage is at or above 90% of its
  subscription/quota pool** (see `native-cli-provider-adapters.md`'s `usage` command),
  in which case newly-unblocked tasks are held, not dispatched. Already-running tasks
  are never interrupted — they run to completion regardless of usage crossing the
  threshold mid-flight. This resolves the "several parallel tasks colliding on the same
  cap" concern from open question 3 below: rather than race multiple tasks against a
  shared cap, the system simply stops accepting new work once close to it, draining to
  a clean point where a human can `switch` provider before anything resumes.
- If the parsed result says *not* clean, this is not a new failure path — it flows into
  the checkpoint mechanism that already exists (`checkpoint_document`,
  `promote_checkpoint`), just triggered by a verified result instead of an uncaught
  exception. Checkpoint promotion stays human-reviewed; only the "was this actually
  clean" determination stops being agent-self-reported.

## Non-goals

- Do not build a new execution engine for the interactive/in-session case — that's
  already solved by the host session's own subagent mechanism (see above). This plan is
  scoped to headless/unattended execution only.
- Do not make automatic advancement bypass human review of failures — a verified-clean
  result can advance automatically; a not-clean result still produces a checkpoint for a
  human to look at, exactly as today.
- Do not conflate this with the provider-adapter plan — this loop should work
  identically regardless of which adapter a task uses.

## Open questions

1. **Resolved 2026-07-19, corrected same day.** The mechanism must be provider-agnostic
   — see "trust the artifact, not the agent" above: escape-ai runs the gate command
   itself, directly, independent of whichever adapter did the editing. JUnit-XML
   parsing is an additive enhancement for test gates specifically. No per-gate-type
   bespoke artifact format needed for v1, and no dependence on any one adapter's own
   tool-execution/event capabilities.
2. **Resolved 2026-07-19 — concurrency shape.** A small, bounded worker pool (configurable,
   default 5 concurrent) inside the *same* `Scheduler`, not multiple
   separate `Scheduler` instances. `Store` already wraps every write in
   `with self.lock, self.connection:` — a `threading.Lock` that exists today but has
   never been exercised by more than one thread at once; this reuses it rather than
   redesigning `Store`'s concurrency model. A real implementation task should still
   include a concurrent-writes test to confirm that lock actually holds up under
   multiple workers, not just assume it from reading the code.
3. **Resolved 2026-07-19 — subscription rate-limit contention.** Once the active
   provider's usage reaches 90% of its subscription/quota pool, automatic advancement
   (section 3 above) stops *dispatching new* tasks — already-running tasks are never
   interrupted, they finish normally. This avoids racing several tasks against a shared
   cap at once, and drains the initiative to a clean point where a human can `switch`
   provider (see `native-cli-provider-adapters.md`) before anything else resumes. The
   `usage` command's colored warning (also decided there) is the visibility half of
   this; the dispatch-pause here is the actual safeguard.
4. **Cycle validation** in `generate_multi_repository_workflow` — currently only checks
   that a `depends_on` reference is a declared member of the initiative, not that the
   overall graph is acyclic. Needs the same kind of topological check
   `architecture_lookup.py::resolve_architecture_docs` already does for architecture
   documents.
5. **Resolved 2026-07-19 — trigger mechanism.** Event-driven, not polled: hook the
   "check the graph for newly-unblocked tasks" call directly into the same place
   `update_run` already records a run's final status, since that already fires exactly
   once, exactly when a result becomes known. A poll loop would add up to a full
   interval's worth of latency and re-check state that hasn't changed; the existing
   `Scheduler` shape is already reactive, so this extends it rather than adding a
   second, competing mechanism. **Explicitly deferred, not now:** a user-initiated
   on-demand "give me a status update" command is a reasonable future addition on top
   of this — a manual pull, not a background poll loop — but it's additive to the
   event-driven design, not a replacement for it, and isn't being built in this pass.

## Tasks

Each task below is phase-sized (one bounded objective, matching how the old
per-repository Wave/Phase docs were structured — an initiative's "Wave" maps to this
whole plan, each "Phase" maps to one task here), in `esc-ai-execution-framework` and
`esc-ai-orchestrator` unless noted. Listed in dependency order; independent ones are
marked as such.

1. ~~**Task-level dependency graph.**~~ Change `generate_multi_repository_workflow` to
   accept and validate an arbitrary `depends_on` graph (it currently only ever chains
   linearly by declared repository order regardless of what's passed) plus real
   topological cycle detection (today it only checks that a reference is a declared
   initiative member, not that the graph is acyclic — mirror
   `architecture_lookup.py::resolve_architecture_docs`'s approach). No dependencies.
   Done 2026-07-23 in `esc-ai-execution-framework`, no separate plan doc needed.
   **Correction found while implementing:** the "chains linearly by declared order"
   behavior does not actually live in `generate_multi_repository_workflow` itself —
   that function already threaded through whatever `depends_on` graph it was handed,
   arbitrary shape and all; the linear-chain behavior lives one layer up, in
   `esc-ai-orchestrator`'s `apply_plan` (`escape_ai_cli.py`), which today always
   *synthesizes* a straight `depends_on` chain before calling this function — a
   separate, later concern, not touched here. What this function actually lacked was
   cycle detection: its only `depends_on` check was declared-set membership. Also
   confirmed `architecture_lookup.py::resolve_architecture_docs` cannot be mirrored
   for cycle detection specifically — despite doing a DFS over a `requires` adjacency,
   it only tracks a single "visited" set to stop infinite recursion and never actually
   detects or reports a cycle (see its own `test_cycle_does_not_infinite_loop`, which
   asserts exactly that — no exception). Added `_find_dependency_cycle` (`planning.py`):
   DFS over the `depends_on` graph across every declared task, with the
   currently-on-this-path ("grey") set `resolve_architecture_docs` is missing,
   returning a human-readable trace (e.g. `"a/x -> b/y -> a/x"`) for the first cycle
   found. Wired into the existing "validate everything, then write" two-pass
   structure, so a cycle is reported alongside any other validation error and nothing
   is written to any repository if one exists. Tests (`tests/test_planning.py`): a
   two-task cycle, a self-referencing task, and — proving the graph really is
   arbitrary, not just chain-with-a-cycle-check — a genuine diamond (two independent
   tasks depending on the same upstream task, neither on the other) is accepted.
   Suite: 391 -> 394 passing. **Noted but explicitly out of scope:** `contracts.py`'s
   separate validator for the dormant `initiative` schema kind (no code currently
   produces a standalone document of that kind) has the identical membership-only gap
   — not fixed here since nothing consumes it yet; revisit only if/when something
   starts producing that document.
2. ~~**Task-impact analysis.**~~ New function mirroring `dependencies.py::analyze_impact`
   that, given a completed task, returns which other tasks in the initiative are now
   fully unblocked. Depends on (1). Scoped out into its own plan, done 2026-07-23:
   [`task-impact-analysis.md`](../done/task-impact-analysis.md).
3. ~~**Provider-agnostic gate execution.**~~ escape-ai's own code invokes a
   verification gate's command directly (subprocess, in the same workspace the agent
   edited) and captures the real exit code — independent of whichever adapter did the
   editing. No dependencies; can proceed in parallel with (1)/(2). Scoped out into its
   own plan, done 2026-07-21:
   [`provider-agnostic-gate-execution.md`](../done/provider-agnostic-gate-execution.md).
4. ~~**JUnit-XML enhancement.**~~ Where a gate's `source_format` is `junit-xml`,
   locate the report the invocation in (3) produced and run it through
   `summarize_junit` (already exists, already tested) into a validated
   `verification-summary.json`. Depends on (3). Scoped out into its own plan, done
   2026-07-21:
   [`junit-report-verification-enhancement.md`](../done/junit-report-verification-enhancement.md).
5. ~~**Independently-verified result replaces agent self-report.**~~ Wire (3)/(4)'s
   result into `Store`/`Scheduler` as the run's authoritative status, instead of
   trusting whatever the agent's own final message claims. Depends on (3). Done
   2026-07-23 in `esc-ai-orchestrator`, no separate plan doc needed (design held up
   against the code unchanged): `esc_orchestrator/runtime.py`'s `_AdapterRuntime.execute`
   now calls `execute_verification_plan` right after the adapter returns, alongside the
   `verification-plan.json` it already wrote; `esc_orchestrator/scheduler.py`'s `_work`
   reads the resulting `verification-result.json` out of the run's `output_path` and
   only marks a run `"succeeded"` when its top-level `status == "passed"` — a
   `"failed"` verification result now marks the run `"failed"` (with a summary of
   which gate/check failed as the run's `error`) even though the adapter itself never
   raised. If no `verification-result.json` exists at all (a fake/legacy runtime that
   doesn't produce one), Scheduler falls back to trusting the runtime's own
   return-vs-raise signal, same as before this task — a deliberate compatibility
   fallback, not a gap, since every real `_AdapterRuntime`-based runtime always
   produces one now. Tests: `tests/test_orchestrator.py` — one exercising the real
   `build_verification_plan` -> `execute_verification_plan` pipeline through
   `_AdapterRuntime.execute` with a fake adapter and a real onboarded fixture repo
   (`test_adapter_runtime_executes_verification_plan_independently`), two exercising
   Scheduler's status derivation via `FakeRuntime`'s new optional
   `verification_status` (`test_verification_result_passed_still_succeeds`,
   `test_verification_result_failed_marks_run_failed_not_agent_self_report`). Full
   suite: 123 -> 126 passing. Does not yet build a checkpoint for a verification-
   failed run (no exception was raised, so the existing except-block checkpoint path
   never fires) — that's task 6, deliberately not pulled forward here.
6. ~~**Not-clean path into existing checkpoints.**~~ Route a not-clean verified result
   into the existing `checkpoint_document`/`promote_checkpoint` mechanism, same as an
   uncaught exception does today — this is a new trigger for an existing path, not a
   new mechanism. Depends on (5). Done 2026-07-23 in `esc-ai-orchestrator`, no separate
   plan doc needed: `esc_orchestrator/scheduler.py`'s `_work` now calls a new shared
   `_write_checkpoint_candidate` helper — factored out of the checkpoint-building code
   the uncaught-exception branch already had, so both triggers build the same shape of
   candidate through one path — when a run's verification result is not-clean, passing
   `output` (the adapter's own real run_dir, already on disk with
   `verification-plan.json`/`verification-result.json` in it) as the candidate
   directory and one blocker string per failing gate/check (e.g.
   `"final.test (failed, exit_code=1)"`), instead of the single opaque exception
   message the except-branch uses. `checkpoint_candidate`/`promote_checkpoint`/
   `active_work` in `escape_ai_cli.py` needed zero changes — they already worked from
   `run["status"] == "failed"` plus a `checkpoint.yaml` under `output_path`, exactly
   what this trigger now also produces. Tests: `tests/test_orchestrator.py`'s
   verification-failure test now also asserts the checkpoint's shape; a new CLI-level
   end-to-end test in `tests/test_escape_ai_cli.py`
   (`test_verification_failure_produces_promotable_checkpoint`) drives a real
   onboarded repository through `execute_task` with a runtime that reports success but
   writes a not-clean `verification-result.json`, then through
   `checkpoint_candidate`/`promote_checkpoint`/`active_work`, mirroring the existing
   uncaught-exception version of that same test
   (`test_execute_retry_promote_and_resume_view`). Full suite: 126 -> 127 passing.
7. ~~**Event-driven automatic advancement.**~~ Hook "check (2) for newly-unblocked
   tasks and submit them" directly into the same place `update_run` already records
   a verified run's final status. Depends on (2) and (5). Done 2026-07-23 in
   `esc-ai-orchestrator`, no separate plan doc needed: `Scheduler._advance` (new,
   `scheduler.py`), called from `_work` right after `update_run(run_id,
   "succeeded", ...)` — never from the `"failed"` branches, verification-failed or
   uncaught-exception alike, matching the design's "only verified-clean advances"
   rule — calls `analyze_task_impact(store, registry, task_id)` and, for each
   `newly_unblocked` entry, submits it via the existing `self.submit(...)`
   (feeding back into the same queue the same worker thread drains, so a chain of
   unblocked tasks runs out one at a time without any new concurrency). Reuses the
   just-completed task's own `adapter`/`policy` contracts verbatim rather than
   resolving a fresh provider — this Scheduler has exactly one `runtime`, so
   those two sub-dicts are already proven compatible with it; per-adapter
   dispatch across multiple runtimes stays deferred to
   `native-cli-provider-adapters.md`, unaffected by this task. A newly-unblocked
   task with **any** existing `Store` row at all (running, queued, or previously
   failed and awaiting checkpoint review) is skipped, never silently resubmitted
   — `execute_task` doesn't enforce `depends_on`, so a task can legitimately have
   already been run out of order by a human, and auto-advancement must not step
   on that. A bug inside `_advance` is caught and swallowed at its call site so
   it can never retroactively flip an already-recorded success back to a
   failure. **Real bug found and fixed in task 2's `analyze_task_impact` along
   the way:** `completed_set` was built purely from graph-discovered nodes, so a
   completed task whose own `task.yaml` wasn't part of the disk scan (e.g. never
   written to disk in a test double, or any future case where a completed task's
   directory stops being discoverable) would leave its dependents incorrectly
   "still blocked" on it forever, contradicting the very premise of "given a
   completed task." Fixed by unioning `completed_node` into `completed_set`
   unconditionally — the caller telling us a task is complete is authoritative,
   not contingent on a coincidental disk-scan artifact. Tests:
   `tests/test_initiative.py` gained a regression test for that fix;
   `tests/test_orchestrator.py` gained two `Scheduler`-level tests (auto-submits
   a newly-unblocked task; does not resubmit one with existing Store history);
   `tests/test_escape_ai_cli.py`'s cross-repository `task impact` test was
   updated to assert the real end-to-end behavior — repo-b's task now runs
   automatically inside repo-a's own `execute_task` call, with no second
   explicit submission, confirmed via both `store.get_task` and the now-empty
   `task impact` result afterward. Suite: 136 -> 139 passing.
8. **Bounded worker pool in `Scheduler`.** Extend from one worker thread to a small
   configurable pool (default 5), plus a concurrent-writes test confirming `Store`'s
   existing `threading.Lock` actually holds up under multiple workers (it's never been
   exercised by more than one thread). Independent of (1)-(7); needed before (7) is
   useful for genuinely-parallel unblocked tasks, but can be built any time.
9. **90%-usage dispatch pause.** In (7)'s automatic-advancement path, hold newly-
   unblocked tasks rather than dispatching them once the active provider's usage is at
   or above 90% (already-running tasks are never interrupted). **Blocked on
   `native-cli-provider-adapters.md`'s `usage` tracking existing at all** — that plan
   has no adapters built yet, so there is no usage number to check yet. Until that
   exists, (7) should simply always dispatch (no pause condition), and this task adds
   the pause once usage tracking is real. Do not stub a fake/hardcoded usage check to
   unblock this early — that would be worse than no check at all.

Tasks 1, 2, 3, 4, 5, 6, and 7 are done (see above) — the whole headless
dependency-graph-plus-verification loop this plan set out to build now actually
runs itself. Only task 8 (bounded worker pool, independent, can be built any time)
and task 9 (blocked on a different plan's `usage` tracking existing at all, don't
pull it forward) remain.
