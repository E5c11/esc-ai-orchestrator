# Run-Outcome Surfacing: No-Op Success and Dependency Chains — Plan

**Status:** Proposed
**Date:** 2026-08-03
**Objective:** Two cases where escape-ai's own output already implies a fact the CLI
never states outright, forcing a human to read raw JSON/YAML by hand to recover it —
a `plan apply` that silently locks in a straight-chain task ordering, and a `task run`
that reports `succeeded` for a run that wrote no code at all. Distilled from
`plan/done/escape-ai-improvements.md` findings #7 and #9.

## Why this is a separate plan

Both are "surface a fact the tool already holds, in its own primary output" gaps,
same shape as `cli-discoverability.md` but for *run/plan output* rather than *input
resolution* — kept as its own doc rather than folded into that one since the touched
subsystems (`Scheduler._work`, `apply_plan`/`render_plan_result`) are unrelated to
task-ID/question resolution. Distinct from `pre-flight-doctor-and-gate-
prerequisites.md`: that plan is about catching environment gaps *before* dispatch;
this plan is about correctly interpreting and reporting what already happened
*after* a run completes.

## What we found

### #9 — a bigger issue than just display: no-op "success" also auto-advances dependents

`Scheduler._work` (`esc_orchestrator/scheduler.py:113`) is the one place a run's
final status gets decided:

```python
else:
    self.store.update_run(run_id, "succeeded", str(output))
    try:
        self._advance(task_id)
    except Exception:
        pass
```

There is no check here for whether the run actually produced a diff — `succeeded`
is stamped purely because verification passed and no permission was denied, whether
or not the agent wrote a single line of code. Two consequences, not one:

