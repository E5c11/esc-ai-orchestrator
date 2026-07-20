# AI Conversation Primitive — Plan

**Status:** Implemented
**Date:** 2026-07-20
**Objective:** Give escape-ai a genuine multi-turn AI conversation capability -- not
another one-shot batched call -- usable wherever a bounded decision needs real
back-and-forth with a human before converging, rather than a single suggest-then-
confirm pass.

## Why this is a separate plan

Started as part of `scaffold-new-or-empty-repository.md` (a new/empty repo needs a
conversation about intent, since there's no source to ground a one-shot suggestion
in), but the user pointed out this isn't scaffold-specific: refining a feature/bug-fix
plan needs the same back-and-forth. Confirmed directly against the code, not assumed
-- `esc_exec/planning.py` (`route_objective`, `planning_questions`,
`generate_single_repository_workflow`) is entirely static keyword-matching Q&A today;
grepped `escape_ai_cli.py`'s planning flow for any Claude/Runtime/Adapter reference
and found none. Zero AI involvement anywhere in "Plan new work," same gap as
onboarding had before Tier 2.

This is the shared mechanism both consumers need -- the hard design work (session
continuity, turn-taking UX, when a conversation ends, what artifact it produces) is
common to both, not specific to either. Splitting it out avoids designing it twice,
slightly differently, in two different plan docs.

## The idea

- Uses the adapter's already-threaded `session_id`/`--resume` mechanism -- present in
  `ClaudeCodeAdapter.execute`'s signature since it was first built, but never actually
  exercised end-to-end. Each conversational turn is a real `claude -p --resume <id>`
  call, not a single batched suggestion the way Tier 2 (purpose/frameworks
  suggestions) works.
- Still bounded, matching this system's whole "bounded, evidence-driven, observable"
  discipline -- not open-ended chat. Each conversation is scoped to converging on one
  concrete artifact (a stack choice for scaffolding; a refined objective/scope/
  completion-conditions for planning), just reached via multiple turns instead of one.
- Ends in a concrete, reviewed proposal the human explicitly applies -- never
  auto-applied mid-conversation or at conversation end without a real confirm step,
  matching every other write path in this system (onboarding's proposal engine, plan
  drafts, checkpoints).
- **Resolved 2026-07-20, verified live.** Multiple turns do *not* re-pay the fixed
  overhead each time. A two-turn `--resume` test measured: turn 1 (fresh) --
  `cache_creation_input_tokens: 6141` (full cache-write cost paid); turn 2 (resumed)
  -- `cache_creation_input_tokens: 45` (almost nothing new), `cache_read_input_tokens:
  10620` (the prior turn's context reused from cache, not re-paid at full price).
  The model also genuinely remembered turn 1's content without being reminded,
  confirming real conversational continuity, not just cheap-but-stateless calls. A
  multi-turn conversation is affordable: pay the large fixed cost once on the first
  turn, then each subsequent turn is mostly cheap cache reads plus whatever's
  actually new.

### Ending a conversation -- two real thresholds, not a fabricated "quality" signal

A long-running conversation needs a stopping point before context grows unbounded.
There's no API-exposed signal for response quality/degradation to trigger on --
inventing one would be exactly the kind of ungrounded signal this project has
deliberately avoided everywhere else ("trust the artifact, not a guess"). The only
thing genuinely measurable each turn is context-window consumption (`cache_read_
input_tokens + cache_creation_input_tokens + input_tokens` against the model's real
`context_window`, both real fields the API already returns). So: two tiers of the
*same* real measurement, not two different mechanisms --

- **Soft threshold (first resort), ~60-70% of context window:** proactively offer to
  wrap up and finalize what's converged on so far, rather than continuing to grow the
  session.
- **Hard threshold (last resort), 90%:** forces a stop -- matches the exact pattern
  already established in `task-orchestration-and-verification-loop.md` for
  subscription-usage dispatch pausing, same threshold, same reasoning.
- Turn count is a cheap secondary nudge worth having alongside the percentage --
  a conversation with many small turns can be tiring for the *human* to follow even
  when token-cheap, a different kind of "getting unwieldy" than context math alone.

Either threshold should produce something durable, not just cut off -- see below.

### The missing artifact: conversation state before a task exists

Neither of this system's two existing "in-progress work" artifacts actually fits a
mid-conversation stopping point, confirmed by reading both schemas directly:

- `checkpoint.schema.yaml` has exactly the right `progress` shape (`completed`/
  `decisions`/`remaining`/`blockers`) but requires `task_id` and `objective` --
  it presupposes a task already exists and is mid-execution. A planning conversation
  often hasn't produced a task yet when it hits a threshold.
- `initiative.schema.yaml` requires `tasks` with at least one item -- it's the
  *converged* output of planning, not a mid-conversation snapshot.

This needs a new artifact -- reusing checkpoint's proven `progress` shape (already
validated, no reason to redesign it) rather than forcing it into either existing
schema. But a flat "everything so far" dump isn't enough either: there's a real
difference between what should persist indefinitely and what's just this
conversation's scratch work, and collapsing that distinction means either every future
session re-reads a growing pile of stale back-and-forth, or durable identity/direction
gets lost the moment a conversation is compacted away. So this is actually **two
artifacts, two lifecycles, not one**:

