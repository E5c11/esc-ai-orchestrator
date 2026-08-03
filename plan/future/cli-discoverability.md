# CLI Discoverability: Task IDs and Pending Questions — Plan

**Status:** Proposed
**Date:** 2026-08-03
**Objective:** Two small, mechanical CLI gaps where escape-ai already holds the exact
data a user needs to recover from a mistake or answer a pending question, but prints
only a count or a flat "not found" instead of the data itself. Distilled from
`plan/done/escape-ai-improvements.md` findings #1 and #2.

## Why this is a separate plan

Both findings are the same shape — "the tool already computed this, it just isn't
printed" — but touch unrelated code paths (task-ID resolution in `execute_task` vs.
plan-draft rendering in `render_plan_draft`), so they don't belong inside either of
the other two distilled plans (`pre-flight-doctor-and-gate-prerequisites.md`,
`run-outcome-surfacing.md`), which are both about *runtime* environment/status gaps,
not input discoverability. Small enough that both fit in one doc rather than two.

## What we found

- `execute_task` (`esc_orchestrator/escape_ai_cli.py:669`) resolves a task path
  directly as `.esc-ai/workflows/active/<task_id>/task.yaml` and raises
  `ValueError(f"no task.yaml found for \`{task_id}\` in \`{repository_id}\`; plan
  apply first")` on a miss — no listing of what *does* exist under
  `.esc-ai/workflows/active/` for that repository, even though the sibling
  directories are sitting right there on disk at the moment this error is raised.
- `render_plan_draft` (`escape_ai_cli.py:206`) already has `draft["questions"]` — the
  real list of pending `semantic_questions` — in memory at render time. It only ever
  prints `len(draft["questions"])`. There is no `--json` flag anywhere on the `plan`
  subcommand group (`plan draft`/`plan answer`/`plan apply`/`plan status`/`plan
  ready`), unlike `resume`, which already has one.

## Design

### 1. Unknown task ID lists real candidates instead of a flat "not found"

In `execute_task`, on a missing `task_path`, list the sibling directory names under
`repository_path / ".esc-ai" / "workflows" / "active"` before raising, and include
them in the error:

```
INVALID    no task.yaml found for `user-identity-spring` in `ampm-backend`
           did you mean: user-identity-spring-ampm-backend, referral-premium-spring-ampm-backend?
```

Simple substring/prefix match against the existing directory names (the multi-repo
convention is always `<initiative-id>-<repository-id>`, so a prefix match against the
given `task_id` covers the documented case directly) — no fuzzy-matching library
needed for the first cut. If there are no candidates at all (empty directory, or
truly nonexistent repository), fall back to today's plain message unchanged.

### 2. `plan draft`/`plan status` gain `--json`, printing the real questions

Add `--json` to `plan draft` and `plan status` (mirroring `resume --json`, the one
existing precedent in the CLI), which prints `draft["questions"]`/the stored
`semantic_questions` array verbatim — the same array `repository analyze --json`
already prints today, per finding #2's own comparison. No new schema: this is
purely "also expose, via `--json`, data that already exists in the draft record,"
not a new question format to design.

## Non-goals

- A general fuzzy-matching/typo-correction library for every CLI argument. Scoped to
  task-ID resolution specifically, where the exact set of valid values is always
  cheaply enumerable from a directory listing at the moment of failure.
- Redesigning the pending-question schema itself (`semantic_questions`' shape,
  `components`/`scope_boundary`/`completion_conditions`/`rollout_needs`). This plan
  only exposes what already exists via `--json`; the schema itself is untouched.
- Adding `--json` to every remaining subcommand as a blanket policy change. Scoped to
  `plan draft`/`plan status` specifically, where the underlying gap (documented only
  in `BACKDOOR.md`, not discoverable from `--help` or the CLI's own output) was
  concretely hit. Extend to other subcommands only if a real gap surfaces the same
  way.

## Open questions

1. Whether the task-ID suggestion list should be scoped to the given
   `repository_id` only (as sketched above) or also check across *other* registered
   repositories in case the user targeted the wrong repository entirely — leaning
   toward repository-scoped only for the first cut, since that's exactly what the
   real dogfooding case hit, but not decided.
