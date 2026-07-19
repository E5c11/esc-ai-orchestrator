# Scaffold a New or Empty Repository — Plan

**Status:** Proposed
**Date:** 2026-07-20
**Objective:** Give onboarding a real path for a repository that has no detectable
build system yet -- either because it's freshly created and empty, or because it
doesn't exist anywhere yet -- instead of just failing with "no supported build-system
adapter detected."

## Why this is a separate plan

Surfaced directly from a gap the user noticed: every onboarding path built so far
assumes real, existing project structure to analyze (Tier 1 static detection) or read
(Tier 2 AI suggestion) -- `detect_build_system` requires a real `settings.gradle.kts`
to already exist, and just raises `ValueError` otherwise. Nothing handles "there's
nothing here yet." Same reasoning as the other standalone plans in this directory:
distinct concern, worth its own document rather than folding in half-formed.

## Two scenarios that look similar but aren't

1. **Empty-but-real repository.** A location exists -- cloned locally, a real git
   remote (GitHub/GitLab) -- but it's empty or near-empty. `detect_build_system`
   fails cleanly and specifically here (no build file found), which is a real,
   catchable condition escape-ai already sees today, it just doesn't do anything
   useful with it yet.
2. **No location at all.** Nothing has been created anywhere -- just an idea. This
   is arguably not an onboarding concern at all: escape-ai's whole framing is "you
   have a repository, let's manage what's in it," and inventing the repository itself
   (naming it, creating it on GitHub/GitLab, `git init`) is a different job. Leaning
   toward **out of scope** -- point the user to create the empty repo first through
   normal means, then case 1 applies. Not settled; see open questions.

## The idea (case 1 -- empty-but-real repository)

- Catch `detect_build_system`'s failure specifically (empty/no build system detected,
  not some other error) and offer a distinct path: "this looks like a new project --
  describe what you're building and I'll suggest a stack," instead of the current
  bare failure message.
- This can't be a single bounded batched call the way Tier 2's suggestions are (see
  `onboarding-answer-detection-and-suggestion.md`) -- there's no real source to read,
  so there's nothing to ground a one-shot suggestion in. It's fundamentally a
  **conversation**: describe the idea, get a proposed stack/architecture, react to it
  ("actually I want Compose Multiplatform instead"), converge.
- **Depends on `ai-conversation-primitive.md`** -- the actual conversation mechanism
  (session continuity, turn-taking UX, when it concludes) is a shared primitive, not
  designed twice here. This doc only covers what's specific to scaffolding: detecting
  the empty-repo condition and deciding what artifact the conversation should produce
  for *this* consumer.
- Whatever the conversation converges on should still land in the same bounded,
  reviewed shape everything else in this system uses -- a proposal (stack choice,
  initial file/directory layout) the human explicitly applies, never auto-scaffolded
  without review. Same discipline as onboarding's existing create/update/preserve
  proposal engine, not a new paradigm.

## Non-goals

- Do not build case 2 (no location at all) without a decision that it's actually in
  scope -- see open questions.
- Do not silently auto-generate project files without an explicit human apply step,
  matching every other write path in this system.
- Do not treat this as a one-shot suggestion call like Tier 2 -- there's no real
  source to ground a single batched answer in the way purpose/frameworks suggestions
  are grounded in real code.

## Open questions

1. Is case 2 (no location at all) actually in scope, or does escape-ai stay
   strictly repository-scoped and defer entirely to the user creating an empty repo
   first? Not decided.
2. What's the bounded output artifact -- a stack/architecture choice only (letting
   the existing onboarding flow take it from there once real files exist), or does
   this also need to generate actual scaffold files (build.gradle.kts, initial
   directory layout)? If the latter, that's a real step beyond anything this system
   currently writes (onboarding writes manifests *about* existing structure, never
   the structure itself).
3. How does this interact with the "no wall at first run" / provider-optional
   principle already established? A pure-conversation flow arguably *does* need a
   provider connected to do anything useful at all -- unlike onboarding an existing
   repo, there's no plain-question fallback that makes sense here (you can't
   meaningfully ask a human "what frameworks does this use" about code that doesn't
   exist). Worth deciding explicitly rather than assuming.