**`project_roadmap`** -- durable, repository-level, one per repository, *updated* in
place, never just appended to. This is what every future session gets seeded with by
default:

```yaml
schema_version: 1
project_roadmap:
  repository: string
  updated_at: date-time
  purpose: string           # "this repo is about XYZ" -- durable identity, rarely changes
  current_stage: string     # "X" -- where implementation actually stands right now
  direction: string         # "moving towards Y" -- where it's headed
  durable_decisions: [...]  # architecture/stack choices etc. that remain valid
                             # indefinitely -- not this conversation's tactical detail
```

**`conversation_summary`** -- ephemeral, per-conversation, the compaction artifact
itself. Its job is to feed an update into `project_roadmap`, not to be what future
sessions are seeded with directly:

```yaml
schema_version: 1
conversation_summary:
  id: string
  purpose: string        # "scaffold new repo X", "plan feature Y" -- what this is for
  status: in-progress | converged | abandoned
  updated_at: date-time
progress:
  completed: [...]
  decisions: [...]
  remaining: [...]
  open_questions: [...]  # undecided -- distinct from a task's "blockers" (implies
                          # stuck, needs a human), an open question is just not
                          # resolved yet
```

At a compaction threshold: the AI extracts whatever from `conversation_summary` is
actually durable (identity/stage/direction/decisions that outlive this conversation)
into a `project_roadmap` update, and the rest -- the tactical back-and-forth that got
there -- is left behind, not carried into the next seed. This *is* a legitimate use of
AI judgment, unlike this system's usual "never trust the agent's self-report" stance
elsewhere: summarization is genuinely the model's job here, not a correctness claim
about whether a task succeeded. But the extracted `project_roadmap` update should
still go through the same propose-then-human-confirms step everything else in this
system does before being saved -- durable repository-level state is exactly the kind
of thing that shouldn't silently drift from an unreviewed AI summary.

A brand-new session (fresh conversation, or resuming after compaction) gets seeded
with the current `project_roadmap` -- small, curated, durable -- not a blind `--resume`
of any prior session_id and not the raw `conversation_summary` transcript either. This
is also what answers open question 4 below (session persistence): `project_roadmap`
*is* the persistence mechanism, not a raw session_id staying resumable indefinitely
across separate `escape-ai` invocations.

## Consumer

**Built 2026-07-20: feature/bug-fix planning refinement**
(`run_planning_conversation_interactive` in `esc_orchestrator/escape_ai_cli.py`,
`esc_exec/conversation.py`, `esc_exec/roadmap.py`) -- proven against planning
refinement first, since it happens far more often than bootstrapping a brand-new
repository. Provider-gated, optional, offered after drafting a single-repository
plan; roadmap updates go through an explicit confirm. Live smoke-tested against the
real `claude` CLI.

**Not a second consumer.** New/empty-repository scaffolding
(`scaffold-new-or-empty-repository.md`) originally surfaced this primitive, but that
doc was resolved 2026-07-20 to *not* need a conversation at all -- scaffolding a new
project from nothing is a solved problem external wizards (`create-next-app`, Spring
Initializr, ...) already handle deterministically; an AI conversation reinventing
that decision would be strictly worse (less deterministic, costs tokens, drifts from
ecosystem convention) and contradicts this system's own premise. See that doc's
"Resolved 2026-07-20" note.

