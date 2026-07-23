# Apply-Time Profile-ID Suggestion Gap — Plan

**Status:** Implemented
**Date:** 2026-07-20
**Objective:** Fix `apply_onboarding_answers` so a component whose
`architecture.profile_ids` was already suggested at analyze time (Tier 1 static
detection, or an imported legacy `context/project-profile.yaml`) actually gets
that suggestion written into its manifest — today it silently doesn't, unless the
human-provided answer happens to redundantly repeat the same
frameworks/targets.

## Why this is a separate plan

Discovered while writing the end-to-end regression test for
`plan/done/npm-architecture-profile-detection.md` (see that doc's "Discovered,
out-of-scope issue" section) — but it's a distinct bug from that plan's two gaps,
reproduces on the pre-existing Gradle path too (not npm/Next.js-specific), and
needs its own fix and its own tests. Folding it into that already-closed plan
would misrepresent it as part of the same fix.

## What was actually checked, not assumed

- `_architecture_signals` (`esc_exec/onboarding.py`) computes
  `profile_id_suggestions` at analyze time and — critically — only asks a
  "frameworks" (or, since the plan above, "architecture_style") question for a
  component when *no* suggestion could be resolved. A component Tier 1 (or an
  imported profile) already resolved never gets a question, by design — that's
  the entire point of Tier 1 skipping the question.
- Because of that, `answers` (the human-provided dict `apply_onboarding_answers`
  receives) never contains a `frameworks`/`targets` entry for such a component —
  there was never a question asked for it to answer.
- `apply_onboarding_answers`'s own profile-id resolution
  (`esc_exec/onboarding.py`, the per-component loop) only ever consults
  `answer.get("frameworks")`/`answer.get("targets")` (recomputing via
  `suggest_profile_ids`) or, if those are empty, `repository_suggestion` (which
  it computes itself, freshly, from `import_project_profile(root)` — the legacy
  whole-repository profile import, not Tier 1 per-component detection). It never
  reads `proposal.get("profile_id_suggestions")` at all, despite `proposal` being
  one of its own parameters.
- Confirmed this isn't a plumbing gap between separate CLI invocations: `analyze`
  (`escape_ai_cli.py:298-304`) persists the full proposal via
  `store.save_onboarding_proposal`, and `apply_answers`
  (`escape_ai_cli.py:307-318`) already reads it back
  (`record["proposal"]`) and passes it straight into
  `apply_onboarding_answers` as the `proposal` argument. **The data is already
  there, in-hand, inside the function that needs it — the bug is purely that
  `apply_onboarding_answers` ignores `proposal["profile_id_suggestions"]` in its
  resolution logic, not that the value is unavailable.** This means the fix is
  self-contained inside `esc_exec` and benefits every consumer that goes through
  `apply_answers`/`apply_onboarding_answers` uniformly (the interactive CLI and
  the orchestrator's HTTP API alike), with no cross-repository or store-schema
  changes needed.
- Confirmed via the existing test suite that this was never caught: every
  existing profile-id test in `tests/test_onboarding.py` (Gradle and the newly
  added npm ones) passes an explicit `frameworks` answer even in cases where
  Tier 1 detection could have resolved it independently — none of them exercise
  "Tier 1 (or imported-profile) suggests it, the question is genuinely never
  asked, apply still needs to write it."

## Design

In `apply_onboarding_answers`'s per-component loop
(`esc_exec/onboarding.py`, the block guarded by
`if not manifest.get("architecture", {}).get("profile_ids"):`), add
`proposal.get("profile_id_suggestions", {}).get(component_id)` as a source,
between the existing two:

1. Explicit `answer.get("frameworks")`/`answer.get("targets")` — unchanged,
   highest precedence, so a human who explicitly answers a question still wins
   (covers both the ordinary "question was asked and answered" case and a human
   deliberately overriding a Tier 1 suggestion they disagree with, e.g. via a
   later `repository answer` call).
2. **New:** `proposal["profile_id_suggestions"][component_id]`, if present — the
   already-resolved doc-ID list `_architecture_signals` computed at analyze
   time. Used directly, not re-resolved through `suggest_profile_ids` again (it's
   already resolved doc IDs, not raw frameworks/targets to re-map).
3. `repository_suggestion` (the existing apply-time imported-profile fallback) —
   unchanged, kept for whatever corner case might still reach apply with a
   `proposal` that predates this fix (e.g. a proposal saved before this change
   ships, missing the key entirely — `.get(..., {})` degrades safely to today's
   behavior).

`attempted` (used to decide whether an unresolved component gets reported in
`empty_profile_id_suggestions`) needs the same new source added to its
disjunction, so a Tier-1-suggested-but-now-empty case (shouldn't normally happen,
but e.g. a stale/hand-edited proposal) is still reported rather than silently
passed over.

## Non-goals

- Do not re-run Tier 1 detection at apply time — the whole point of this fix is
  that analyze already did it and stored the result in `proposal`; re-detecting
  would be redundant work and could disagree with what the human already saw and
  implicitly approved by not being asked a question about it.
- Do not change `_architecture_signals`'s question-skipping behavior itself —
  confirmed correct and intentional (Tier 1 resolving something is exactly why no
  question should be asked); the bug is downstream, in apply, not in that
  decision.
- Do not touch the `architecture_style`/Next.js-specific logic from
  `npm-architecture-profile-detection.md` — this plan is adapter-agnostic and
  fixes the general apply-time gap; that plan's fixes already compose correctly
  with this one once it lands (a Tier-1-detected `PLAT-WEB-NEXT` suggestion, with
  or without a separately-answered `architecture_style`, flows through the same
  `profile_id_suggestions` path this plan finally makes apply actually use).

## Open questions

1. Should a stale/hand-edited `proposal` (e.g. one saved before this fix
   shipped, or manually crafted for a script) that lacks `profile_id_suggestions`
   entirely be treated as "no suggestion" (current lean, matches `.get(..., {})`
   defaulting cleanly) or should `apply_onboarding_answers` re-validate
   `proposal`'s shape more strictly here? Leaning: no new validation — this
   function already doesn't schema-validate `proposal` beyond the repository-id
   stale check, and `.get` degrading to `{}` is consistent with that.
2. Precedence between an explicit answer and the analyze-time suggestion is
   "explicit answer always wins" above — worth confirming that's actually
   desired versus, say, warning if they conflict. Leaning: no warning needed,
   since in ordinary flow they're mutually exclusive by construction (a
   component only gets a question, and therefore only gets an answer, when Tier 1
   found *nothing*) — the "both present" case only arises from a deliberately
   crafted answer, which should just win, silently, same as any other explicit
   override in this system.

## Task breakdown (ordered by dependency)

1. ~~Add `proposal.get("profile_id_suggestions", {}).get(component_id)` as the
   second-precedence source~~ — done 2026-07-20 in `esc_exec/onboarding.py`.
2. ~~Regression tests~~ — done 2026-07-20 in `tests/test_onboarding.py`:
   `test_apply_writes_tier1_suggestion_with_no_explicit_answer` (Gradle) and
   `test_apply_writes_tier1_detected_next_with_no_explicit_answer` (npm), both
   with a completely empty (or purpose-only) `answers` dict for the affected
   component.
3. ~~Regression test: explicit answer overrides analyze-time suggestion~~ — done
   as `test_apply_explicit_answer_overrides_tier1_suggestion`.
4. ~~Full suite green~~ — done: 370 -> 374 tests, all passing, no existing
   test's behavior changed.

**Real bug found and fixed along the way, not called out in this doc's original
design:** this plan's own "Non-goals" claimed composing with
`npm-architecture-profile-detection.md`'s `architecture_style` "already works
correctly... once it lands" — that was wrong, caught while writing task 2's npm
test. The `analyzed_suggestion` branch carries an already-resolved doc-ID list
forward *unchanged*; it never passed through `suggest_profile_ids`, so a
separately-answered `architecture_style` (the normal case for npm, since
`frameworks` is never asked once Tier 1 resolves it) would have silently never
applied. Fixed by extracting the style-refinement rule out of
`suggest_profile_ids` into a new standalone `refine_profile_ids_for_style`
(`esc_exec/architecture_lookup.py`), called from both the fresh-`suggest_profile_
ids` path and the carried-forward `analyzed_suggestion`/`repository_suggestion`
paths in `apply_onboarding_answers`, so all three sources compose with style
consistently instead of two doing so and one silently not. New regression test:
`test_apply_applies_style_answer_on_top_of_tier1_detected_next`.

## Status: complete (2026-07-20)

`esc-ai-execution-framework`: 370 -> 374 tests passing.
