# Scaffold a New or Empty Repository — Plan

**Status:** Implemented
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
   (naming it, creating it on GitHub/GitLab, `git init`) is a different job.

**Resolved 2026-07-20: both cases collapse into the same answer, and it isn't a
conversation.** Surfaced while stress-testing escape-ai against a real "build a small
app tonight" scenario (not scaffolded through escape-ai in the end, used purely as a
measuring stick for what's actually missing). Scaffolding a brand-new project from
nothing is already a solved, well-tooled problem -- `npx create-next-app`, Spring
Initializr, Kotlin Multiplatform Wizard, `cargo new`, etc. -- each deterministic,
free, and maintained by people who actually own that ecosystem's conventions. An AI
conversation reinventing that same decision from scratch every time is strictly
worse on every axis that matters here: less deterministic (same request can yield a
different stack shape run to run), costs real tokens for a decision a wizard makes
instantly, and drifts from whatever idioms that ecosystem's tooling actually expects.
This directly contradicts escape-ai's own founding motivation -- consistent, governed,
repeatable outcomes over ad-hoc AI generation -- so building a bespoke scaffolding
conversation would be undermining the thing this system exists to enforce, not
extending it.

## The idea (supersedes the old case-1/case-2 split)

- `detect_build_system` failing (no build system, or no repository at all) is not a
  dead end to patch with more escape-ai machinery -- it's the correct moment to point
  outward. Ask (or infer from context) which stack the user wants, print the matching
  external wizard's exact invocation from a small maintained table (Next.js ->
  `npx create-next-app@latest`, Spring Boot -> Spring Initializr / its CLI, KMP -> the
  Kotlin Multiplatform Wizard, etc.), and stop there -- **run the wizard yourself,
  then come back and onboard the real result** is the whole flow. No conversation, no
  proposal engine, no new artifact.
- This applies identically whether a location already exists (empty repo) or not
  (case 2) -- the instruction is the same either way, so the two cases that used to
  need separate handling no longer do. Case 2 is not "out of scope," it's just not a
  *distinct* case anymore.
- **No longer depends on `ai-conversation-primitive.md`.** That primitive remains
  real and valuable for its actual proven consumer (planning refinement), but
  scaffolding never needed it -- this was the wrong problem for a conversation to
  solve, not a case where the conversation mechanism was missing a feature.
- Real remaining work is small and mechanical: the wizard-command table itself
  (which stacks are covered, and it will always be incomplete -- decide what
  "unsupported stack" says), and detecting which wizard just ran (so the follow-up
  "now onboard it" step doesn't require the user to remember to come back manually).
  Both are plain lookups/detection, not AI decisions.

## Non-goals

- Do not build an AI conversation to invent a stack from scratch -- an external
  wizard already does this deterministically and this system's whole premise is
  preferring deterministic, repeatable outcomes over regenerating a judgment call
  every time.
- Do not have escape-ai itself generate scaffold files (build.gradle.kts, package.json,
  initial directory layout) -- that's exactly the wizard's job, and duplicating it
  means maintaining a second, worse copy of ecosystem conventions escape-ai has no
  business owning.
- Do not silently auto-run a wizard on the user's behalf -- print the command, let
  them run it (or decline and pick something else), matching every other write path
  in this system that requires an explicit human action before anything changes.

## Open questions

1. ~~Is case 2 (no location at all) actually in scope~~ -- resolved above, moot: both
   cases get the same "run this wizard, then onboard" answer.
2. ~~What's the bounded output artifact~~ -- resolved: none. No artifact, no proposal
   engine -- just a printed instruction. If the wizard-detection follow-up (see "The
   idea") is built, its own small state ("a wizard was just suggested for X") might
   need a place to live, but that's not a new persistence *system*.
3. ~~Provider-optional / "no wall at first run"~~ -- resolved: moot, since there's no
   AI call in this flow at all now. Fully works standalone, provider or not.
4. ~~What's actually in the wizard-command table~~ -- resolved 2026-07-20: a plain
   hardcoded `dict` (`esc_orchestrator/scaffold_wizards.py::WIZARD_COMMANDS`), not
   registry-editable. Six starting entries (Next.js, React/Vite, plain npm, Spring
   Boot, Kotlin Multiplatform, Rust); the rendered message says "any other external
   scaffolding tool works the same way" for anything not listed, rather than
   claiming completeness it doesn't have.
5. ~~How does escape-ai detect "a wizard was just run here"~~ -- resolved 2026-07-20:
   no detection at all, as leaned. The printed suggestion's `next_step` line just
   says what to run once the wizard is done, and trusts the user to come back.

## Implementation (2026-07-20)

Built in `esc-ai-orchestrator` (the CLI/UX layer -- this never needed anything from
`esc-ai-execution-framework`, since it's purely about what the CLI does when
`detect_build_system` already fails, not a new detection capability):

- `esc_orchestrator/scaffold_wizards.py` (new) -- `WIZARD_COMMANDS` and
  `render_wizard_suggestion(reason, next_step)`.
- `escape_ai_cli.py::run_onboarding_interactive` -- the interactive "Repository
  path:" prompt now renders the wizard suggestion instead of a bare error, for both
  scenarios (a real empty directory with no detected build system, and a path that
  doesn't exist / isn't a registered repository ID). The second case fixes a real
  pre-existing bug found while implementing this: `resolve_repository`'s
  `KeyError`/`FileNotFoundError` (from `resolve_route` when nothing matches) was
  never caught here at all -- only `ValueError` was -- so typing a nonexistent path
  previously crashed with an uncaught traceback instead of failing cleanly.
- `escape_ai_cli.py::_dispatch_repository`'s `analyze` subcommand -- same two-case
  treatment, replacing the flat `INVALID {exc}` message the plan's own "Why this
  plan exists" section called out by name. Other subcommands (`answer`/`apply`/
  `validate`/`status`) were deliberately left with the plain `INVALID` message --
  by that point a repository should already be registered, so "not found" there is
  more likely a typo of an existing ID than a new-project moment, and a six-line
  wizard table would be a worse message, not a better one, there.
- Tests: `tests/test_scaffold_wizards.py` (new), plus four new cases in
  `tests/test_escape_ai_cli.py` covering both entry points x both scenarios. Full
  suite: 97 tests passing (up from 90).
