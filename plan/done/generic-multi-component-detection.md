# Generic Multi-Component Detection, AI Fallback, and Confirmation — Plan

**Status:** Implemented
**Date:** 2026-07-20
**Objective:** Make "a repository can have multiple real components, detection can
be ambiguous, and a human should confirm the final list before anything is written"
a single generic capability that works the same way for every `BuildSystemAdapter`
-- present (Gradle, npm) and future -- instead of Gradle-specific logic that would
need re-solving per adapter. Also generalizes "offer an AI-suggested draft" itself
into a standing, scoped affordance rather than two bespoke hardcoded fields
(`purpose`, `frameworks`) -- see design section 5.

## Why this is a separate plan

Surfaced from two things in the same conversation: (1) a real Gradle repository
(`arrow-errors`) where `project(":x").projectDir = file("y")` remapping caused every
remapped module to be silently dropped -- fixed this session, deterministically,
in `esc_exec/gradle.py`; (2) the follow-up question "what happens when static
parsing genuinely can't resolve something" surfaced that the fix, and the AI-fallback
idea discussed alongside it (`unresolved_gradle_includes`), were both Gradle-only --
and separately, that `NpmAdapter` is deliberately single-component-only today (no
npm/yarn/pnpm workspace detection), a real, already-shipped non-goal in
`plan/done/npm-build-system-support.md`.

**Explicit operating principle from this point forward (see the `feedback_build_to_
scale` memory): design each feature for the general case a capability needs to
eventually cover, not the narrowest version that unblocks today's example.** Solving
Gradle's projectDir ambiguity and separately, later, solving npm's workspace gap
with its own bespoke mechanism would be the same mistake twice. This plan designs
the one mechanism both need.

## What was actually checked, not assumed

