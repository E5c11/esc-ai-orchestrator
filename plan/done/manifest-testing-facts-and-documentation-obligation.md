# Component/Repository Manifest: Testing-Tool Facts and a Documentation Update Obligation — Plan

**Status:** Implemented
**Date:** 2026-08-03
**Implemented:** 2026-08-03 — shipped and tested in esc-ai-execution-framework only
(esc-ai-orchestrator has no changes for this plan). `manifests.py` gained
`merged_testing`/`component_testing_platforms`/`resolve_testing_fact`/
`component_testing_gaps` plus `_testing_errors`/`_documentation_errors` validators,
wired into `validate_repository`/`validate_component`; `indexing.py`'s
`build_component_index`/`build_indexes` embed the resolved facts into the real index
files an agent is told to read. One real, load-bearing correction was made during
implementation, not just a location tweak: this doc originally assumed
`build_instruction_bundle` was how facts reach the agent's prompt — verified false (see
design 1's "Where this actually gets used," corrected below) — so the actual integration
point is the component/root index files instead. Landing here doesn't mean every open
question was resolved as originally framed — see each one's own note, including a new
one (7) added during implementation about the gap between "visible" and the user's own
"imperative" framing.
**Objective:** Two related manifest-completeness gaps, surfaced by reviewing the real,
currently-onboarded `ampm-kmp` and `ampm-backend` manifests: (1) a component/repository
manifest declares its build system (`build.system: gradle`) and points at universal
architecture *philosophy* (`architecture.profile_ids`), but never declares the concrete
testing/quality tooling a component actually uses — unit test framework, mocking
library, coverage tool, lint tool, and (uniquely for KMP) a per-platform UI-testing
approach; (2) a `documentation` path already exists but is an unenforced accident, not a
declared obligation — auto-detected only for a single per-component `README.md`,
inconsistently present even where applicable, and treated identically to a test
directory (a place to read, never a place a task is told to write to).

## Why this is a separate plan

Both are the same underlying gap wearing different clothes: the manifest has a home for
*structural* identity (build system, paths) and a pointer to *universal* philosophy
(`architecture.profile_ids` → the architecture framework's `quality-gates/` prose), but
nowhere to declare a component's own *concrete, project-specific* facts about how
quality is actually enforced and where its own documentation lives. Bundled into one doc
rather than two because fixing #1 (adding a `testing` block) is the natural moment to
also fix #2 (promoting `documentation` from an ad hoc informational path into a
first-class, obligated field) — both are "give the manifest a proper home for a real
fact it's currently missing," found in the same pass over the same two real repositories.

## What we found (grounded against the real, currently-onboarded repositories)

### Testing/quality tooling: real, verified, and genuinely different across the two repos

- **`ampm-kmp`** (`~/StudioProjects/AMPM`, repository ID `ampm-kmp`) —
  `gradle/libs.versions.toml` declares `kover = "0.9.6"` (coverage, applied per-module,
  e.g. `core/common/build.gradle.kts`'s `kover { reports { ... } }` block),
  `mokkery = "2.7.3"` (KMP-native mocking — plain MockK doesn't work across non-JVM
  targets), `detekt = "1.23.7"` (lint/static analysis, applied via
  `build-logic/convention/src/main/kotlin/AmpmAndroidDetektConventionPlugin.kt`, config
  at `config/detekt/detekt.yml`), and `kotlin-test` (unit test framework). UI testing is
  genuinely **two different mechanisms for the same component** (`composeApp`), one per
  platform: Android drives an AI/adb + Compose `testTag`-semantics workflow documented
  in the repo's own `wiki/` (the user's stated preference, not a conventional
  instrumented-test suite); iOS uses a real XCTest target
  (`iosApp/iosAppUITests/iosAppUITests.swift`), since the AI/adb approach has no iOS
  equivalent.
