# Form-Driven Planning Conversation — Plan

**Status:** Active
**Date:** 2026-07-20
**Objective:** Replace planning's fragmented sequence of separate static questions
(work_type menu, objective prompt, components prompt, scope_boundary prompt,
completion_conditions prompt, rollout_needs prompt, optionally followed by a
separate scope-refinement AI conversation) with one conversation that converges
on the same real fields, ends itself once they're filled (not just when the human
notices and stops), and falls back to the existing plain questions for anything
still missing.

## Why this is a separate plan

Surfaced from two things in the same conversation: (1) a post-onboarding UX gap
(the CLI returns to the home menu with no next-step guidance -- fixed separately,
already shipped, this doc doesn't cover that); (2) discussing a proposed 6th
work-type menu option ("Not sure -- let's chat about it") surfaced that
`run_planning_conversation_interactive` (`plan/done/ai-conversation-primitive.md`'s
only consumer besides onboarding's new module-resolution turn) already exists for
scope refinement, but only triggers *after* `draft_plan` -- which itself needs
`work_type` already decided. Checked directly against the code, not assumed:
`draft_plan` (`escape_ai_cli.py`) never actually *uses* `work_type` for routing or
question-generation, it only validates membership in `WORK_TYPES` and stores it --
so the ordering constraint is real (schema requires it eventually) but not as
rigid as it first looked, and a single unified conversation covering work_type
through completion_conditions is architecturally possible without restructuring
`draft_plan`/`route_objective`.

## What was actually checked, not assumed

- `initiative.schema.yaml` requires `work_type` (enum, 5 values) and `objective`
  at the initiative level; `task-specification.schema.yaml` requires
  `completion_conditions` to be a **non-empty** list -- the one field that can't
  be silently skipped if the form ends up incomplete.
- `task_size` -- proposed mid-discussion, then dropped: nothing in any schema or
  downstream code reads a size signal today. Revisit only once
  `task-orchestration-and-verification-loop.md`'s worker-pool/concurrency work is
  real and could actually consume it -- not before.
- `language`/`platform` -- not new fields to *ask* about. Each component's
  manifest, from onboarding, already has `build.system` and resolved
  `architecture.profile_ids`/frameworks/targets. Asking the user to restate this
  would repeat the exact mistake this system's onboarding side already corrected
  (AI/human re-deriving what a deterministic source already answered). These
  become **context fed into the conversation's prompt**, not fields the form
  waits on.
- `compact_conversation` (`esc_exec/conversation.py`) already proves the general
  shape -- one more `run_turn` at the end of a free-form chat, structured JSON
  extraction, fails open to empty/partial on any parse failure. This plan's
  ending mechanism is a real variant of that, not a green-field design (see
  Design section 2 for the actual difference: per-turn, not end-of-conversation-
  only, because the system needs to notice completion itself, not rely on the
  human noticing and typing a blank line).

## Design

### 1. Reordered flow + the 6th work-type option

Current order: work-type menu -> objective -> initiative ID -> repositories ->
`draft_plan` -> *(single-repo only)* offer scope-refinement conversation ->
static components/scope_boundary/completion_conditions/rollout_needs questions ->
confirm.

New order: repositories -> objective -> initiative ID -> work-type menu, now six
options (`feature`/`fix`/`refactor`/`maintenance`/`investigation`/**"Not sure --
let's chat about it"**) -> `draft_plan` -> static questions for whatever the
conversation (if used) didn't already fill -> confirm.

- Picking one of the five fixed types: identical to today, zero AI involvement,
  zero cost -- the deterministic path is never worse than it already is.
- Picking "chat about it": starts the form-driven conversation (section 2),
  single-repository plans only -- same gating the existing scope-refinement
  conversation already uses, for the same reason (a form converging on one
  `work_type`/`scope_boundary` doesn't map cleanly onto per-repository
  differences in a multi-repo plan). Provider-gated the same "no wall at first
  run" way as everywhere else -- no provider connected, no wall, just no
  AI-assisted path; pick your best guess from the five instead.

### 2. The form and its completion mechanism

Fields: `work_type`, `objective` (refined from whatever was first typed),
`components`, `scope_boundary`, `completion_conditions`, `rollout_needs`. Every
one of these already exists as a field this system asks about somewhere --
nothing new added to any schema.

**Per-turn structured trailer, not a separate extraction call.** Doubling the
turn count (one turn to talk, a second turn to check completion) would double
however many `--resume` calls a conversation takes -- cheap individually, but
real at scale, and unnecessary. Instead, each turn's prompt instructs the model
to end its reply with a small delimited block (a fixed marker line, then fenced
JSON) reporting current known values for every form field -- `null`/omitted for
anything not yet determined. The code splits the response on that marker: the
human-visible portion is shown as the actual chat reply; the trailer is parsed
(fails open -- missing or malformed trailer means "nothing new confirmed," never
a crash, never blocks the conversation) to check whether every *required* field
(`work_type`, `objective`, `completion_conditions`) is now filled.

- **All required fields filled:** the loop stops itself -- prints what it has
  (`work_type: feature`, `objective: ...`, etc.) and asks "Use this, or keep
  talking?" rather than silently finalizing without a look. This is the
  behavior the human noticing and typing a blank line already gives today for
  the existing scope conversation; this plan makes the *system* notice too,
  instead of only the human.
- **Human wants to stop early anyway, fields incomplete:** blank line still
  ends the conversation immediately, exactly like today's convention -- never
  trap someone in a mandatory Q&A. Whatever the trailer captured is used;
  anything still missing falls through to section 3.
- **Scope drift:** the conversation's system prompt includes the stated
  objective and instructs the model to flag it explicitly if the discussion
  drifts past a scope_boundary that's already been stated -- this is the "keep
  it in line with the specific feature" idea from the original ask, mapped onto
  the *existing* `scope_boundary` field rather than a new boolean flag with
  nothing downstream to consume it.

### 3. Fallback for anything still missing

After the conversation ends (either way), any required field the trailer never
confirmed falls back to the exact existing plain question for that field --
same "AI suggestion missing, ask directly" discipline used everywhere else in
this system (onboarding's Tier 2, the groundable-fields registry). Optional
fields (`rollout_needs`) simply stay empty if never discussed, same as leaving
today's prompt blank.

## Non-goals

- Do not build `task_size` or any other field nothing downstream reads --
  dropped explicitly this session, revisit only when
  `task-orchestration-and-verification-loop.md` gives it a real consumer.
- Do not ask the user to restate `language`/`platform` -- already known from
  onboarding, fed in as context, never a question.
- Do not extend this to multi-repository plans -- same single-repo gating the
  existing scope conversation already has; a real multi-repo version is a
  future extension, not something to half-build here.
- Do not remove the deterministic five-item menu path -- "chat about it" is
  strictly additive, never the only way to pick a work type.
- Do not build a mandatory-completion trap -- blank line always ends the
  conversation immediately regardless of form state, same as today.

## Open questions

All three resolved 2026-07-20:

1. ~~Exact trailer format/marker~~ -- resolved: a literal `---FORM---` line,
   then a fenced JSON block with the form fields (`null`/omitted for anything
   not yet known). Code splits the response on the marker -- everything before
   is the human-visible reply, everything after is parsed. Still genuinely
   unverified until task 6 runs against the real `claude` CLI -- this is a
   concrete starting format to build and test against, not a proven one.
2. ~~Same trailer mode after "keep talking"~~ -- resolved: yes, throughout, no
   mode-switching logic.
3. ~~Does `components` need the conversation to ask about it~~ -- resolved: the
   conversation confirms/overrides the existing deterministic routing
   suggestion, never asks blind.

## Task breakdown (ordered by dependency)

1. Reorder `run_planning_interactive`'s question sequence (repositories ->
   objective -> initiative ID -> work-type menu). No dependencies -- pure
   reordering of existing prompts, no new behavior yet.
2. Add the 6th work-type menu option and its provider gate (same pattern
   `suggest_answers_via_provider`/the module-resolution turn already use).
   Depends on (1).
3. Per-turn structured trailer: prompt instruction, parser (fails open),
   marker format resolved (open question 1). New function in
   `esc_exec/conversation.py`, alongside `suggest_unresolved_components`/
   `suggest_groundable_answers_turn`. No dependencies -- can be built and
   tested independently of the CLI wiring.
4. Wire (3) into a new form-driven conversation loop, gated single-repo only,
   replacing the offer point for "chat about it" from (2). Depends on (2), (3).
5. Fallback to existing plain questions for any required field the trailer
   never confirmed. Depends on (4).
6. Live verification against the real `claude` CLI that the trailer format
   actually holds up turn over turn (matches how every other AI-facing piece
   in this codebase was verified before being trusted, not just unit-tested
   against a fake client). Depends on (4).