- `BuildSystemAdapter.detect(root) -> tuple[str, list[tuple[str, Path]]]`
  (`esc_exec/adapters.py`) already returns a **list** of components -- the Protocol
  shape was never the limitation. `GradleAdapter.detect` can return many;
  `NpmAdapter.detect` (`esc_exec/npm.py::detect_npm_repository`) always returns
  exactly one, by explicit design (`npm-build-system-support.md`'s stated non-goal).
- npm/yarn/pnpm workspaces are declared in `package.json`'s `"workspaces"` field
  (an array of glob patterns, e.g. `["packages/*"]`) or, for pnpm specifically, a
  separate `pnpm-workspace.yaml`. Resolving a glob pattern against the real
  filesystem and checking each match has its own `package.json` is **fully
  deterministic** -- no equivalent of Gradle's `projectDir` remap ambiguity exists
  here. This means npm workspace detection needs zero AI fallback to build to scale;
  it just needs the glob-resolution logic Gradle's adapter never needed.
- `ai-conversation-primitive.md`'s `run_turn`/`--resume` mechanism already exists
  and is live-verified cheap on a second turn (6141 tokens fresh vs. 45 tokens
  resumed) -- this plan reuses it rather than building session continuity again.
  It currently has exactly one consumer (planning refinement); this plan gives it a
  second.
- `suggest_onboarding_answers` (Tier 2 purpose/frameworks suggestion,
  `esc_exec/claude_code_adapter.py`) is a one-shot `client.ask()` call today, not
  session-based -- if module-resolution became a second, separate one-shot call,
  onboarding would pay the ~40-50K fixed `claude -p` setup cost twice in one
  onboarding pass. Confirmed this is a real cost, not a hypothetical one.

## Design

### 1. `BuildSystemAdapter` gains a generic `unresolved` signal

```python
class BuildSystemAdapter(Protocol):
    name: str
    repository_type: str
    component_type: str

    def detects(self, root: Path) -> bool: ...
    def detect(self, root: Path) -> tuple[str, list[tuple[str, Path]]]: ...
    def unresolved(self, root: Path) -> list[str]: ...
```

`unresolved` returns whatever identifiers this adapter's own static detection found
signal for but could not resolve to a real, on-disk component -- in that adapter's
own ID shape (a Gradle project path like `:core:api`; whatever an npm-workspace or
future Maven/Cargo adapter's equivalent would be). Non-empty is the generic "Tier 1
is stuck here" signal onboarding orchestration keys off of, without ever needing to
know which adapter produced it.

- `GradleAdapter.unresolved` wraps `unresolved_gradle_includes` (already built this
  session in `esc_exec/gradle.py`).
- `NpmAdapter.unresolved` returns `[]` always, once workspace detection (below) is
  built -- glob resolution against a real filesystem has nothing left ambiguous by
  construction. The method still exists (satisfying the Protocol uniformly) even
  though this adapter never populates it; a future adapter that *does* have
  irreducible ambiguity (or npm's own glob resolution turning out to need a fallback
  for some case not yet seen) has the seam ready without changing the Protocol again.

### 2. Real npm/yarn/pnpm workspace detection (reverses `npm-build-system-support.md`'s non-goal)

`detect_npm_repository` (`esc_exec/npm.py`) gains real multi-component detection:

- Read the root `package.json`'s `"workspaces"` field -- either a plain array of
  glob patterns, or (less common but real) `{"packages": [...]}`.
- For pnpm specifically, also read `pnpm-workspace.yaml`'s `packages:` list if
  present (pnpm does not use `package.json`'s `workspaces` field at all).
- Resolve each glob pattern against the filesystem (support `*` and `**`, plus a
  leading `!` negation prefix to exclude a match -- covers the large majority of
  real-world declarations without chasing full glob-spec completeness, the same
  proportionality Gradle's own regex-based parsing already applies).
- Every resolved directory containing its own `package.json` becomes a component,
  same shape as Gradle's `(component_id, relative_path)` tuples -- `component_id`
  from that sub-package's own `package.json` `name` field (mirroring how Gradle
  derives a component ID from its project path), falling back to the directory name.
- No `"workspaces"` field at all -- today's single-component behavior, unchanged.
  This is not a breaking change for the common single-app case.

### 3. Generic AI-fallback module-resolution turn

When `adapter.unresolved(root)` is non-empty **and** a provider is connected
(matches the existing "AI is enrichment, never a requirement" gate used everywhere
else -- `suggest_answers_via_provider`'s exact pattern): run one `run_turn` call,
adapter-agnostic in its phrasing --

> "This repository declares these identifiers in its build configuration, but they
> could not be resolved to a real on-disk directory: `{unresolved}`. Explore the
> repository and figure out which existing directory each one actually corresponds
> to. Respond with ONLY a JSON object: `{"<identifier>": "<repository-root-relative
> directory>", ...}` -- omit any you can't confidently resolve, never guess."

The prompt never needs to mention Gradle, npm, or any build-system-specific syntax
-- it's phrased entirely in terms of "identifier -> directory," which is exactly
what `unresolved()`'s generic contract provides. Parses the response the same
defensive way `suggest_onboarding_answers` already does (fails open to `{}` on any
error), and only accepts an answer whose directory genuinely exists on disk.

This is a **new second consumer** for `ai-conversation-primitive.md`'s `run_turn` --
update that doc's "Consumer" section once built.

### 4. Always-shown component confirmation step

After the component list is final (Tier 1 alone, or Tier 1 + the AI-resolved
additions from step 3), **before any manifest is generated or purpose/frameworks
question is asked**, show the user the full list (component ID + real path,
whichever adapter produced it) and let them deselect any they don't want onboarded
(test fixtures, deprecated modules, samples, ...). Applies uniformly regardless of
adapter or whether AI was involved -- decided explicitly this session, not
conditional on ambiguity having occurred.

- Confirmed components proceed into the **second** `run_turn` of the same session
  (`--resume`, cheap) for purpose/frameworks suggestion -- excluded components are
  never asked about at all, saving both AI cost and a pointless question. If no AI
  turn was needed for resolution (the common, fully-deterministic case),
  purpose/frameworks suggestion stays exactly as it is today: a single fresh call,
  no forced "turn 1" invented just to have one.
- UX mechanism: open question 3 below.

### 5. Generalize "AI suggest" into a standing, scoped affordance

Raised mid-conversation: not everyone using this tool knows their own repository
well enough to answer a free-text question confidently, and the tool's whole point
is steering someone toward a good, context-driven AI interaction instead of an
inefficient ad hoc one -- so "would you like AI to suggest an answer?" should be a
standing option wherever it can be genuinely useful, not a special case wired into
exactly two field names (`purpose`, `frameworks`) the way `_collect_answer`
(`escape_ai_cli.py`) does today.

**Scope: repo-groundable questions only, never open judgment calls.** Checked
directly against the code, not assumed -- `planning.py::planning_questions`
already draws exactly this line for a *different* question set:
- `components` (which components an initiative touches) already has a
  deterministic, non-AI suggestion baked directly into its prompt text, from
  `route_objective`'s keyword-matched routing -- no AI involvement needed or
  wanted here, Tier 1-equivalent already covers it.
- `scope_boundary`, `completion_conditions`, `rollout_needs` are explicitly
  documented in that function's own docstring as "product decisions... needing a
  human" -- these are judgment calls with nothing in the repository to ground a
  suggestion in. Offering an "AI suggest" button here risks anchoring the user
  toward an invented-sounding answer for exactly the kind of decision this system
  elsewhere refuses to let AI self-report on ("trust the artifact, not the agent"
  applies here too: don't trust an ungrounded guess either).

So this plan's actual scope for the affordance is: **onboarding's own semantic
questions** (`purpose`, `frameworks`/`targets`, and the new module-identity
resolution in section 3 above) -- all three are genuinely answerable by reading
real source, and all three already have (or, for module-identity, are gaining) a
grounded AI path in this doc. Extending the affordance to planning's questions is
explicitly out of scope here (see Non-goals) -- `components` already has its
non-AI equivalent, and the other three are judgment calls by design, not a gap.

**Mechanical requirement: no new live per-question call.** `_collect_answer`
today handles exactly two field types by name, each independently; a new
groundable field type (should onboarding ever grow one) currently has no generic
suggestion path at all -- it silently falls through to a bare, unsuggested prompt.
To build to scale here without reintroducing a live AI call per question (the
exact cost problem section 3's batching already solves once), `suggest_onboarding_
answers`'s prompt-building and response-parsing should be driven by a small
declared registry of groundable fields (each with its own short instruction for
the AI and its own response-validation shape), not two hardcoded if-branches --
so a future groundable field is additive to that registry, never a new bespoke
function or a new AI call.

### 6. Exclusion persistence

A user's exclusion decision needs to survive re-analysis -- otherwise Tier 1 (or
Tier 1 + AI) just re-proposes the same excluded module as "create" every single
time, forcing the user to exclude it again and again. Stored as a new
`excluded_components: [gradle-or-npm-derived-id, ...]` list in the repository's own
`.esc-ai/esc-execution.yaml` -- repo-level, travels with the repo, shared with the
team via commit, same storage tier as everything else escape-ai decides (not the
orchestrator's local machine-only sqlite store, which holds only in-progress
session state like pending answers). `analyze_repository` filters any
Tier-1/Tier-2-detected component whose ID appears in `excluded_components` out of
its proposal entirely -- it stops being offered as "create," not shown as some new
fourth action alongside create/update/preserve/deprecate.

## Non-goals

- Do not chase full npm/yarn/pnpm glob-spec completeness (advanced patterns like
  nested negation, non-`*`/`**` glob syntax) -- `*`/`**`/leading-`!` covers the
  large majority of real declarations, matching the same proportionality Gradle's
  own regex-based (not a real Gradle DSL parser) detection already accepts.
- Do not build this for a hypothetical third adapter (Maven, Cargo workspaces, ...)
  that doesn't exist yet -- the point is the *extension point* (`unresolved()` on
  the Protocol) is generic, not that every conceivable adapter is pre-built. Adding
  one later should not require touching this plan's design again, only implementing
  that adapter's own `unresolved()`.
- Do not make the AI fallback (step 3) the default path when Tier 1 already
  resolves confidently -- still Tier 1 first, AI only when genuinely stuck, per the
  existing Tier1/Tier2 precedent this whole codebase already follows elsewhere.
- Do not let the confirmation step (step 4) become a second, separate persistence
  system -- it reuses the repository manifest's own component-list machinery
  (`excluded_components` is just one more field there), not a new artifact type.
- Do not extend the "AI suggest" affordance (step 5) to `planning.py`'s own
  question set -- `components` already has a non-AI equivalent (`route_objective`
  routing), and `scope_boundary`/`completion_conditions`/`rollout_needs` are
  judgment calls with no grounding to suggest from, by that function's own stated
  design. Not a gap this plan needs to close.
- Do not build a per-question live AI call as part of generalizing step 5 -- every
  suggestion still rides the same batched/session mechanism sections 3 and 5
  already establish, never a new fresh `claude -p` invocation per field.

## Open questions

1. Exact multi-select UX mechanism -- a new arrow-key multi-select variant of
   `select_menu` (nicer, more implementation work, needs a non-TTY fallback like
   the existing single-select picker), or a simpler v1 ("type numbers to exclude,
   comma-separated, Enter to include all," matching the existing non-TTY fallback
   style and requiring no new picker infrastructure)? Leaning toward the simpler v1
   first -- consistent with how `select_menu` itself already has this exact
   TTY/non-TTY split, and a fancier picker can follow later without changing the
   underlying confirm-then-proceed flow.
2. Where exactly does `NpmAdapter.detect`'s per-workspace-member `component_id`
   come from when a sub-package's own `package.json` has no `name` field -- the
   directory name (mirroring Gradle's `rootProject.name` fallback), or something
   else? Leaning directory name, not yet verified against a real pnpm-workspace
   repository.
3. Should `unresolved()` be a required Protocol method every adapter must implement
   (uniform, but forces `NpmAdapter` to add a method that always returns `[]`), or
   optional via `getattr(adapter, "unresolved", lambda root: [])`-style duck typing
   (less uniform, avoids a trivial always-empty override)? Leaning required --
   matches how `detects`/`detect` are already required, not optional, on the same
   Protocol, and keeps every future adapter honest about the question rather than
   letting it be silently skipped.
4. Exact shape of the "groundable fields" registry in design section 5 -- a plain
   list of `(field_name, ai_instruction, response_validator)` tuples inside
   `claude_code_adapter.py` (simplest, colocated with the one function that reads
   it), or something onboarding's own schema declares and `suggest_onboarding_
   answers` merely consumes (more layers, but keeps "which fields are groundable"
   a property of onboarding's question model rather than the AI-calling code)?
   Leaning the former for now -- only three fields exist to register today
   (`purpose`, `frameworks`/`targets` as one, module-identity), not enough
   variety yet to justify a separate declarative layer.

## Task breakdown (ordered by dependency)

1. ~~`BuildSystemAdapter.unresolved` Protocol method; `GradleAdapter.unresolved`~~ --
   done 2026-07-20, `NpmAdapter.unresolved` (always `[]`) built alongside it since
   its value never depends on task 2's workspace-detection work.
2. ~~Real npm/yarn/pnpm workspace detection in `detect_npm_repository`~~ -- done
   2026-07-20: `package.json` `"workspaces"` (array or `{"packages": [...]}`) and
   `pnpm-workspace.yaml`, `*`/`**`/leading-`!` glob support, `node_modules` always
   excluded even under `**`. 14 tests.
3. ~~Generic AI-fallback `run_turn`-based module-resolution function~~ -- done
   2026-07-20 as `suggest_unresolved_components` in `esc_exec/conversation.py`,
   corrected from this doc's original `claude_code_adapter.py` placement:
   `conversation.py` imports `ClaudeCodeClient`/`run_turn` dependencies *from*
   `claude_code_adapter.py`, so putting a `run_turn`-based function in the latter
   would be a circular import, not a placement preference. 7 tests, fails open on
   any turn error, never mentions Gradle/npm in its prompt (verified by test).
4. ~~Wire (3) into `run_onboarding_interactive`~~ -- done 2026-07-20: runs before
   `analyze()` so the resolution is reflected in this session's proposal
   immediately (not just after a later apply persists it), gated on
   `active_provider(registry) == {"id": "claude", "route": "subscription"}` --
   the same gate `suggest_answers_via_provider` already uses. Prints what
   resolved and what's being skipped either way (with or without AI).
5. ~~Always-shown component confirmation step~~ -- done 2026-07-20 as
   `confirm_components_interactive`, per open question 1's leaning: typed
   numbers, no new picker infrastructure. Wired between `render_proposal` and
   the semantic-questions loop, so excluded components are never asked about
   purpose/frameworks either -- not just excluded from the final manifests.
   Returns `None` on EOF/Ctrl-C, mirroring `select_menu`'s own cancel
   convention, rather than a bespoke cancellation signal.
6. ~~Groundable-fields registry~~ -- done 2026-07-20 as `GroundableField`/
   `GROUNDABLE_FIELDS` in `claude_code_adapter.py`, refactoring
   `suggest_onboarding_answers`'s two hardcoded if-branches into registry entries
   with the exact same external signature and behavior (all 35 pre-existing tests
   pass unchanged). **Correction from this doc's original wording:** no
   module-identity entry was added here -- module-identity resolution
   (`suggest_unresolved_components`, task 3) is keyed by *unresolved identifier*,
   not *component ID*, and runs before the component list is even confirmed, so
   it doesn't share this registry's per-component-id shape. It stays its own
   function; only purpose/frameworks-targets (genuinely per-component-id
   questions) are registry entries.
7. ~~Second `run_turn` (`--resume`) for purpose/frameworks suggestion~~ -- done
   2026-07-20 as `suggest_groundable_answers_turn` in `conversation.py`, sharing
   `build_groundable_prompt`/`parse_groundable_response` with the one-shot path
   rather than duplicating prompt logic (same circular-import reason as task 3:
   `client.ask()` has no `--resume` support at all, only `client.run()`/
   `run_turn` does). `suggest_answers_via_provider` takes an optional
   `resume_session_id`; `run_onboarding_interactive` passes task 4's
   `resolution_session_id` through, `None` when no turn 1 ran (today's single
   fresh call, unchanged). **Real bug found and fixed along the way, not called
   out in this doc's original design:** the registry refactor (task 6) initially
   leaked a field to every requested component regardless of whether it was
   individually applicable, once two groundable fields were requested in the
   same call with different per-component applicability -- caught by a new
   regression test in both the one-shot and turn-based paths before it shipped.
8. ~~`excluded_components` persistence~~ -- done 2026-07-20, together with
   `resolved_components` persistence for task 4's AI answers, both via one
   shared `_merged_components` helper in `onboarding.py` rather than two
   separate mechanisms -- both `analyze_repository` and `apply_onboarding_
   answers` read/merge/filter through it, so a later analyze with zero override
   arguments still reflects a past session's confirmed exclusions and AI
   resolutions automatically. **Real latent bug found and fixed along the way,
   exposed by (but predating) this session's projectDir-remap fix:**
   `generate_gradle_manifests` reconstructed `build.project` from a component's
   *directory* (`":" + relative.parts joined`), which only matches the real
   Gradle project path by convention -- a convention projectDir remapping
   exists specifically to break. Fixed via a new `gradle_project_paths(root)`
   helper; both `generate_gradle_manifests`/`generate_npm_manifests` also
   gained optional `repository_id`/`components` overrides so a caller with a
   merged/filtered list doesn't get silently ignored in favor of a fresh
   Tier-1-only detection.
9. ~~Update `ai-conversation-primitive.md`'s "Consumer" section~~ -- done
   2026-07-20.

## Status: all 9 tasks complete (2026-07-20)

Full test suites green throughout: `esc-ai-execution-framework` 262 -> 300 tests,
`esc-ai-orchestrator` 98 -> 102 tests. Live-verified against the real `arrow-errors`
repository (read-only `analyze_repository` call, not a write) -- all 4 real
components detected correctly end to end through the full merged pipeline.
