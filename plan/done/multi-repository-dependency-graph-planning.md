# Multi-Repository Dependency Graph in Planning — Plan

**Status:** Implemented
**Date:** 2026-08-04
**Implemented:** 2026-08-04 -- `planning_questions()` (esc-ai-execution-framework,
`esc_exec/planning.py`) now emits a `depends_on` question per repository for a
multi-repository draft, carrying a `suggested` default (the previous repository in
declared order, empty for the first); `apply_plan`/`render_plan_draft`/
`render_plan_result` (esc-ai-orchestrator, `escape_ai_cli.py`) read and render it,
falling back to the old straight-chain default whenever the field or a specific
repository's entry is left unanswered, so existing callers are unaffected. Full
suites green in both repos (498 tests, esc-ai-execution-framework;
209 tests, esc-ai-orchestrator), including a new end-to-end test proving a real
branching (non-chain) graph writes correctly
(`test_multi_repository_plan_accepts_an_explicit_branching_dependency_graph`).
**Objective:** Let `plan draft`/`plan apply` accept a real `depends_on` graph across
a multi-repository initiative's tasks instead of always chaining every repository to
the one immediately before it in declared list order.

## Why this is a separate plan

`plan/active/escape-ai-improvements.md` finding #7 flagged this as "already known,
confirmed still current" but didn't design a fix — this doc is that fix. Distinct from
`plan/active/cohesive-system-integration-and-onboarding.md` Phase 7, which is marked
Complete for the typed-question planning mechanism this extends, not reopens.

## What we found

- The underlying primitive already does the hard part.
  `generate_multi_repository_workflow` (esc-ai-execution-framework,
  `esc_exec/planning.py:222`) accepts an arbitrary acyclic `depends_on` graph per
  task — branching, diamonds, independent subgraphs — with real cycle detection
  (`_find_dependency_cycle`). This is not a schema or write-path gap.
- The gap is entirely in the CLI's own question-asking. `apply_plan`
  (`esc_orchestrator/escape_ai_cli.py:945-1020`) hardcodes
  `task["depends_on"] = [previous_task_ref]`, locking every multi-repo plan into a
  straight chain matching `repositories` declared order. Its own docstring says this
  is deliberate ("rather than asking for a full dependency graph through the CLI"),
  not an oversight.
- `render_plan_result` already prints the resulting chain explicitly
  (`plan/done/run-outcome-surfacing.md` finding #7's display half) — only the input
  side remains unfixed.

## Design

- Add a `depends_on` typed planning question, one per repository in a multi-repo
  draft (skipped entirely for single-repo drafts, where no chain exists), following
  the exact pattern `planning_questions()` (`esc_exec/planning.py:23`) already uses
  for `components` — same question shape, `{"field": "depends_on", "repository":
  repository_id, "prompt": ...}`, so the interactive answer-collection loop
  (`run_planning_interactive`, `escape_ai_cli.py:1852-1866`) needs only one new
  `elif question["field"] == "depends_on"` branch that mirrors the existing
  `components` branch verbatim (parse comma-separated repository ids into a list,
  keyed by `question["repository"]` under `answers["depends_on"]`) — not a new
  collection mechanism.
  - Prompt: "Which other repositories in this initiative must complete before
    `<repository_id>`? (comma-separated repository ids, or blank for none)"
  - Suggested default shown inline: the repository immediately before it in
    declared order — preserves today's behavior for anyone who just accepts the
    default, so this is additive, not a breaking change to existing muscle memory.
    For the first repository in declared order there is no previous entry, so the
    suggested default is simply empty (no dependencies) — no special-cased prompt
    copy needed, the same prompt renders correctly whether or not a suggestion
    exists.
- `apply_plan` reads a new `answers.get("depends_on", {})` dict (`repository_id ->
  list[repository_id]`) instead of unconditionally computing `previous_task_ref`,
  translating each declared repository-id dependency into the `repository_id/task_id`
  reference `generate_multi_repository_workflow` expects. A repository missing from
  the answer defaults to the previous-repository suggestion — a caller passing an
  `answers.json` that predates this field reproduces today's exact chain behavior, no
  silent behavior change for existing non-interactive/scripted callers. This follows
  the exact same untyped-JSON convention every other answer field already uses
  (`components`/`scope_boundary`/`completion_conditions`/`rollout_needs` have no
  schema file either — `apply_plan` just reads keys via `.get()`), so no new schema
  file is needed for `depends_on`.
- Validation is already handled for free: `generate_multi_repository_workflow`'s
  existing cycle check and "depends_on references a declared repository/task_id"
  check fire on whatever graph this produces, unchanged. A repository declared to
  depend on itself is caught the same way — `_find_dependency_cycle`'s `on_path`
  check flags a 1-node cycle exactly like an N-node one, no special-casing needed.
- `render_plan_draft` gains a line surfacing each repository's suggested dependency
  default before answers are given, mirroring how routed-component suggestions are
  already shown.

## Non-goals

- No visual DAG rendering (ASCII graph, etc.) — `render_plan_result`'s existing
  "chain" print generalizes to "A depends on B, C" lines per repository; a graphical
  view is Phase 9 (richer interfaces) territory if ever needed.
- No change to `generate_multi_repository_workflow`, the initiative schema, or cycle
  detection — all already correct for an arbitrary graph.
- No automatic dependency inference from component-level dependency graphs
  (`esc-dependencies.json`) — a declared-order default is enough; inferring real
  inter-repo build/API dependencies automatically is a separate, unproven idea (no
  cross-repo dependency data exists today) and shouldn't block this fix.

## Open questions

None remaining. Both questions from the initial draft are resolved by checking the
actual answer-parsing code (`_dispatch_plan`, `apply_plan`,
`run_planning_interactive`) rather than guessing:

1. **Resolved.** `_dispatch_plan`'s `answer`/`apply` handlers
   (`escape_ai_cli.py:2260-2289`) load `answers.json` as raw, untyped JSON and pass
   it straight through to `apply_plan`, which reads specific keys via `.get()` — no
   schema file backs `components`, `scope_boundary`, `completion_conditions`, or
   `rollout_needs` today, and `depends_on` doesn't need one either. Schema: a
   top-level `"depends_on": {"repository-id": ["other-repository-id", ...]}` key,
   same convention as every other field.
2. **Resolved.** No distinct prompt needed — see Design above. A repository with no
   previous entry in declared order simply gets an empty suggested default; the
   prompt copy and answer-collection code are identical either way.
