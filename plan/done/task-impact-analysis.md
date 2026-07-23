# Task-Impact Analysis — Plan

**Status:** Implemented
**Date:** 2026-07-23
**Objective:** Given a task that just completed, determine which other tasks
declared in the same initiative — across every registered repository, not just the
completed task's own — are now fully unblocked (every `depends_on` entry
satisfied), versus which remain blocked and on what. This is the thing task 7 of
`task-orchestration-and-verification-loop.md` ("event-driven automatic
advancement") will call once it exists; without it, a real `depends_on` graph
(task 1, done) has no way to actually drive dispatch.

## Why this is a separate plan

This is task 2 of
[`task-orchestration-and-verification-loop.md`](task-orchestration-and-verification-loop.md),
named there as "mirroring `dependencies.py::analyze_impact`." Once actually checked
against the code, that one-line description hid a real, non-obvious problem:
`analyze_impact` is self-contained within a single repository's own
`esc-dependencies.json`; this task's graph spans multiple repositories with no
equivalent pre-built artifact anywhere, and determining "completed" requires state
`analyze_impact`'s component-dependency equivalent never needed. That's enough of
a genuine design gap — not just an implementation detail — to warrant its own
doc, same reasoning as `provider-agnostic-gate-execution.md` and
`junit-report-verification-enhancement.md` before it.

## What was checked, not assumed

- **There is no single document that aggregates an initiative's task graph.**
  `generate_multi_repository_workflow` (task 1) writes each task's `depends_on`
  into that task's *own* `task.yaml` (`task.initiative.depends_on`), one file per
  repository — never a central `initiative.yaml` matching the dormant
  `initiative` contract schema (`schemas/initiative.schema.yaml`). Confirmed: no
  code anywhere in either repository produces a standalone document of that kind
  (grep across both repos, and task 1's own completion note already flagged this).
  Mirroring `analyze_impact`'s "load one pre-built graph" shape literally is not
  possible; the graph has to be reconstructed live.
- **`task.yaml`'s own `task["status"]` field is dead.** `generate_single_repository_workflow`
  hardcodes it to `"ready"` at write time
  (`esc-ai-execution-framework/esc_exec/planning.py`), and nothing anywhere in
  either repository ever updates it afterward (confirmed by a full-repo grep for
  writes to `task["status"]`/`task.yaml` after creation). Real completion state
  lives only in `esc-ai-orchestrator`'s `Store` (`tasks.status`, driven by
  `Store.update_run`, itself driven by task 5's independently-verified
  `verification-result.json`). This is why "given a completed task" cannot be
  answered by reading `task.yaml` alone.
- **`Store` cannot enumerate "all tasks in an initiative."** The `tasks` SQLite
  table has no `initiative_id` column and no unfiltered/broad `SELECT` anywhere in
  `store.py` — every lookup is by a single known `task_id`
  (`get_task`/`contracts`/`get_latest_run_for_task`). `Store` only knows about
  tasks that were actually `submit()`-ted at least once; a task written to disk by
  `apply_plan` but never executed has no row at all.
- **The existing precedent for "read `task.yaml` across every registered
  repository" is `escape_ai_cli.py::active_work`**
  (`esc_orchestrator/escape_ai_cli.py:519-548`): it iterates
  `read_registry(registry)["repositories"]`, scans each repo's
  `.esc-ai/workflows/active/*/task.yaml` via `esc_exec.yaml_io.load_yaml`, and
  cross-references `Store` per discovered `task_id` to enrich with run status.
  This plan's graph-discovery step follows that exact same shape rather than
  inventing a new one. `esc_exec/registry.py` has no dedicated "list all routes"
  function beyond `read_registry`'s plain dict access — `active_work` doesn't use
  one either, so this doesn't either.
- **`task["repository"]` is present on every task and matches its registry key by
  construction** (`generate_single_repository_workflow` always sets it to the
  `repository_id` the caller passed) — safe to use for building the
  `"repository/task_id"` node references `depends_on` entries already use, no
  extra lookup needed.
- **Layering: this cannot live in `esc-ai-execution-framework` next to
  `analyze_impact`.** It needs both a `registry: Path` (repository enumeration,
  already available from `esc_exec.registry`) *and* a `Store` (orchestrator-only,
  to know what's actually completed). `esc-ai-execution-framework` must not
  depend on `esc-ai-orchestrator` — the dependency direction in this codebase only
  ever goes the other way (confirmed: every existing cross-repo import is
  orchestrator → execution-framework, never the reverse). So this function belongs
  in `esc-ai-orchestrator`, even though it mirrors an execution-framework function.

## Design

### `analyze_task_impact(store, registry, completed_task_id, output=None) -> dict`

Added to a new module, `esc_orchestrator/initiative.py` (mirrors `dependencies.py`
being its own module in the execution framework, rather than folding this into
`store.py`/`scheduler.py`/the already-large `escape_ai_cli.py`).

- **Graph discovery** (`_discover_initiative_graph(registry, initiative_id)`):
  scans every registered repository's `.esc-ai/workflows/active/*/task.yaml`
  (`active_work`'s exact precedent), keeping only tasks whose
  `task.initiative.id` matches, returning `{"repository/task_id": [depends_on,
  ...]}`.
- **Completion**: a node counts as complete only if `store.get_task(task_id)`
  exists and its `status == "succeeded"` — never `task.yaml`'s own dead `status`
  field.
- **Scope of "impact"**: only *direct* dependents of `completed_task_id` can
  change unblocked/blocked status as a result of this one completion — a task
  blocked on a longer chain only becomes unblocked when its own immediate
  dependency later completes, a separate future call. A task unrelated to
  `completed_task_id` (not a direct dependent) is not reported at all, even if it
  happens to already be fully unblocked from some earlier event — this is
  impact-of-this-completion, not a full initiative status dump. (This is also why
  the traversal is a flat single-hop filter, not a BFS the way `analyze_impact`'s
  consumer-propagation is — "unblocked" doesn't propagate transitively in one
  event the way "affected by a changed component" does.)
- **A task with no `task.initiative` at all** (every single-repository
  `apply_plan` today — `generate_single_repository_workflow`'s single-repo branch
  never passes `initiative`, see task 1's completion note on the same asymmetry)
  is a de facto initiative-of-one: returns an empty result (`initiative_id: null,
  newly_unblocked: [], still_blocked: {}`), not an error.
- **Document shape**, mirroring `analyze_impact`'s `schema_version` + sorted
  string-list-field convention: `{schema_version, initiative_id, completed_task,
  newly_unblocked: [...], still_blocked: {task: [remaining deps...]}}`. `output:
  Path | None = None` for parity with `analyze_impact`'s own optional-disk-write
  signature.
- **Manual invocation surface**: `escape-ai task impact <task_id>` (grouped under
  the existing `task` command family alongside `run`/`promote-checkpoint`, not a
  new top-level command group, matching how this CLI organizes closely-related
  subcommands), printing the JSON document. `INVALID no such task: <id>` (exit 1)
  if the task was never submitted to `Store` — mirroring the `INVALID` convention
  other task-family commands already use for a bad reference.

## Non-goals

- Do not add a formal JSON-schema contract kind for this document. Every existing
  schema-validated contract in this codebase is a durable artifact something else
  reads back from disk later (`verification-plan`, `run-metrics`,
  `impact-analysis` itself, etc.); this document's primary consumer is task 7,
  in-process, not yet built. Adding a schema now — across two repositories, since
  schemas live in `esc-ai-execution-framework` but the producer lives in
  `esc-ai-orchestrator` — would be speculative. Revisit if/when something starts
  writing this to disk as a durable artifact.
- Do not touch the dormant `initiative` contract schema/its membership-only
  `depends_on` validator in `contracts.py` — already noted as explicitly
  out-of-scope by task 1, unchanged here; nothing produces a document of that kind.
- Do not update `task.yaml`'s own dead `status` field, or otherwise make it the
  source of truth for completion — `Store` already is, correctly; resurrecting a
  second, redundant status field is out of scope and not needed by anything here.
- Do not implement task 7's actual dispatch/submission logic — this plan only
  produces the *answer* to "what's now unblocked"; hooking it into
  `update_run`/`Scheduler` to actually submit newly-unblocked tasks is task 7,
  which depends on this one but is not part of it.

## Task breakdown

1. ~~`esc_orchestrator/initiative.py`: `_discover_initiative_graph` +
   `analyze_task_impact`~~ — done 2026-07-23. `_discover_initiative_graph` scans
   every registered repository's active `task.yaml`s filtered by
   `initiative.id`; `analyze_task_impact` resolves the completed task via
   `store.get_task`/`store.contracts` (raising `ValueError(f"no such task:
   {completed_task_id}")` if never submitted), short-circuits to an empty result
   when there's no `initiative`, otherwise computes `completed_set` via
   per-node `Store` lookups and filters direct dependents into
   `newly_unblocked`/`still_blocked`.
2. ~~Unit tests~~ — done 2026-07-23, `tests/test_initiative.py`, 8 tests: no
   initiative (empty result), a direct dependent becoming unblocked, a dependent
   still blocked on a second not-yet-complete task (with the remaining dependency
   named), an unrelated task in the same initiative not reported, an
   already-completed dependent not re-reported, a missing task raising, a
   cross-repository dependent becoming unblocked (two registered repositories),
   and the optional `output` path actually being written.
3. ~~`escape-ai task impact <task_id>` CLI command~~ — done 2026-07-23,
   `esc_orchestrator/escape_ai_cli.py` (`task_commands.add_parser("impact")` +
   `_dispatch_task`'s new branch), printing the JSON document or `INVALID` on an
   unknown task_id.
4. ~~CLI-level end-to-end test~~ — done 2026-07-23,
   `tests/test_escape_ai_cli.py::test_task_impact_reports_newly_unblocked_cross_repository_task`:
   two real onboarded repositories, a real multi-repository `plan
   draft`/`answer`/`apply`, `task impact` before repo-a's task has ever run
   (`INVALID no such task`), then after it actually executes and succeeds
   (`execute_task` with a fake succeeding runtime) — confirming repo-b's task is
   reported `newly_unblocked` through the real CLI, not just the
   `analyze_task_impact` unit level.

Depends on `task-orchestration-and-verification-loop.md` task 1 (done). Unblocks
task 7 there — task 7 is now blocked only on nothing content-wise, just not yet
built.

## Status: complete (2026-07-23)

`esc-ai-orchestrator`: 127 -> 136 tests passing.