- **`ampm-backend`** (`~/IdeaProjects/ampm-backend`, repository ID `ampm-backend`) — a
  completely different, internally consistent stack: `kotlin-test-junit5` +
  `mockk`/`springmockk` (`build.gradle.kts:71-73`), `jacoco` for coverage, applied via a
  shared custom convention plugin (`id("ampm.backend.module")`, which itself applies
  `jacoco` — confirmed in the extracted plugin block). **No lint tool configured at
  all today** — grepping the whole repo for `detekt`/`ktlint` found nothing. A real gap
  in that repo, not a design assumption; the schema must treat "no lint tool declared"
  as valid, not force one.
- None of this is declared in either repo's real, committed
  `.esc-ai/components/*/esc-component.yaml` today — confirmed by reading `core-common`'s
  (`ampm-kmp`) and `auth`'s (`ampm-backend`) manifests directly. `build.system: gradle`
  is the only build-adjacent fact present; a task working on either repo has no
  machine-readable way to know it should run `detekt`, generate a Kover report, or that
  mocking on this repo goes through `mokkery`, not `mockk`.
- **A component's real KMP source-set layout is already fully detected and sitting in
  its manifest today** — `esc_exec/gradle.py:133`'s `component_structure` walks
  `src/*Main`/`src/*Test` and records one `source_<sourceSet>`/`tests_<sourceSet>` path
  key per directory found, no new detection needed. But reading every one of
  `ampm-kmp`'s 28 real component manifests shows this can't be read as "one key per
  platform" naively: most components use `iosMain` for their Apple-target source set
  (`composeApp`, `core-analytics`, `core-database`, `core-network`, ...), but
  `core-common`/`core-concurrency`/`core-session` use `nativeMain` for what is
  conceptually the same target, and some source sets aren't platforms at all —
  `core-database`'s `roomMain`, `core-firebase`'s `restMain` are internal KMP source-set
  groupings, not deployable targets. Any design that resolves "platform" from these keys
  has to account for this real naming drift, not assume it's already normalized.

### `documentation` already exists, but as an unenforced accident, not a declared obligation

- `esc_exec/gradle.py:141` / `esc_exec/npm.py:167` (`component_structure`) already
  Tier-1-detect a component-root `README.md` and label it `"documentation"` in the
  generated `paths` dict. Confirmed live: 4 of `ampm-backend`'s 10 components (`auth`,
  `core-api`, `core-datetime`, `core-web`) have `paths.documentation: README.md` in
  their real manifests; the other 6 don't (no `README.md` at their path). `ampm-kmp` has
  **zero** components with this key at all — its real documentation lives in a
  repository-wide `wiki/` directory (`wiki/home/home_feature.md`, `wiki/platform/ios.md`,
  etc.), a convention the per-component-README detector has no way to represent.
- `esc_exec/indexing.py:144` is the *only* consumer: `documentation` just gets folded
  into `search_roots` alongside `source`/`tests`/`resources` — purely "a place worth
  reading," identical treatment to a test directory. Nothing distinguishes it as a place
  that *should be written to* when a task changes behavior, the way architecture-profile
  docs are automatically included in every task's instruction bundle. Today an agent has
  to be told, per task, "also update the docs" — only if a human remembers to write that
  into that specific task's `completion_conditions` — there's no standing instruction the
  way there is for architecture coverage.

## Design

### 1. A `testing` block, resolved common-first, then per-platform, then flagged as a gap

New optional `testing` mapping on repository and component manifests (component
overrides repository, same precedent as `architecture.profile_ids`), split into
`common` (the shared answer, if one exists) and `platforms` (per-platform overrides or
additions) — applied uniformly to *every* testing fact (`unit_framework`,
`mocking_framework`, `coverage`, `lint_tools`, `ui_testing`), not a special case for UI
testing alone:

```yaml
# .esc-ai/esc-execution.yaml (ampm-kmp, repository-wide default)
testing:
  common:
    unit_framework: kotlin-test
    mocking_framework: mokkery
    coverage:
      tool: kover
    lint_tools: [detekt]
  platforms:
    android:
      source_sets: [androidMain]
      ui_testing:
        framework: ai-adb-compose-testtag
        notes: "See wiki/ for the AI/adb-driven workflow using Compose testTag semantics."
    ios:
      # Tolerates real per-module naming drift (see "What we found" above): most
      # components use iosMain, but core-common/core-concurrency/core-session use
      # nativeMain for the same conceptual target. A component matches this platform
      # if ANY of these source sets appear in its own detected paths.
      source_sets: [iosMain, nativeMain]
      ui_testing:
        framework: xctest
        notes: "iosApp/iosAppUITests -- no AI/adb equivalent exists for iOS."
```

