# Parallel Task Dispatch — Plan

**Status:** Implemented
**Date:** 2026-07-23
**Objective:** Extend `headless-backdoor-mode.md` so that, whenever an initiative has
more than one currently-unblocked task, `BACKDOOR.md` dispatches all of them at once
via the AI session's own parallel/subagent mechanism, instead of one task at a time.

## Why this is a separate plan

A follow-on request against the already-shipped, already-`plan/done/`
`headless-backdoor-mode.md` — not a design flaw in that plan, an intentionally new
scope on top of it. Reopening a closed "Implemented" doc to grow it would blur what
was actually delivered when; a small new doc for a small new addition matches every
other standalone plan in this directory.

## What was checked, not assumed

- **The engine for this already existed, mostly.** `Scheduler`'s bounded worker pool
  (task 8 of `task-orchestration-and-verification-loop.md`) already runs up to 5
  tasks concurrently *within one process* once they're all queued — and task 7's
  auto-advancement already feeds newly-unblocked dependents back into that same
  queue automatically. The actual gap was narrower than "build parallel execution":
  it was specifically that nothing ever discovers and submits *multiple independent
  root tasks* at once — `execute_task`/`task run` only ever takes one task ID, and
  nothing scans an initiative for "everything ready right now."
- **A real, load-bearing bug was found and fixed while building this.**
  `Store.submit_if_new` (task 8) was proven race-safe under concurrent *threads*
  but was never exercised across separate *processes* — which genuinely matters now,
  since the design here relies on dispatching several `task run --yes` invocations
  as separate OS processes (real subagents, not Python threads) racing on the same
  SQLite file. A standalone repro script (20 attempts x 8 real subprocesses) showed
  a real failure: with the existing `with self.lock, self.connection:` pattern,
  SQLite's default *deferred* transaction lets a bare `SELECT` run without taking
  any lock, so two separate processes could both see "no row" and race to `INSERT`
  — `sqlite3.IntegrityError: UNIQUE constraint failed`, not a clean `None`, on
  2 of 20 attempts. `self.lock` (a `threading.RLock`) never could have caught this
  — it only serializes threads within one process; it has no reach across processes
  at all. Fixed by making `submit_if_new` open an explicit `BEGIN IMMEDIATE`
  transaction instead of relying on the implicit deferred one — this takes SQLite's
  write lock *before* the `SELECT` runs, so a second process's transaction blocks
  (via the sqlite3 module's default 5-second busy timeout) until the first commits,
  and then correctly sees the now-existing row. Re-ran the same repro against the
  fix: 20/20 attempts, exactly one winner every time, zero errors. General
  cross-process writes (not the check-then-act gate specifically) were separately
  confirmed already fine without any change, via a 4-process x 200-write stress
  script — Python's sqlite3 module's default `timeout=5.0` already provides real
  retry behavior for ordinary lock contention; it was specifically the *check-then-
  insert* pattern's lock-free `SELECT` that was the actual gap.
- **Auto-advancement already generalizes correctly across processes, once
  `submit_if_new` is fixed — no polling loop needed.** Worked through the
  cross-process interleaving by hand: if a downstream task depends on two tasks
  dispatched as separate parallel subagents, whichever subagent's process happens
  to finish *last* will, via its own `Scheduler._advance` call, correctly see the
  other's already-committed completion in `Store` (since `Store` is the single
  shared source of truth read fresh on every check, not cached per-process) and
  submit the downstream task itself. This composes correctly for arbitrarily deep
  chains, not just direct pairs, since each completion re-triggers the same check.
  `BACKDOOR.md` therefore only needs to say "dispatch the initial ready set," not
  "keep polling and re-dispatching."
- **The standard `plan apply` flow can't currently produce genuine parallelism at
  all.** `apply_plan`'s multi-repository branch (`escape_ai_cli.py`) always
  synthesizes a straight chain across the declared `repositories` list order,
  regardless of whether the work actually requires that ordering — confirmed this
  is unchanged since task 1 of `task-orchestration-and-verification-loop.md`
  explicitly deferred fixing it ("a separate, later concern, not touched here").
  This means `plan ready` will typically return exactly one entry immediately after
  a fresh `plan apply`, even when the underlying work has no real ordering
  constraint. Documented as a known limitation in `BACKDOOR.md` with a manual
  workaround (hand-edit a task's `depends_on` to drop an artificial dependency)
  rather than silently fixing `apply_plan`'s planning-answer shape in the same
  pass — that's a genuinely separate, larger UX/design decision (new
  plan-answers.json fields, new tests, docs) that deserves its own explicit go-ahead,
  not scope creep riding along with a Store-layer bug fix.

