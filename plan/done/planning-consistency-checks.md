# Planning Consistency Checks — Plan

**Status:** Implemented
**Date:** 2026-07-20
**Objective:** Two checks that run once a plan's `work_type`/`objective`/`scope_
boundary`/`completion_conditions`/`components` are known -- regardless of whether
they came from the deterministic menu/static-question path or the (proposed)
form-driven conversation -- so a plan is never finalized against a stale
assumption: (1) does the declared `work_type` still match what's actually being
described, and (2) does the target component's architecture framework actually
have real guidance for this objective, or is it silently carrying forward
generic profile_ids that aren't really relevant.

## Why this is a separate plan

Surfaced discussing `form-driven-planning-conversation.md`'s design, but neither
check is actually about the conversation mechanism -- both apply identically to a
plan drafted entirely through today's deterministic menu + static questions, no
AI chat involved at all. Same "distinct concern, own doc" reasoning as everywhere
else in this directory; folding these into the conversation doc would make that
doc's scope unclear (is it about conversation UX, or about plan correctness?)
and would wrongly imply the checks don't apply to the non-conversation path.

Also directly touches `plan/future/framework-contribution-and-extensibility.md`
(check 2 below) -- read alongside that doc, not duplicating it. That plan already
designs the **community/official** escalation (PR, CI dry-run, maintainer review,
`status: stub -> active`); it has no **local** stage at all. This plan's check 2
is what actually produces the artifact that stage would consume.

## What was actually checked, not assumed

- `planning.py::_architecture_doc_ids` (called from `generate_single_repository_
  workflow`) reads whatever `architecture.profile_ids` a component resolved
  **once, generically, at onboarding time** (via `suggest_profile_ids`, a static
  frameworks/targets -> doc-ID lookup table, `architecture_lookup.py`) and
  carries it into every task's doc verbatim. Nothing re-evaluates whether *this
  specific objective* is actually well-covered by those documents. Confirmed by
  reading the function, not assumed.
- `draft_plan` never uses `work_type` for routing/question-generation (same fact
  `form-driven-planning-conversation.md` already established) -- so `work_type`
  really is just a label carried forward unchecked against whatever gets
  described afterward, in both the menu path and (if built) the conversation path.
- `framework-contribution-and-extensibility.md` (read in full) designs a real,
  already-thought-through community contribution flow -- derives validity from
  content (an `overview` doc existing under `architectures/X/`) rather than a
  hand-maintained enum, uses the framework's existing `status: stub` for
  "exists, not yet reviewed," and a bounded CLI flow (`Which layer? -> existing
  or propose new? -> scaffold frontmatter -> local dry-run -> guided PR`) for
  turning a stub into a community contribution. This plan's local artifact
  (design section 2) is meant to feed directly into that flow's "propose new"
  step, not reinvent contribution mechanics.

## Design

### 1. Work-type drift check

Once `objective`/`scope_boundary`/`completion_conditions` are known (whichever
path produced them), one bounded AI check compares them against the declared
`work_type`: does this still read as a `fix` (or whatever was declared), or has
it grown into something else?

- **Flags, never silently reclassifies or blocks** (explicit decision this
  session): shows what changed -- e.g. "this now touches N components and
  describes new behavior, not a correction" -- and asks the user to either
  confirm the new type or explicitly keep the original and stay bounded to it.
  "Stay bounded to it" is a real, first-class answer, not just a formality --
  the user may deliberately want to keep something scoped as a fix even if it's
  tempting to grow, and the check's job is surfacing the drift, not enforcing a
  particular outcome.
- Runs once, after the objective/scope are settled -- not a per-turn check
  during a conversation (that's `form-driven-planning-conversation.md`'s own
  concern, if that plan is built; this check works identically whether or not
  it is, since it only needs the final settled fields, not the conversation
  that produced them).
- Provider-gated the same "no wall" way as everything else -- no provider
  connected, this check simply doesn't run; the plan proceeds with whatever
  work_type was declared, exactly as today.

### 2. Architecture-framework coverage check, with a local artifact

Once `components`/`objective` are known, check whether the involved
component(s)' resolved `architecture.profile_ids` actually look relevant to
this specific objective (not just "were resolved at all," which they already
were, generically, at onboarding) -- an AI-assisted check, since "is this
objective covered by this documentation" is a real judgment call a static
lookup table can't make (the lookup table only maps stack signals to docs, not
objectives to docs).

**When coverage looks thin or absent, per this session's "local -> community ->
official" framing:**

- **Local (this plan builds this):** offer to draft a **local architecture
  note** at `.esc-ai/local-architecture/<slug>.md`, never in the shared
  `esc-ai-architecture-framework` checkout. **Resolved 2026-07-20, grounded
  against the real schema** (`esc-ai-architecture-framework/schemas/
  document.yaml`, read directly, not assumed): reuse its exact frontmatter --
  `id`, `type`, `layer`, `platform`, `architecture`, `requires`, `related`,
  `tags`, `status: stub` -- so promoting one later through
  `framework-contribution-and-extensibility.md`'s flow is a lift-and-drop into
  the real repo's directory structure, not a rewrite. `id` doesn't need to
  satisfy that schema's real namespace-prefix-per-layer pattern while local --
  it's never merged into the framework's own index until an actual promotion
  happens, at which point it gets a real ID anyway. The task doc references it
  distinctly from real framework documents (a `local_architecture_notes` field
  alongside `architecture_doc_ids`, not merged into that list) so nothing
  downstream mistakes a locally-drafted, unreviewed note for a reviewed
  community document.
- **Community (already designed, not this plan):**
  `framework-contribution-and-extensibility.md`'s bounded contribution flow --
  this plan's local note is exactly the kind of thing that flow's "propose
  new" step should be able to pick up as a starting draft, rather than
  starting from a blank scaffold. That doc's own task list is unaffected by
  this plan; this is a forward reference, not new work assigned to it.
- **Official:** `status: stub -> active` promotion by a maintainer -- already
  fully designed in that doc, nothing new needed here.
- **Decision this session:** warn-and-proceed, not a hard block. Matches this
  system's existing discipline (stub/missing-document warnings during
  onboarding already work this way) of never fabricating guidance that doesn't
  exist, and never trapping a user who just wants to move forward with the
  closest available guidance while a note gets drafted alongside it.

## Non-goals

- Do not build the community/official contribution flow here -- that's
  `framework-contribution-and-extensibility.md`'s job, already designed. This
  plan only produces the local artifact that flow can later consume.
- Do not make either check a hard gate that blocks planning -- both are
  warn/flag-and-confirm, never silent auto-action and never a wall.
- Do not run either check per-turn during a conversation -- both operate on
  final settled fields, independent of whether a conversation or the static
  path produced them.
- Do not merge locally-drafted architecture notes into the shared framework
  checkout automatically, ever -- crossing from local to community is always
  an explicit human act, through the existing contribution flow.

## Open questions

All three resolved 2026-07-20:

1. ~~Exact schema/location for the local architecture note artifact~~ --
   resolved, see design section 2 above (grounded against the real
   `document.yaml` schema).
2. ~~Combined or separate calls for the two checks~~ -- resolved: **separate**,
   reversing this doc's original "leaning combined." Check 1 (work-type drift)
   is being built as its own standalone function, matching every other
   AI-suggestion function in this codebase being single-purpose -- retroactively
   coupling check 2 to that implementation before it's even verified would be
   the wrong order to build in.
3. ~~Should the conversation's per-turn trailer include informal early
   versions of these checks~~ -- resolved: no, they stay the single
   end-of-flow check designed here.

## Task breakdown

1. ~~Work-type drift check (design section 1)~~ -- done 2026-07-20, built by a
   background agent: `suggest_work_type_drift` in `esc_exec/claude_code_
   adapter.py` (mirrors `suggest_onboarding_answers`'s one-shot `client.ask()`
   shape, not session-based -- this is a single independent check, not part of
   a conversation; grants zero tools, since the judgment is text-only against
   the declared work_type/objective/scope_boundary/completion_conditions, no
   repository file access needed). Fails open on any subprocess/parse/schema
   failure; only ever returns one of the real `WORK_TYPES` values, never equal
   to the declared type, never claims drift without a non-empty `reasoning`
   string. Wired into `run_planning_interactive`
   (`confirm_work_type_drift_interactive`) after the existing
   `planning_questions` loop, before the final confirm prompt -- works against
   today's existing question order, no dependency on `form-driven-planning-
   conversation.md`'s reordering. Provider-gated the same "no wall" way as
   `suggest_answers_via_provider`. Never silently reclassifies: on drift, shows
   the reasoning and offers "reclassify" vs. "keep bounded to the original"
   as an explicit choice (any cancel/EOF defaults to keeping the original,
   matching this system's "no destructive silent default" discipline).
   **One correction from this doc's own framing:** since the check re-persists
   an updated `work_type` into the stored plan draft when the human accepts
   reclassification (`store.save_plan_draft`, required because `apply_plan`
   re-reads `work_type` from the stored draft, not from any in-memory
   variable -- confirmed by reading `apply_plan` directly, not assumed), it
   isn't actually gated to single-repository plans the way the existing scope-
   refinement conversation is. The check is text-only (objective/scope/
   completion_conditions are already repository-agnostic fields by the time
   they reach this point) and needs a repository path only as the subprocess's
   working directory, so it runs identically for single- and multi-repository
   plans -- there was no real reason to restrict it, and the plan's original
   design section 1 didn't call this out either way. 18 new tests across both
   repos (12 for `suggest_work_type_drift`'s fail-open/validation discipline
   in `esc-ai-execution-framework`, 6 for the interactive wiring and the
   confirm-helper's provider gating in `esc-ai-orchestrator`). Both full
   suites green throughout (execution-framework 300 -> 312, orchestrator
   103 -> 109).
2. ~~Architecture-framework coverage check + local note (design section
   2)~~ -- done 2026-07-20, built directly (not delegated), the second half
   picked up once (1)'s agent's edits landed (avoided the same files while it
   was actively editing them):
   - ~~Local architecture note writer~~ -- `esc_exec/local_architecture.py`
     (`write_local_architecture_note`, `read_local_architecture_note_
     frontmatter`, `list_local_architecture_notes`), frontmatter shape
     verified directly against `esc-ai-architecture-framework/schemas/
     document.yaml`, `status` always `"stub"`. 9 tests.
   - ~~Task-doc rendering~~ -- **corrected from this doc's original
     assumption**: checked how `architecture_doc_ids` itself is actually
     surfaced today (`esc_exec/planning.py::generate_single_repository_
     workflow`) before guessing -- it's README-only, never written into the
     schema-validated `task.yaml` at all. `local_architecture_notes` follows
     the exact same precedent: a new optional parameter through
     `generate_single_repository_workflow`/`generate_multi_repository_
     workflow` -> `_render_task_readme`, its own "## Local architecture notes
     (unreviewed)" section, never merged into "## Referenced architecture
     documents." No `task-specification.schema.yaml` change needed -- this
     doc's original task 3 assumption (a schema change) was wrong, corrected
     here rather than carried forward. 4 tests.
   - ~~Coverage-check function~~ -- `suggest_architecture_coverage_gap` in
     `esc_exec/claude_code_adapter.py`. Unlike `suggest_work_type_drift`, grants
     `Read`/`Glob`/`Grep` scoped to the architecture framework's own checkout --
     judging real documentation coverage benefits from reading actual document
     content, not just the index's id/tags/layer metadata. Zero resolved
     documents at all is treated as an uncovered gap without spending an AI
     call on it. Fails open to `covered=True`; a gap claim always requires both
     non-empty `reasoning` and `suggested_title`. 10 tests.
   - ~~Interactive wiring~~ -- `offer_local_architecture_note_interactive` in
     `escape_ai_cli.py`, run once per repository in `run_planning_interactive`
     (components/profile_ids genuinely differ per repository, unlike the
     work-type check which only needs one) after the work-type drift check,
     before the final apply confirm. Provider-gated the same "no wall" way.
     Threaded through `apply_plan` (new optional
     `local_architecture_notes_by_repo` parameter) into both the single- and
     multi-repository write paths. 6 tests (5 for the helper's gating/accept/
     decline behavior, 1 full interactive end-to-end through onboarding +
     planning verifying the note path lands in the real written README).

   Both full suites green throughout this task: execution-framework
   312 -> 334, orchestrator 109 -> 115.