```yaml
# .esc-ai/esc-execution.yaml (ampm-backend, repository-wide default -- no per-platform
# variation at all, so `platforms` is simply omitted)
testing:
  common:
    unit_framework: junit5
    mocking_framework: mockk+springmockk
    coverage:
      tool: jacoco
    lint_tools: []   # a real, current gap in the repo -- valid, not an error
```

**Resolution algorithm** (the user's own framing, generalized to every fact instead of
special-cased for `ui_testing`): for a given fact and a given platform,

1. `testing.common.<fact>`, if declared — the shared answer already covers every
   platform, so it wins outright, not merely as a fallback default.
2. Else `testing.platforms.<platform>.<fact>`, if declared for that platform.
3. Else — nothing is known. Not silently treated as "no testing exists"; surfaced as an
   explicit gap (see "Alerting" below).

This matches both real repos exactly: `ampm-kmp`'s `unit_framework`/`mocking_framework`/
`coverage`/`lint_tools` all have one common answer that already covers every target
(kotlin-test/mokkery/kover/detekt work identically on Android, iOS, wasm...), so step 1
resolves them and step 2 never runs. `ui_testing` has no common answer at all — there's
no cross-platform UI-test framework — so every platform always falls through to step 2,
and a target `ampm-kmp` doesn't cover yet (a future desktop or wasm build, say) correctly
falls through to step 3 and gets flagged, rather than silently inheriting Android's
answer or looking "tested" when it isn't.

**Alerting (step 3) reuses this codebase's existing manifest-validation severity model
rather than inventing a new one.** `validate_component`/`validate_repository` already
distinguish `INVALID` (schema violation) from `INCOMPLETE` (a real, expected gap in
human-authored semantic content — e.g. an empty `component.purpose`). An unresolved
testing fact for a platform the component is genuinely built for is the same kind of
gap: the new validator appends an `INCOMPLETE`-severity message (`"no {fact} known for
platform {platform}"`), visible through the existing `repository validate`/`repository
status` surfaces — not a new command, and not a hard pre-dispatch blocker (that tier is
reserved for environment/infrastructure gaps per
`pre-flight-doctor-and-gate-prerequisites.md`, not manifest-declaration gaps). Separately,
`build_task_context`/`build_instruction_bundle` includes the same "not declared" note
directly in a task's instructions when a routed component/platform can't resolve a fact,
so the agent is told "no known UI-testing tooling for this platform" rather than the
instructions just silently omitting it.

**Determining "platform" comes from the repository's own indexing, not from the task.**
This was originally an open question leaning toward inferring platform from a task's
declared `scope.paths` — reconsidered: `esc_exec/gradle.py:133`'s `component_structure`
already detects a component's real `source_<sourceSet>`/`tests_<sourceSet>` keys during
onboarding (confirmed live in every `ampm-kmp` manifest), so a component's active
platforms are a **structural fact known once, at manifest-generation time**, never
something that needs guessing from a task's scope. `resolve_testing_fact` derives a
component's active platforms by intersecting its own detected `source_*`/`tests_*` key
names against each declared `platforms.<name>.source_sets` list — a component with
`source_androidMain` and `source_nativeMain` (`core-common`) matches both `android` and
`ios` even though its own source-set name is `nativeMain`, not `iosMain`; a component
whose only non-common source sets are `roomMain`/`restMain` (internal KMP groupings, not
real platforms) matches neither, so no platform-specific facts get resolved for it beyond
`common` — correctly, since those aren't deployable targets. This needs no new field on
`task.yaml` and no routing-logic changes to `build_task_context` beyond reading each
routed component's already-computed `paths` dict, which it already has in hand.