## Design

### 1. `find_ready_tasks(store, registry, initiative_id) -> list[str]`

Added to `esc_orchestrator/initiative.py`, alongside `analyze_task_impact` (shares
its `_discover_initiative_graph` graph-reconstruction and a newly-factored-out
`_is_task_complete` helper, previously duplicated as an inline closure inside
`analyze_task_impact` — factored out once it had a second real call site). Returns
every node whose `depends_on` is a subset of the completed set *and* that has no
`Store` row at all yet (`store.get_task(task_id) is None`) — deliberately the same
"never touch anything with existing history" rule `Scheduler._advance` already
applies, so a task a human already ran out of order is never re-offered as "ready."

Unlike `analyze_task_impact`, this needs no "just completed" anchor — it's a
point-in-time snapshot of "what's dispatchable right now," the natural query for an
initiative's initial parallel wave (which may have several independent roots) as
well as a manual "did anything get missed" recheck.

### 2. `escape-ai plan ready <initiative-id>`

A new read-only subcommand under the existing `plan` command family (alongside
`draft`/`answer`/`apply`/`status`), printing the JSON list. No side effects, safe to
run repeatedly.

### 3. `Store.submit_if_new`: `BEGIN IMMEDIATE` instead of the implicit deferred
transaction

See "What was checked" above for the bug and the fix. `self.lock` (the
thread-level guard) is kept alongside it, unchanged — the two failure modes are
independent (Python API-level thread safety vs. SQLite transaction-level process
safety) and both are real.

### 4. `BACKDOOR.md` Step 4 rewritten

From "run the first task" (implying exactly one) to: run `plan ready`, dispatch
every entry via the session's own parallel/subagent mechanism (not a serial loop),
and rely on auto-advancement to correctly finish the rest without further polling
— with the `apply_plan` straight-chain limitation flagged explicitly, including the
manual `depends_on`-editing workaround.

## Non-goals

- Do not change `apply_plan`'s planning-answer shape to accept a real dependency
  graph from the CLI — a real, separate, larger design decision (see above),
  explicitly not bundled into this pass.
- Do not build a new execution engine, worker pool, or scheduling concept — this
  entirely reuses task 7/8's already-shipped engine; the only new capability is
  *discovering* the initial parallel set and making cross-process dispatch of it
  actually safe.
- Do not add a `--force`/override for the `apply_plan` straight-chain limitation
  beyond the documented manual `depends_on` edit — that's a real, sufficient
  workaround for now; revisit only if it proves too much friction in practice.

## Tasks

1. ~~`find_ready_tasks` + `_is_task_complete` factor-out~~ — done, 5 new unit tests
   in `tests/test_initiative.py` (independent roots ready; blocked task not ready;
   already-submitted task not re-offered; a dependent becomes ready once its
   dependency completes; a task in an unrelated initiative not reported). Suite:
   146 -> 151 passing.
2. ~~`escape-ai plan ready` CLI command~~ — done, exercised end to end (real
   two-repository plan, checked ready-before and empty-after) as an extension of
   `tests/test_escape_ai_cli.py::test_task_impact_and_auto_advancement_for_cross_repository_task`.
3. ~~`Store.submit_if_new` cross-process fix~~ — done. New permanent regression test
   `tests/test_orchestrator.py::test_submit_if_new_is_race_safe_across_processes`
   (real subprocesses, 5 attempts x 6 processes each) alongside the existing
   thread-level one. Suite: 151 -> 152 passing.
4. ~~`BACKDOOR.md` Step 4 rewrite~~ — done, including the `apply_plan` limitation
   note and manual workaround.

Depends on `headless-backdoor-mode.md` (done) and, transitively, task 7/8 of
`task-orchestration-and-verification-loop.md` (done).

## Status: complete (2026-07-23)

`esc-ai-orchestrator`: 146 -> 152 tests passing.