**Built 2026-07-20: onboarding's Tier 2 module resolution + purpose/frameworks
suggestion** (`suggest_unresolved_components`/`suggest_groundable_answers_turn` in
`esc_exec/conversation.py`, wired into `run_onboarding_interactive` in
`esc_orchestrator/escape_ai_cli.py`) -- the genuine second consumer the note above
was waiting for, from `plan/active/generic-multi-component-detection.md`. Two turns
of one session, not two separate one-shot calls: turn 1 (only when
`BuildSystemAdapter.unresolved()` is non-empty) asks the AI to resolve a build
identifier Tier 1 static parsing couldn't map to a real directory; turn 2
(`--resume` of that same session, skipped entirely if turn 1 never ran) asks for
purpose/frameworks on the now-confirmed component list. This is what the "multiple
turns don't re-pay the fixed overhead" measurement above was actually motivated
by avoiding in a concrete case, not just a hypothetical -- module resolution and
purpose/frameworks suggestion would otherwise be two separate fresh `claude -p`
invocations in the same onboarding pass. Provider-gated the same way planning
refinement is; the module-resolution turn's answer is persisted into the
repository manifest (`resolved_components`) so a later analyze/apply never needs
to re-run it.

**Built 2026-07-20: form-driven planning conversation** (`suggest_form_turn` in
`esc_exec/conversation.py`, `run_form_driven_planning_conversation_interactive` in
`esc_orchestrator/escape_ai_cli.py`) -- the third consumer, from
`plan/done/form-driven-planning-conversation.md`. A real functional difference
from the other two: instead of one bounded suggestion call (Tier 2) or a fixed
two-turn sequence (module resolution then purpose/frameworks), this is genuinely
open-ended free-form chat, ending itself once a `---FORM---` trailer (parsed every
turn, stripped from what the human sees) reports every required field filled and
the human confirms -- or when the human sends a blank line early, or the hard
context threshold is hit. Live-verified across two real `--resume` turns,
including the model *revising* an earlier-captured field (`work_type` changed from
`feature` to `fix`) once new information came in mid-conversation, not just
accumulating monotonically.

This primitive is no longer a one-consumer mechanism -- it was never
scaffolding-specific in its design, just in how it was first motivated, and now has
three real, independently-justified consumers.

## Non-goals

- Do not build open-ended/unbounded chat -- every conversation this primitive drives
  is scoped to converging on one specific, named artifact, decided before the
  conversation starts.
- Do not auto-apply anything a conversation converges on without an explicit human
  confirm step, matching every other write path already in this system.
- Do not invent a "response quality/degradation" signal -- only real, API-reported
  context-consumption numbers drive the soft/hard thresholds above.
- Do not let either threshold just cut a conversation off with nothing to show for
  it -- both must produce a durable `conversation_summary` artifact, even if the
  conversation didn't fully converge (`status: in-progress` or `abandoned`, not just
  `converged`).
- Do not let `project_roadmap` silently drift from an unreviewed AI summary -- an
  update to it goes through the same propose-then-human-confirms step as everything
  else this system writes, even though the summarization judgment itself is trusted.
- Do not conflate `conversation_summary` (ephemeral, safe to eventually archive) with
  `project_roadmap` (durable, the actual seed for future sessions) -- they have
  different lifecycles and collapsing them was the mistake this design corrects.

## Open questions

1. ~~Which consumer to build/prove this against first~~ -- resolved: planning
   refinement, built 2026-07-20 (see "Consumer" above). Scaffolding is no longer a
   second consumer at all (see that section) -- this question no longer applies.
2. ~~Turn-taking UX~~ -- resolved: free-form chat, built as `ask()` calls in
   `run_planning_conversation_interactive`'s loop, not structured options.
3. Exact soft/hard threshold percentages (sketched above as ~60-70% / 90%, implemented
   as exactly 0.65/0.90) -- live smoke-tested for basic correctness, but not yet
   against a real conversation long enough to actually approach either threshold.
   Turn count as a secondary "getting unwieldy" nudge (mentioned above) is also still
   unimplemented -- only the percentage thresholds exist in code today.
4. ~~Where do `conversation_summary` and `project_roadmap` actually live~~ -- resolved:
   exactly `.esc-ai/conversations/<id>/summary.yaml` and `.esc-ai/roadmap.yaml`, built.
5. When resuming from `project_roadmap`, does the new session get it as plain prompt
   text (simplest), or does it need its own schema-validated contract the way
   task/workspace/adapter/policy are validated today? Built as plain prompt text (see
   `_roadmap_context_text` in `conversation.py`) -- still no formal schema; revisit
   only if that starts causing real problems, not speculatively.
6. ~~Is `project_roadmap` truly one-per-repository~~ -- moot now that there's only one
   consumer; built as one-per-repository regardless, since that's still the right
   shape for a single evolving "what is this project and where does it stand."
7. How does `project_roadmap`'s human-review step actually work mechanically -- shown
   as a diff against the previous version (old stage/direction vs. new), or just the
   new document in full? Still unresolved -- built as neither, actually: the current
   `confirm()` prompt shows nothing before asking yes/no. Worth fixing before this
   sees real use; a diff is probably still the right answer.
