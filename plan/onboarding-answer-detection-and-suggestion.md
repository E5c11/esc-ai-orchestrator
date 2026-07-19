# Onboarding Answer Detection and Suggestion — Plan

**Status:** Proposed
**Date:** 2026-07-19
**Objective:** Stop asking a user to type a free-text answer to a semantic onboarding
question when the answer is mechanically discoverable, and where it genuinely isn't,
let an AI provider suggest a draft the user confirms or edits instead of typing from a
blank prompt.

## Why this is a separate plan

Surfaced directly from watching the real onboarding question flow (see the
`Question 1 of 2` / `Question 2 of 2` screenshots from this session): both semantic
questions today (`purpose`, `frameworks`/`targets`) ask for free text with no options,
even though at least one of them is plausibly answerable by reading the repository
itself. Same reasoning as the other standalone plans in this directory: a distinct
concern from onboarding's core create/update/preserve/conflict engine, worth its own
document rather than folding in half-formed.

## What was actually checked, not assumed

- `esc_exec/dependencies.py`'s existing Gradle parsing (`PROJECT_DEPENDENCY`,
  `TYPESAFE_PROJECT_DEPENDENCY`) only matches **internal** cross-module references --
  `project(":core:common")` or `projects.core.common` -- for the component dependency
  graph. It does not parse **external** library coordinates
  (`implementation("io.ktor:ktor-client-core:2.3.0")`). Detecting frameworks-in-use is
  a real gap, not something already half-built.
- `onboarding.py::import_project_profile` looked promising but isn't static detection
  either -- it only reads a pre-existing, hand-authored `context/project-profile.yaml`
  legacy file if one happens to already exist. It answers nothing about a repository
  that has never had one written.
- `esc_exec/adapters.py` has exactly one `BuildSystemAdapter` today: `GradleAdapter`.
  Any static-detection work is Gradle-only until another adapter exists.

## The idea

Two tiers, tried in order, per semantic question:

1. **Tier 1 -- deterministic static detection.** Parse the build file directly for an
   answer:
   - **Frameworks**: extract external dependency coordinates
     (`implementation("group:artifact:version")` and friends) from
     `build.gradle.kts`, the same way `dependencies.py` already parses internal
     `project(...)` references -- new regexes, same file, same technique.
   - **Targets/platforms**: detect Kotlin Multiplatform target blocks
     (`android()`, `ios()`, `jvm()`, ...) or single-platform heuristics.
   - If Tier 1 produces a confident answer, skip the question entirely -- present it
     as an already-filled proposal entry (same as any other detected file/manifest
     fact in the onboarding proposal), not a question.
2. **Tier 2 -- AI-suggested, human-confirmed.** For whatever Tier 1 can't answer
   confidently (frameworks/targets when static detection is inconclusive, and
   `purpose`, which is inherently semantic and not mechanically derivable from a build
   file at all): run a small, bounded, read-only task through whichever provider is
   connected (now real, since `native-cli-provider-adapters.md`'s Claude Code adapter
   works -- read the component's source/manifest, suggest an answer) and show the
   suggestion as a pre-filled default the user confirms or edits. Never auto-applied
   without confirmation.

This is not a new interaction paradigm -- it's the same propose-then-human-confirms
discipline already used everywhere else in this system (onboarding's own
create/update/preserve/conflict classification, plan drafts, checkpoints), extended
into the semantic-questions layer specifically.

## Non-goals

- Do not auto-apply an AI suggestion without explicit human confirmation -- same rule
  as every other proposal this system generates.
- Do not call a provider for a field Tier 1 already answered deterministically --
  cost/latency discipline, matching the project's existing usage-awareness (see
  `native-cli-provider-adapters.md`).
- Do not make onboarding require a connected provider. Tier 2 must degrade gracefully
  to today's plain free-text question if no provider is connected yet -- this would
  otherwise contradict the just-built "no wall at first run" decision (onboarding and
  planning never require a provider).

## Open questions

1. Exact static-detection scope per build system -- Gradle only for now, since it's
   the only adapter that exists. What happens for a future non-Gradle adapter is
   unaddressed.
2. Confidence threshold for "Tier 1 succeeded" vs. falling through to Tier 2 -- not
   designed. A wrong confident-looking detection (e.g. a test-only dependency
   misread as a real framework) is worse than asking.
3. Where the Tier 2 AI-suggest call fits architecturally: onboarding today has zero
   adapter/provider dependency (confirmed by reading `escape_ai_cli.py` -- `analyze`/
   `apply_answers` never touch `Runtime`/`Scheduler`). This would be the first time
   onboarding optionally touches a provider. Does it go through the same
   `Runtime.execute` contract shape as a task, or does it need something lighter-
   weight (no verification plan, no checkpoint, it's a single suggestion, not a run)?
4. Cost/latency at scale -- `AMPM` alone has 28 components. Suggesting an answer
   per-component per-field could mean dozens of provider calls during one onboarding
   pass. Needs batching or scoping thought, not per-component calls by default.