**Merge/validation functions all live in `manifests.py`, not `task_context.py`** (a
deliberate deviation from this doc's original sketch, made to avoid a circular import —
`task_context.py` already imports from `manifests.py`, not the other way around):
`component_testing_platforms(testing, component_paths) -> list[str]` implements the
source-set-intersection derivation above; `resolve_testing_fact(testing, fact,
platform=None) -> Any` implements the three-step common → platform → unresolved
algorithm; `merged_testing(repository_manifest, component_manifest) -> dict` deep-merges
repository and component `testing` blocks (component overrides repository field by
field and platform by platform); `component_testing_gaps(testing, component_paths) ->
list[str]` combines all three into the actual gap list. `_testing_errors`/
`_documentation_errors` (private, matching `_architecture_selector_errors`/
`_worktree_inherit_errors`'s naming) validate shape, reused at both repository and
component scope; `validate_component` gained an optional `repository_testing` parameter
(`None` for any existing caller, preserving behavior) so only `validate_repository` —
which already has the repository manifest loaded — triggers the resolution-gap check.

**Where this actually gets used — corrected from this doc's original assumption, found
while implementing:** `build_instruction_bundle`'s output is *never consumed by any
adapter's actual prompt*. Tracing it down: `claude_code_adapter.py`'s (and Codex's and
OpenCode's) `_prompt` method builds the literal text sent to the model from `context`
directly — "read the repository index first," "then read `{component's index}` for
component `{id}`; search only: `{search_roots}`" — and never touches
`instruction_bundle` at all; that value only gets written to `instruction-bundle.json`
as a provenance record. The *real* integration point is the index files the agent is
explicitly told to read: `esc_exec/indexing.py`'s `build_component_index` now embeds
each component's resolved `testing` facts (merged repository+component, filtered to only
the platforms this component's own detected source sets actually match), and
`build_indexes` embeds the repository's `documentation` block into the *root* index —
the one file `_prompt` tells the agent to read before any component index, making it the
natural place for a repository-wide obligation to actually be seen. Generating *new gate
checks* from the `testing` facts (a lint check, a coverage-report check in
`generate_gradle_verification_profile`/`generate_npm_manifests`) is real, separate
follow-on work — see Non-goals.

### 2. `documentation` becomes a declared, obligated fact — not just an auto-detected search root

Two changes:

- **Generalize beyond "one README per component."** New optional repository-level
  `documentation` block, alongside the new `testing` block:
  ```yaml
  documentation:
    location: wiki/
    convention: "One page per feature/module under wiki/; update the relevant page
      whenever behavior changes."
  ```
  For a component that still uses the per-file convention (`ampm-backend`'s `auth`,
  `core-api`, etc.), the existing Tier-1-detected `paths.documentation: README.md` keeps
  meaning exactly what it means today — this is additive, a repository-wide alternative
  for repos like `ampm-kmp` where docs aren't 1:1 with a component directory, not a
  replacement for the per-component case.
- **Make it visible where the agent actually looks, not a bucket nothing reads.**
  Corrected from this doc's original sketch (`build_instruction_bundle`, which no
  adapter's prompt consumes — see design 1's note): the repository-wide
  `documentation.location`/`convention` is embedded directly in the *root* index
  (`esc_exec/indexing.py`'s `build_indexes`), the file every `_prompt` implementation
  tells the agent to read before anything else. A component's own
  `paths.documentation` (existing, Tier-1-detected) is unaffected — it was already
  folded into that component's own `search_roots`, which the agent is separately told to
  read. This doesn't yet make the obligation as forceful as the user's "imperative"
  framing calls for (see open question 7, added during implementation) — it's visible
  wherever the agent already looks, not a hard requirement it cannot proceed without.

## Non-goals

- Generating new verification-gate checks (a lint check, a coverage-report check) from
  the `testing` block's facts. That's real, separate work touching
  `generate_gradle_verification_profile` *and* `generate_npm_manifests` (per
  `feedback_build_to_scale` — fix the general problem across every adapter, not just
  Gradle) and needs a coverage-report summarizer analogous to `reporting.py`'s existing
  JUnit one (`contracts.py` already reserves `artifact.kind: coverage`, but nothing
  produces one today). Declaring the facts is this plan's whole scope; consuming them
  for gate generation is a follow-on plan once this schema is real and populated on at
  least one onboarded repository.
- Enforcing that `documentation` was *actually* updated (e.g., failing verification if a
  task's diff touches source but not `wiki/`). The instruction-bundle line makes the
  obligation explicit and standing; checking compliance mechanically is a much harder,
  separate problem (path-based heuristics are noisy — a task can legitimately not need a
  doc update) and isn't attempted here.
- A single canonical documentation *system* every repository must adopt. The schema
  accommodates whichever convention a repository already has — see both real, different
  conventions above.
- Requiring every field. All of `testing`/`documentation` stay fully optional, same
  discipline as `architecture.profile_ids` and `worktree_inherit` — a repository that
  hasn't declared its tooling yet (or, like `ampm-backend`'s lint tools today, genuinely
  doesn't have one) isn't invalid for lacking it.

## Open questions

1. **Resolved 2026-08-03:** platform relevance comes from the repository's own indexing
   (each component's already-detected `source_*`/`tests_*` keys), never from the task —
   see the Design section's "Determining platform" note. No new `task.yaml` field, no
   `build_task_context` routing changes beyond reading data it already has.
2. **Resolved 2026-08-03, as a consequence of #1:** `platforms.<platform>` keys are
   free-form, human-declared names (a repository's own choice, e.g. `ios`/`android`),
   decoupled entirely from the raw KMP source-set names via each platform's own
   `source_sets` list — this is what absorbs real naming drift like `nativeMain` vs
   `iosMain` (see "What we found" above) rather than needing a fixed enum or a
   requirement that source-set names already be normalized.
3. Still open: the exact shape of the `_testing_errors` validator's messages when a
   declared `platforms.<name>.source_sets` entry matches *no* component in the
   repository at all (a typo, or a platform the repository doesn't actually build for
   any component) — likely another `INCOMPLETE`-severity finding at the repository
   level, but not designed to schema detail.
4. Whether `coverage.threshold` (a numeric minimum) belongs in this first cut, or is
   premature without also building the gate-generation/enforcement half (see
   Non-goals) — leaning toward including the field now (cheap, purely declarative) even
   though nothing reads it yet, the same "declare now, consume later" step
   `worktree_inherit` and `architecture.profile_ids` both took successfully, but not
   decided.
5. Whether a component-level `paths.documentation` (existing, Tier-1-detected, folded
   into `search_roots`) and a repository-level `documentation.location` (new, folded
   into the root index) being simultaneously true for the same repository reads as
   redundant to an agent seeing both — likely fine (they're complementary, a repo can
   have a wiki *and* per-module READMEs) but not designed further here.
6. Whether an unresolved fact (step 3 of the resolution algorithm) should ever escalate
   beyond `INCOMPLETE` validation severity — e.g. if a task's scope specifically and
   only concerns a platform with zero declared testing tooling at all, is that still
   just a visible gap, or should it behave more like the pre-flight doctor's blockers?
   Leaning toward keeping it at `INCOMPLETE` severity always (per the Design section's
   reasoning: this is a declaration gap, not an environment/infrastructure one), but
   flagging the question since it's a real edge case the two grounding repositories
   don't currently exercise (neither has a platform with zero known tooling).
7. **Added during implementation:** embedding `documentation` in the root index makes it
   *visible* (the agent is told to read that file first) but not *enforced* — nothing
   stops an agent from reading it and still not updating docs, unlike a real pre-dispatch
   or verification gate. The user's own framing was "imperative." Whether that word calls
   for something stronger than visibility (e.g. a required `completion_conditions` entry
   the planning step auto-suggests, or a post-hoc diff check) is unresolved; this
   implementation only delivers visibility, matching the doc's own Non-goals (no
   mechanical enforcement was ever in scope), but the gap between "visible" and
   "imperative" is worth being honest about rather than claiming this fully satisfies
   the ask.
