# Npm/Next.js Architecture-Profile Detection — Plan

**Status:** Implemented
**Date:** 2026-07-20
**Objective:** Close two related gaps found while stress-testing onboarding against
a hypothetical Next.js build (see the now-scrapped `garage-fault-triage-showcase.md`
and the `escape-ai-scope-and-gaps` memory): (1) Tier 1 static framework/profile
detection only exists for Gradle, so an npm component can never get an automatic
`architecture.profile_ids` suggestion; (2) even a correct "this component uses
Next.js" signal can't safely resolve to the more specific `PLAT-WEB-NEXT-APP` doc,
because that requires knowing the component's architecture style (`web-app` vs.
`web-content`), and nothing today asks or derives that for any adapter.

## Why this is a separate plan

Distinct concern, own doc, same reasoning this repo's other plans already use.
This is a general onboarding-detection capability affecting every future npm/web
repository, not something specific to any one project's build — folding it into a
project-specific showcase doc (which is why it isn't landing there) would make it
unreusable the same way `npm-build-system-support.md`'s own history warns against.

## What was actually checked, not assumed

- `detect_gradle_frameworks_and_targets` (`esc_exec/dependencies.py:61-91`) is the
  exact contract to mirror: regex-matches external dependency coordinates in a
  Gradle build file against `GRADLE_COORDINATE_FRAMEWORKS` (a tuple of
  `(prefix, field, value)` triples, e.g. `("io.ktor:", "network", "ktor")`),
  returns `({field: value, ...}, [targets])`, `({}, [])` on a missing/unreadable
  file. Never raises; unrecognized dependencies are silently not reported, never
  guessed at.
- `_architecture_signals` (`esc_exec/onboarding.py:227-282`) calls that Gradle
  function *directly and unconditionally* — `detect_gradle_frameworks_and_targets(root
  / relative / "build.gradle.kts")` — regardless of which adapter actually matched
  the repository. Same hardcoded-to-Gradle shape as the two bugs
  `npm-build-system-support.md` already found and fixed elsewhere
  (`validate_component`, `build_dependency_graph`) — this is a third instance of
  the same class of bug, in a spot neither of those fixes touched.
- The dispatch pattern to mirror already exists and is proven:
  `MANIFEST_GENERATORS = {"gradle": generate_gradle_manifests, "npm":
  generate_npm_manifests}` (`esc_exec/onboarding.py:30-32`), keyed by
  `adapter.name` and looked up at the one call site (`onboarding.py:366`,
  `cli.py:297`). Framework detection needs the same shape, not a new pattern.
- `profile-doc-map.json`'s `"ui": {"next": ["PLAT-WEB-NEXT"]}` entry is *correct as
  far as it goes* — checked `PLAT-WEB-NEXT`'s own frontmatter
  (`esc-ai-architecture-framework/platforms/web/nextjs.md`): `requires:
  [CORE-COUPLING]`, and its body states it's shared by both `ARCH-WEB-APP` and
  `ARCH-WEB-CONTENT` architectures. `PLAT-WEB-NEXT-APP`
  (`platforms/web/nextjs-app.md`) is the `web-app`-specific extension:
  `requires: [PLAT-WEB-NEXT, ARCH-WEB-APP, ARCH-WEB-APP-ERR-CLASSES]`.
  `resolve_architecture_docs` (`esc_exec/architecture_lookup.py`) walks a seed's
  `requires` chain *downward* (what it depends on) before including the seed —
  never upward to something that depends on it. So seeding from the generic
  `PLAT-WEB-NEXT` structurally can never pull in `PLAT-WEB-NEXT-APP`. This is not
  a bug in the walk direction (confirmed correct for every other case) or a
  missing map entry — "uses Next.js" alone genuinely doesn't tell you whether a
  component is `web-app` (forms/Server Actions-heavy) or `web-content`
  (SSG/ISR-heavy); the generic-only mapping is the conservative, correct default
  given what that one signal actually carries.
- No existing onboarding signal captures architecture style at all, for any
  adapter. Checked `planning_questions` (`planning.py`), `_architecture_signals`
  (`onboarding.py`), and `GROUNDABLE_FIELDS` (`claude_code_adapter.py:429-455`) —
  none ask or derive `web-app` vs. `web-content` (or any other framework's
  equivalent style split). This is a real, previously-uncovered gap, not
  something already handled elsewhere and merely unwired.

## Design

### 1. Generic npm framework/profile Tier 1 detection

New `detect_npm_frameworks_and_targets(package_json_path)` in `esc_exec/npm.py`,
mirroring `detect_gradle_frameworks_and_targets`'s contract exactly:

- Reads `dependencies`/`devDependencies` from the component's own `package.json`.
- Matches package names against a new `NPM_PACKAGE_FRAMEWORKS` table of
  `(package_name, field, value)` triples — seeded with at least `("next", "ui",
  "next")`, matching `profile-doc-map.json`'s existing `"ui":{"next":...}` key
  exactly. Add `("react-hook-form", "forms", "react-hook-form")` as a second
  entry too (the doc map already has a matching `"forms":{"react-hook-form":
  ["PLAT-WEB-FORMS"]}` bucket) — proves the table isn't built single-purpose
  around exactly one framework.
- Returns `({field: value, ...}, [])` — no npm-side equivalent of Gradle's KMP-iOS
  target detection exists yet, so `targets` is always `[]` for now (not a
  regression, npm has no comparable signal today).
- `({}, [])` on a missing/unreadable `package.json`, same fail-open contract as
  the Gradle version.

Generalize `_architecture_signals`'s call site to dispatch by adapter name instead
of hardcoding Gradle — a `FRAMEWORK_DETECTORS = {"gradle":
detect_gradle_frameworks_and_targets, "npm": detect_npm_frameworks_and_targets}`
table, same shape as `MANIFEST_GENERATORS`, keyed the same way, so a future third
adapter is an additive table entry, never a new hardcoded branch.

### 2. Explicit architecture-style signal

Without this, a specific profile like `PLAT-WEB-NEXT-APP` can never be safely
suggested — only ever the generic `PLAT-WEB-NEXT`.

- Add `architecture_style` as a new entry in `GROUNDABLE_FIELDS`
  (`claude_code_adapter.py`), same mechanism `purpose` and `frameworks_targets`
  already use: AI-groundable from real source (a component's actual route
  handlers/Server Actions vs. static/ISR page shape are things the AI can
  genuinely read and judge), never an open, ungrounded product decision.
  Optional and skippable exactly like `frameworks` today — a blank answer is
  valid and falls back to the generic doc, never an invented guess.
- `suggest_profile_ids` gains an optional `architecture_style` input. When a
  generic seed (`PLAT-WEB-NEXT`) resolves *and* a style answer is present,
  additionally seed the style-specific doc (`PLAT-WEB-NEXT-APP` for `"web-app"`;
  whichever ID exists for `"web-content"` once checked) instead of stopping at
  the generic one. Unanswered style: behavior stays exactly as it is today
  (generic doc only) — this is additive, not a change to today's default path.

## Non-goals

- Do not infer architecture style from code shape via silent heuristics (e.g.
  scanning for `"use server"` as a proxy signal) — that's exactly the kind of
  ungrounded guess this system's Tier 2 discipline already refuses elsewhere.
  Style goes through the same explicit, AI-grounded question path as
  `purpose`/`frameworks`, never a bespoke static heuristic.
- Do not build framework/profile detection for any adapter beyond Gradle/npm —
  the extension point (`FRAMEWORK_DETECTORS` keyed by adapter name) is the
  generic part; a hypothetical future adapter's own entry is that adapter's own
  work, not pre-built here.
- Do not audit every other entry in `profile-doc-map.json` for the same
  generic/specific ambiguity shape — scoped to the one real instance found
  (Next.js `web-app`/`web-content`). A systemic audit is separate future work if
  the pattern turns out to recur elsewhere, not assumed here.
- Do not change `resolve_architecture_docs`'s `requires`-walk direction —
  confirmed correct behavior for every existing case; the fix is upstream (which
  IDs get seeded), not in the walk itself.

## Open questions

1. Exact `NPM_PACKAGE_FRAMEWORKS` table contents beyond `next`/`react-hook-form` —
   how many entries is "enough" for v1 vs. speculative. Leaning: exactly these
   two to start (mirrors `profile-doc-map.json`'s existing web-relevant entries),
   grown later only when a real repository surfaces a new one, matching how the
   Gradle table itself grew.
2. Whether `architecture_style`'s vocabulary is exactly `web-app`/`web-content`
   (reusing the architecture-framework doc's own `architecture:` frontmatter tag
   values directly) or a separate onboarding-side vocabulary that gets mapped —
   leaning reuse directly, no reason to invent a parallel one.
3. Whether the "does this framework have more than one style-specific extension"
   check (i.e., whether asking about style is even worth doing) is table-driven
   (declared per `profile-doc-map.json` entry) or just hardcoded for the one
   known case (`next`) — leaning hardcoded-but-easy-to-extend for now, given only
   one real instance is known; matches this codebase's existing "don't build for
   a hypothetical case that doesn't exist yet" discipline.

## Task breakdown (ordered by dependency)

1. ~~`detect_npm_frameworks_and_targets` in `esc_exec/npm.py` + `NPM_PACKAGE_FRAMEWORKS`
   table + tests~~ — done 2026-07-20. Seeded with exactly `("next", "ui", "next")`
   and `("react-hook-form", "forms", "react-hook-form")`. 7 new tests in
   `tests/test_npm.py`.
2. ~~`FRAMEWORK_DETECTORS` adapter-dispatch table; wire into `_architecture_signals`~~
   — done 2026-07-20. `FRAMEWORK_DETECTORS`/`FRAMEWORK_DETECTOR_FILENAMES` in
   `esc_exec/onboarding.py`, mirroring `MANIFEST_GENERATORS`'s exact shape;
   `_architecture_signals` and its one call site in `analyze_repository` now take
   `adapter` and dispatch through it instead of calling
   `detect_gradle_frameworks_and_targets` directly.
3. ~~`architecture_style` groundable field~~ — done 2026-07-20 as
   `_extract_architecture_style` + a new `GROUNDABLE_FIELDS` entry in
   `esc_exec/claude_code_adapter.py`. Flows through
   `suggest_groundable_answers_turn` (`conversation.py`) automatically, since both
   paths already dispatch generically over `GROUNDABLE_FIELDS` — verified with a
   test rather than assumed. `_architecture_signals` also now offers this
   question directly: unconditionally alongside a "frameworks" question for any
   npm component (gated to `adapter.name == "npm"` — meaningless for Gradle), and
   whenever a resolved suggestion (imported profile or Tier 1 detection) contains
   `PLAT-WEB-NEXT`.
4. ~~`suggest_profile_ids` gains optional style-aware resolution~~ — done
   2026-07-20 in `esc_exec/architecture_lookup.py`: `architecture_style: str |
   None = None`, additive only, appends `PLAT-WEB-NEXT-APP` when it resolved
   `PLAT-WEB-NEXT` and style is `"web-app"`. `"web-content"` deliberately left
   unhandled (commented, pointing at open question 2) — no confirmed doc ID
   exists for it. Also threaded through `apply_onboarding_answers`'s per-component
   `answer.get("architecture_style")`.
5. ~~End-to-end regression test~~ — done 2026-07-20 in `tests/test_onboarding.py`
   (`NpmOnboardingTests`): a `package.json` with a `next` dependency resolves
   `profile_id_suggestions` to `["PLAT-WEB-NEXT"]` and skips the "frameworks"
   question while still offering "architecture_style"; applying with
   `architecture_style: "web-app"` yields `["PLAT-WEB-NEXT", "PLAT-WEB-NEXT-APP"]`;
   applying without it yields `["PLAT-WEB-NEXT"]` only.

## Status: all 5 tasks complete (2026-07-20)

Built via two parallel agents (task 1; tasks 3-4 — disjoint files, no conflict)
plus a sequential integration pass (tasks 2, 5, both touching the shared
`_architecture_signals`/`apply_onboarding_answers` integration point deliberately
left for last). Full suite: 367 -> 370 tests, all passing.

## Discovered, out-of-scope issue — not fixed here

Writing task 5's end-to-end test surfaced that `apply_onboarding_answers` never
actually applies an analyze-time Tier 1 `profile_id_suggestions` entry unless the
human-provided `answer` dict happens to redundantly repeat the same
`frameworks`/`targets` — because a Tier-1-resolved component's "frameworks"
question is never asked (by design, that's the whole point of Tier 1 skipping the
question), so `answers` never gets populated for it, and `apply_onboarding_answers`
recomputes suggestions purely from `answer.get("frameworks")`/`repository_suggestion`
(imported project profile), never by re-running Tier 1 detection itself. This
reproduces for the pre-existing Gradle path too, not just npm — confirmed by the
same gap existing in `apply_onboarding_answers` regardless of adapter. Every
existing profile-id test in this file (Gradle and npm) already works around it by
passing explicit `frameworks` in the answer, which is why this was never caught
before. Real, but a distinct concern from this plan's two gaps — worth its own
plan doc if it's worth fixing, not folded in here.