1. **Display**: `checkpoint_candidate` (`escape_ai_cli.py:692`) already knows how to
   tell the two cases apart after the fact — it reads
   `run_document["bindings"]["worktree"]["kept"]` and synthesizes a
   `worktree_merge_only` checkpoint candidate for a succeeded run *with* a kept
   (non-empty-diff) worktree. A succeeded run whose worktree was *not* kept (empty
   diff) hits none of `checkpoint_candidate`'s branches and falls through to `raise
   ValueError("no checkpoint candidate found...")` — so today, the only way to
   notice a no-op success is to read `run.json`'s `bindings.worktree.kept` field
   directly, exactly as the dogfooding finding describes.
2. **Correctness**: `_advance(task_id)` runs unconditionally after every `succeeded`
   stamp — `task-orchestration-and-verification-loop.md`'s automatic-advancement
   feature. A no-op succeeded run therefore doesn't just *look* like a completed
   task; it actively unblocks and submits dependent tasks whose declared
   `depends_on` assumed real work had landed. This is worse than the original
   finding's framing ("burns an attempt for nothing") — a chained multi-repo
   initiative could have its second repository's task dispatched against a first
   repository that never actually got the change it depends on.

### #7 — `apply_plan` already computes the chain, `render_plan_result` doesn't print it

`apply_plan` (`escape_ai_cli.py:785`) builds `task["depends_on"] = [previous_task_ref]`
for every repository after the first, in exactly the declared `repositories` list
order — the full chain is fully known at the moment `apply_plan` returns.
`render_plan_result` (`escape_ai_cli.py:221`) only prints, per repository, the
list of file paths written — never the `depends_on` value those files just encoded.

## Design

### 1. Distinguish a no-op "succeeded" at the source, not just at display time

In `Scheduler._work`, before stamping `"succeeded"`, check whether the run actually
produced a change — `run.json`'s `bindings.worktree.kept` (already computed by the
adapter, per finding #9's own discovery) is the existing signal; a run without
worktree isolation falls back to a real `git diff --quiet` check against the
workspace, if one is available. If no change was produced:

- Stamp a distinct status, `succeeded-no-changes`, instead of `succeeded` — a new,
  additive value in the same status field (`running`/`queued`/`succeeded`/`failed`/
  `waiting-approval` today), not a repurposing of an existing one, so every existing
  `status == "succeeded"` check elsewhere in the codebase keeps meaning "real
  completed work" without needing to also check the worktree field itself.
- Skip the `self._advance(task_id)` call entirely for this status — this is the part
  that fixes the correctness issue above, not just the display one. A no-op run must
  never unblock a dependent task.
- Let `checkpoint_candidate` synthesize a candidate for this case the same way it
  already does for the kept-worktree case (design already exists at
  `escape_ai_cli.py:708-722`) — `"remaining": ["This run produced no changes and may
  need clarification or a different approach — review the run's own summary/artifact
  before deciding whether to retry."]` — reusing the existing synthesized-candidate
  mechanism (see `pre-flight-doctor-and-gate-prerequisites.md`'s "what we found" for
  the sibling precedent of reusing an existing checkpoint path rather than building
  a new one).

Separately (the dogfooding doc's own secondary suggestion): the instruction bundle
handed to the agent for a `--yes`/full-autonomy dispatch should say explicitly that
no human will see a clarifying question mid-run, so the agent should make the
reasonable autonomous call and document it, rather than stopping to ask. This is a
wording change to the instruction-bundle template (`esc_exec`'s
`build_instruction_bundle`), not a schema/status change — worth doing alongside the
status fix above since it addresses the same finding's root behavior (the agent
choosing to ask instead of act), not just its symptom (the ambiguous status).

### 2. `plan apply`'s output prints the dependency chain it just wrote

Extend `render_plan_result` to print, for a multi-repository plan, the resolved
chain it just built — reusing `apply_plan`'s own `previous_task_ref`/`depends_on`
values already computed, not re-deriving them:

```
Planned. Files written:
  ampm-backend:
    .esc-ai/workflows/active/user-identity-spring-ampm-backend/task.yaml
  ampm-contracts:
    .esc-ai/workflows/active/user-identity-spring-ampm-contracts/task.yaml

Dependency chain: ampm-backend -> ampm-contracts -> ampm-kmp
```

Single-repository plans print nothing extra (no chain exists). This is purely a
rendering addition — `apply_plan`'s task-construction logic is unchanged.

## Non-goals

- Building real dependency-graph branching for multi-repo plans (letting a plan
  declare something other than a straight chain). Finding #7 explicitly says this is
  "already a known limitation" and the suggested fix here is only about *visibility*
  of the chain that gets built today, not changing how it's built. A branching
  dependency model, if ever pursued, is its own separate plan.
- A general "did this run do what it claimed" semantic verifier beyond "did the
  worktree diff end up empty." `succeeded-no-changes` is a narrow, mechanical
  signal (no diff at all), not an attempt to detect a run that made changes but
  didn't actually address the task's objective — that's a different, much harder
  problem this plan doesn't attempt.
- Retrying a `succeeded-no-changes` task automatically. It becomes a checkpoint
  candidate for human review, same as any other blocked/needs-attention run; whether
  to auto-retry is a separate policy decision, not designed here.

## Open questions

1. For a run with no worktree isolation at all (today's default, per
   `pre-flight-consent-and-bounded-autonomy.md`'s finding that worktree isolation
   isn't universally wired in yet) — is a `git diff --quiet` against the live
   workspace a safe, cheap check to add at this exact point in `Scheduler._work`, or
   does it need the workspace path threaded through in a way it isn't today? Not
   verified against current code; needs a look at what `runtime.execute`'s return
   value (`output`, a `Path`) actually gives access to before implementation.
2. Whether `succeeded-no-changes` should count as "success" or "failure" for CLI
   exit-code purposes (`escape_ai_cli.py:2069`'s `return 0 if result["status"] ==
   "succeeded" else 1`) — leaning toward non-zero (it needs human attention, same as
   a real failure), but not decided.
