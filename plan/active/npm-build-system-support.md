# npm/Next.js Build-System Support — Plan

**Status:** Active
**Date:** 2026-07-20
**Objective:** Give escape-ai's onboarding a second real build-system adapter (npm),
so a JavaScript/TypeScript repository can be onboarded the same way a Gradle
repository already can — not Next.js-specific, though Next.js is the first real
consumer (see `plan/active/garage-fault-triage-showcase.md`).

## Why this is a separate plan

Surfaced while stress-testing escape-ai against a take-home-style exercise (see the
`escape-ai-scope-and-gaps` memory) — confirmed by reading the actual code, not
assumed. Same reasoning as every other standalone plan in this directory: this is a
general onboarding capability with its own value independent of any one project that
happens to motivate it, and folding it into a project-specific showcase doc would
make it harder to reuse or re-derive later.

## What's actually missing (verified against the code, not guessed)

`esc_exec/adapters.py`:
```python
ADAPTERS: list[BuildSystemAdapter] = [GradleAdapter()]
```
Only Gradle is detected at all. A `BuildSystemAdapter` Protocol already exists
(`detects`/`detect`, plus `name`/`repository_type`/`component_type`), so the
extension point is real — nothing here needs to be invented from scratch structurally.

More importantly, **the write side of onboarding doesn't dispatch on the adapter that
did the detecting.** `esc_exec/onboarding.py::apply_onboarding_answers` hardcodes:
- `generate_gradle_manifests(root)` (unconditional, regardless of `adapter.name`)
- `generate_gradle_verification_profile(root, component_id)` (same)

`generate_gradle_manifests` also doesn't accept the adapter's already-detected
`components` list — it calls `detect_gradle_repository(root)` again internally. So
this isn't "write one more `if` branch," it's "give onboarding's apply step a real
adapter-shaped seam it doesn't have yet."

Tier 1 architecture-signal detection (`detect_gradle_frameworks_and_targets`, parses
`build.gradle.kts`) has no npm equivalent either, but this is skippable — Tier 2's AI
suggestion (`suggest_onboarding_answers`) already works by reading the repo directly,
not by parsing a specific build-file format, so it covers npm for free.

## Scope

1. **`NpmAdapter`** in `esc_exec/adapters.py` — `detects()` checks for `package.json`;
   `detect()` returns a repository ID (from `package.json`'s `name` field, mirroring
   how `GradleAdapter` reads `settings.gradle.kts`'s `rootProject.name`) and a
   components list. **Deliberately single-component only for now** — no npm/yarn/pnpm
   workspace (monorepo) detection. A real workspace-aware adapter is a legitimate
   future extension, not something to half-build speculatively here (see Non-goals).
2. **`generate_npm_manifests`** in `esc_exec/manifests.py` — mirrors
   `generate_gradle_manifests`'s shape exactly: repository manifest + one component
   manifest per detected component, `build.system: "npm"` instead of `"gradle"`,
   `paths` via the existing `component_structure` helper (verify it isn't
   Gradle-coupled before reusing — check first, don't assume).
3. **Fix `apply_onboarding_answers`** to actually branch on `adapter.name` (or
   equivalent) instead of hardcoding the two Gradle calls — this is the one change
   that turns "detection is generic" into "onboarding is actually generic."
4. **Verification-profile generation for npm** — open question below; likely the
   smallest-scope answer is "skip for now, don't invent a `generate_npm_verification_
   profile` with guessed gate commands" rather than fabricating `npm test`/`npm run
   build` gates nobody asked this repo's manifest to declare.

## Non-goals

- Do not build monorepo/workspace-aware npm detection (multiple `package.json`s,
  workspace roots) — out of scope until a real consuming project actually needs it.
- Do not build Tier 1 static architecture-signal detection for npm (parsing
  `package.json` dependencies into `profile_doc_map` suggestions) — Tier 2 already
  covers this generically; building Tier 1 too would be effort spent on something
  Tier 2 already does, not a missing capability.
- Do not invent verification-gate commands for npm speculatively — see open question 1.

## Open questions

1. Does `apply_onboarding_answers`'s npm path need a real verification-profile
   equivalent to `generate_gradle_verification_profile`, or is leaving verification
   unset/empty for npm components acceptable until `task-orchestration-and-
   verification-loop.md`'s gate-execution work exists anyway (nothing currently
   *executes* gates regardless of language)? Leaning: leave unset, decide for real
   only if something downstream actually breaks on its absence.
2. Repository ID source — `package.json`'s `name` field, same convention
   `GradleAdapter` uses for `settings.gradle.kts`'s `rootProject.name`? Leaning yes,
   not verified against `component_structure`/`repository_manifest_path` for any
   Gradle-specific assumption yet.
