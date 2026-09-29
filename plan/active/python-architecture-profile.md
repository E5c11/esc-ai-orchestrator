# Python Architecture Profile — Plan

**Status:** Implemented and applied to both repos (2026-09-29). 7 of 27 docs are `status: active`; the other 20 are unreviewed drafts until applied (see "Which docs were exercised")
**Date:** 2026-09-29
**Objective:** Add Python guidance to `esc-ai-architecture-framework` — Practical
Clean with Python idioms — so an architecture gate can resolve a Python repository
against a real, vetted framework profile instead of labeling everything `local`.
Docs and mappings only; the engine side is deliberately deferred (see "Non-goals").

## Why this is a separate plan

`VISION.md`'s core bet is that the architecture gate checks coverage against a real,
resolvable framework, not rules invented per task. Today that framework has no Python
content at all (Python appears only in its own `tools/` scripts), so any Python
objective falls into the `local` case — the one place the vision says trust must be
earned. Adding the profile is the direct fix, and it is independent of the
engine work (a `PythonAdapter`), which is its own follow-up plan/ticket.

Adjacent to `escape-ai-scope-and-gaps` (Gradle/npm-only onboarding, now fixed for npm)
and to BLA-50 (local note rating): a real Python profile shrinks the surface BLA-50
has to rate.

## What we found

- Architectures in the framework: `pragmatic-clean`, `backend-service`, `web-*`.
  Platforms: `mobile`, `backend`, `web`, `library`.
- `pragmatic-clean` is Kotlin Multiplatform / mobile-shaped (`ARCH-PC` declares
  `platform: [mobile]`, layers View → ViewModel → UseCase → Repository → DataSource).
  `backend-service` is Kotlin/Spring (Controller → Service → DataSource). Neither can be
  reused verbatim for a Python service or CLI; the *core* rules (`CORE-DI`,
  `CORE-COUPLING`, `CORE-SSOT`, `CORE-ERROR`, `PAT-DATA-ACCESS`, `PAT-OUTCOME`) are
  language-neutral and are.
- Most existing rules are `enforced_by: [reviewer]`. Python has a mature, mechanical
  way to enforce layer direction (`import-linter` contracts), which fits the vision's
  "gates did their job" better than reviewer-only rules.
- Both this repo and `esc-ai-execution-framework` are Python apps that follow no
  written architecture — they are the natural first consumers (see "Dogfooding").

## Approach

Practical Clean as the base, expressed for Python. New content, in the framework repo:

1. **Architecture decision (first task, written down before any docs).** Either a new
   `architectures/python-service` (Practical Clean layers for Python services/CLIs) or
   generalizing `pragmatic-clean` to non-mobile. Recommendation: new architecture that
   `requires` the same core/pattern IDs, because `ARCH-PC` is explicitly KMP and
   generalizing it risks churn for mobile consumers.
2. **`platforms/python`** (`PLAT-PY-*`): packaging (`pyproject.toml`), typing, testing
   (pytest), project layout, tooling.
3. **Idiom decisions, each as a documented rule (not left implicit):**
   - Dependency inversion: `typing.Protocol` + constructor injection + a plain
     composition root; no DI container.
   - Error flow: `PAT-OUTCOME` (Result) vs Python's exception idiom. Open question —
     see below.
   - Boundary data: frozen dataclasses internally, validation library only at the
     edges.
   - Layer enforcement: `import-linter` contract per layer rule, declared in the
     rule's `enforced_by` as planned/unavailable until the engine can run it.
4. **Profile mappings**: add Python entries to `profile-doc-map.json` and make sure
   `tools/validate.py`, `tools/index.py` and `tools/lookup.py` resolve them.

## Open questions

1. Result type vs exceptions. Lean: typed domain exceptions inside a layer, Result at
   layer boundaries — needs the maintainer's decision before writing `error-flow`.
2. New architecture vs generalizing `pragmatic-clean` (recommendation above).
3. Async: is it a first-class part of the profile or a follow-up?
4. Which sources to draw on for the rules (Clean Architecture's principles, plus the
   framework's existing house style). The maintainer is new to Python app
   development, so docs should be short and opinionated; review before marking
   `status: active`.

## Dogfooding

Feasible, with caveats found in `esc_exec/worktree.py`: runs execute in a disposable
worktree on its own branch, so a run cannot break the live checkout, but the
orchestrator imports the engine as an editable install of the live sibling checkout.
A merge into the engine changes the running tool; a base that drifted mid-run needs
manual conflict resolution; a broken engine cannot repair itself. Mitigations: run the
dogfooding from a pinned, separately installed release; start with low-risk targets
(docs, this profile) before the engine core; keep normal git review as the escape
hatch. The first governed refactor after the profile lands is the planned split of
`escape_ai_cli.py` (2.8k lines, ~73% of the orchestrator).

## Non-goals

- No `PythonAdapter`, verification wiring, or fitness-function execution in
  `esc-ai-execution-framework` — separate follow-up (onboarding a Python repo through
  `escape-ai` stays impossible until then). The profile is validated with the
  framework's own `tools/validate.py` / `lookup.py`, which do not need the engine.
- No changes to the Kotlin/mobile/web content.
- Not exhaustive: cover the gate-relevant rules (layers, DI, errors, testing,
  packaging), not a Python style guide.

## Acceptance

- Architecture decision recorded (open question 2) before docs are written.
- `platforms/python` and the Python architecture docs exist with valid front matter,
  rule blocks, and IDs; `tools/validate.py` passes and `tools/index.py` is regenerated.
- `tools/lookup.py` resolves a Python profile to the new docs.
- Layer rules name their intended `import-linter` enforcement.
- Docs reviewed by the maintainer before `status: active`.

## Follow-ups

- `PythonAdapter` + verification/fitness wiring (execution framework).
- Apply the profile to `esc-ai-orchestrator` and `esc-ai-execution-framework`; first
  governed refactor: split `escape_ai_cli.py`.

## Outcome (2026-09-29)

Built in `esc-ai-architecture-framework` (uncommitted at time of writing): 27 documents,
165 rule blocks, `python` platform + `python-app` architecture enums, profile mappings.

- `architectures/python-app/` (12): overview, entrypoint, use case, domain, datasource,
  gateway, composition, error flow, policy, observability, concurrency, modules.
- `platforms/python/` (11): README (baseline + ruff families), typing, DI, import-linter,
  testing, CLI, MCP, HTTP, persistence, subprocess, packaging.
- `feature-orchestrators/python/` (4): use case, entrypoint, modularise, adapter.
- Tooling: `tools/validate.py` and both schemas accept the new values;
  `tools/lookup.py` `PROFILE_DOC_MAP` gained `interface` (cli/mcp/http) and
  `database` sqlite/sqlalchemy; index and `profile-doc-map.json` regenerated.
  `validate.py` passes; `lookup.py --orchestrator ORCH-PY-* --phase N --profile ...` resolves.

Scope beyond the original "docs cover the layers": the docs were deliberately written to
also cover the other Escape AI tickets' implementations — intent verbs and procedures
(BLA-42/43/44: `PYEP-INTENT-01`, `PYUC-STAGE-*`, `PYEP-HONEST-01`), MCP server (BLA-47:
`PLAT-PY-MCP`), role/interaction model and evidence-rated trust (BLA-49/50:
`ARCH-PY-POLICY`), telemetry/sharing (BLA-46: `ARCH-PY-OBSERVABILITY`), and module
growth/splitting (`ARCH-PY-MODULES`, `ORCH-PY-MODULARISE`).

### Open questions — how they were resolved (all revisable by the maintainer)

1. **Result vs exceptions:** three-way split, not a blanket choice. Expected outcomes
   (a gate failed, findings reported) are returned as frozen outcome values (satisfies
   `PAT-OUTCOME`); known errors are typed `AppError` exceptions translated once per
   surface; unexpected errors propagate to a top-level handler. See `ARCH-PY-ERROR`.
   This differs slightly from the earlier lean ("Result at layer boundaries").
2. **New architecture vs generalizing `pragmatic-clean`:** new `python-app`, because
   `ARCH-PC` is explicitly KMP/mobile; core/pattern IDs are shared.
3. **Async:** not first-class. Sync by default; concurrency confined to named seams
   (`ARCH-PY-CONCURRENCY`).
4. **Sources:** Clean Architecture principles plus the framework's house style; every
   tool-behavior claim was checked by running the tool (import-linter 2.15, ruff 0.16.9,
   mcp 2.2). Notably the MCP SDK renamed `FastMCP` to `MCPServer` at 2.0 — `PLAT-PY-MCP`
   pins the major and says so.

### Review required

Marked `status: active` (framework commit `909aaa0`), because each was applied end to end to both
repos and corrected where it was wrong: `ARCH-PY`, `ARCH-PY-ENTRYPOINT`, `ARCH-PY-MODULES`, `PLAT-PY`,
`PLAT-PY-IMPORT-LINTER`, `PLAT-PY-TESTING`, `ORCH-PY-MODULARISE`.

The other 20 deliberately omit `status:` (the framework's convention for unreviewed docs; `esc_exec`'s
coverage gate treats anything but `active` as unreviewed, so the engine treats them as stubs if a Python
profile is resolved). `ARCH-PY-ERROR` was only partly applied (swallowed errors and `assert`, not
`AppError`, the translation function or the exit-code table), so it stays a draft. Flip a doc to `active`
only once it has been applied to real code.

### Not verified

`tools/lookup.py` resolves the docs, and the import-linter and ruff reference configs
were run against a compliant and a violating skeleton. Not verified: the FastAPI and
`pydantic` guidance (written from knowledge, not run). *Superseded in part by the section below: the profile has since been applied to both repos.*

## Findings from applying the profile (2026-09-29)

The docs were applied to `esc-ai-orchestrator` and `esc-ai-execution-framework` by following
`ORCH-PY-MODULARISE`, on branch `refactor/python-app-conformance` in each repo (not pushed; 10 commits
in the orchestrator, 3 in the engine, each green). Numbers are measured, not estimated.

### Result

| | Before | After |
|---|---|---|
| Orchestrator `escape_ai_cli.py` | 2,827 lines, 89 functions, 146 prints, 3 inputs | 92-line compatibility facade; logic in 22 modules under `domain/`, `application/`, `entrypoints/cli/` (largest 422) |
| Orchestrator tests | 244 | 248, all green; coverage 85% -> 86% (277 vs 279 missed lines) |
| Orchestrator contracts | none | 4 import-linter contracts kept, 2 justified exceptions; ruff config enforced by a test |
| Engine `claude_code_adapter.py` | 886 lines (over the 800 cap), 4 concerns | 262 lines; policy / client / suggestions split out |
| Engine tests | 533 | 536, all green |
| Engine contracts | none | 3 import-linter contracts kept; a test fails when a module is unclassified |

Behavior-preserving except two deliberate, separately committed changes: the three swallowed
scheduler errors now log (`87a4894`), and three renderers that did I/O were made pure, which changed
their signatures (`a9ed188`). Both are described in their commit messages.

### What the docs got right

- The layering (`entrypoints > application > domain`) matched the code once the concerns were
  separated; the engine was already acyclic, so contracts could encode what was true.
- `PYEP-RENDER-01` found real defects: `render_onboarding_map`, `render_repository_list` and
  `render_checkpoint_candidate` read manifests, resolved routes and ran `git diff`.
- `PYERR-SWALLOW-01` found three real silent swallows in the scheduler; `PYERR-ASSERT-01` found an
  import-time `assert` that `python -O` would skip.
- `PYMOD-SIZE-01` correctly flagged the one engine module over the hard cap.
- Transitive contract checking surfaced two design smells: a pure helper (`granted_categories`) living
  in a process-spawning module, and a value type living in an I/O module.

### What the docs got wrong or missed (corrected in the docs)

1. **Tests patching module globals** (`cli.claude_cli_available = fake`) couple tests to file layout; a
   patch silently stops working when code moves. Only ~15 names were involved, so it was a mechanical
   retarget, but `ORCH-PY-MODULARISE` had no step for it. Added (Phase 1 step 5, Phase 3 step 6);
   `PLAT-PY-TESTING` now says to inject instead.
2. **import-linter behavior** was misdescribed or unstated: external `forbidden_modules` must be
   top-level packages (add the sibling library to `root_packages`); checks are transitive by default;
   `ignore_imports` binds to the contract it sits under; `layers` ignores unlisted modules.
   Documented in `PLAT-PY-IMPORT-LINTER`, with a completeness-test pattern for flat libraries.
3. **The ruff selection was wrong**: no `line-length` (412 false hits at the default 88), and `PLR`
   as a whole is noise (`PLR2004`). Corrected; the README now says to adopt rule families incrementally.
4. **Compatibility re-exports can violate layering** if they point upward. New rule `PYMOD-FACADE-01`.
5. **Operations that construct their own infrastructure** can't be fixed by a behavior-preserving
   split. Added to Phase 4 as a recorded, justified exception plus a follow-up.
6. A mistake in my own doc edit (`line-length` under the wrong TOML table) was caught only because the
   documented config was re-run; every config in these docs should be run, not read.

### Still open (not done in this pass)

Orchestrator:
- ~~Two application -> infrastructure exceptions~~ and ~~no `AppError` hierarchy / exit-code table~~: closed in the
  follow-up below.
- No typed domain: values are `dict[str, Any]` throughout, and `POLICY_PROFILES` is a mutable dict
  (`PYDOM-VALUE-01`).
- Error messages still name CLI commands ("run `escape-ai initiative answer` first"), so a second surface
  (the MCP server, BLA-47) would show the wrong hint (`PYERR-SURFACE-01`, soft). Fix = carry the next step as
  data on `AppError` and let each surface phrase it.
- `entrypoints/cli/dispatch_system.py` still calls a few engine functions directly for trivial passthroughs
  (`add_route`, `set_default_policy`, `default_policy_id`); harmless but not "one use case per handler".
- Interactive flows contain logic and call the terminal directly; there is no `Prompter` port
  (`PYEP-INTERACT-01`). `terminal.py` is 34% covered and `resume.py` 67%.
- 5 functions exceed complexity defaults (C901/PLR0912/PLR0915), so those rules are not enabled.
- **Type checking was never run** (`PYTYPE-STRICT-01`): mypy/pyright strict is unverified on both repos.

Engine:
- Modules over 400 lines with no justification or split: `contracts.py` 558, `manifests.py` 557,
  `onboarding.py` 548, `cli.py` 498, `conversation.py` 447, `ai_suggestions.py` 412.
- Core modules read the clock (`reporting`, `roadmap`, `measurement`, `checkpoints`, `architecture`
  call `datetime.now`) and `registry.py` reads `os.environ` (`ESC_AI_REGISTRY`, `XDG_CONFIG_HOME`, `APPDATA`):
  `PYDOM-CLOCK-01` / `PYCOMP-CONFIG-01`. The orchestrator now reads it only through `composition.build_app`.
- 6/10/10 complexity offenders, and import order unsorted in ~26 files (not enabled in ruff).
- An uncommitted change to `esc_exec/verification_execution.py` (a failure-classification regex fix)
  pre-dates this work and was deliberately left out of every commit.

### Which docs were exercised

Exercised end to end: `ARCH-PY` (layers, dependency rules), `ARCH-PY-ENTRYPOINT` (`PYEP-RENDER-01`,
`-IO-01`, `-DISPATCH-01`), `ARCH-PY-MODULES` (size, facade), `ARCH-PY-ERROR` (swallow, assert),
`PLAT-PY-IMPORT-LINTER`, `PLAT-PY` (ruff table), `PLAT-PY-TESTING` (patch coupling), and
`ORCH-PY-MODULARISE` (used start to finish; 5 additions).
Partly exercised: `ARCH-PY-USECASE` (ports, IO), `ARCH-PY-DOMAIN` (purity, clock), `ARCH-PY-GATEWAY`
(pure-helper-in-gateway), `PLAT-PY-DI`.
**Not exercised at all:** `ARCH-PY-POLICY`, `-OBSERVABILITY` (beyond scheduler logging),
`-CONCURRENCY`, `-COMPOSITION`, `-DATASOURCE`, and `PLAT-PY-CLI`, `-MCP`, `-HTTP`, `-PERSISTENCE`,
`-SUBPROCESS`, `-TYPING`, `-PACKAGING`; and `ORCH-PY-USECASE`, `-ENTRYPOINT`, `-ADAPTER`.
These have no evidence yet and should not be marked `active` on the strength of this pass.

## Follow-up: composition object and AppError (2026-09-29)

Done on `refactor/composition-and-app-error` (3 commits in the orchestrator, 260 -> 266 tests; docs commit in the
framework), because BLA-43 (`fix` workflow) will add real failure cases and touch every handler.

**Errors** (`ARCH-PY-ERROR`): `domain/errors.py` (`AppError` -> `NotFound` / `InvalidInput`
(`UnsupportedRepository`) / `Conflict` / `Incomplete` / `Unavailable`, with an optional `hint` line);
`application/errors.py::translates_engine_errors` converts the engine's `KeyError`/`FileNotFoundError`/
`ValueError`/`OSError` at the application boundary; `entrypoints/cli/errors.py` is the single place errors become
output and an exit status (0/1/2/70, documented once), with `@guarded` replacing ~20 per-handler `try/except`
blocks and a top-level handler for bugs. Decision logic that lived in handlers (a missing-answers check that
became "incomplete", the roadmap field merge, task lookup with "did you mean", the doctor call) moved into
application operations; `doctor_check` moved out of `runtime.py` (infrastructure).

**Composition** (`ARCH-PY-COMPOSITION`): `application/app.py::App` (store, registry, scheduler and runtime
factories), `composition.py::build_app` (the only place that constructs the Store, Scheduler and runtimes),
`execute_task` takes the factories instead of building a Scheduler, and `entrypoints/cli/main.py::run(argv,
app_factory)` never imports infrastructure. Every handler and interactive flow takes an `App`.

**Result:** both `.importlinter` exceptions are gone (import-linter reported "no matches" for them). One
transitive contract now forbids domain, application and entrypoints from reaching store, runtime, scheduler or
composition: 4 contracts kept, no ignores.

**What the docs got wrong or missed** (fixed in the docs, `10f0d1a`): a CLI must parse `--db`/`--registry`
before it can build the app, so the entrypoint takes an app *factory* rather than importing the composition
root; operations needing fresh infrastructure per call need a factory on the App (`PYCOMP-FACTORY-01`);
`PYCOMP-CHOICE-01` now allows a documented generic fallback (the OpenCode runtime is exactly that); the
translate-a-library's-exceptions decorator pattern (`PYERR-LIBRARY-01`), including `str(KeyError)` returning its
repr; interactive loops may catch `AppError`; messages that name one surface's command (`PYERR-SURFACE-01`).

**Behavior changes (deliberate, in the commit messages):** an unexpected error now exits 70 with a short message
and the traceback on stderr, instead of an uncaught traceback; a missing `task.yaml` puts "did you mean" on its
own line everywhere; an unreadable answers file is reported before a bad repository id; a `KeyError`'s message
is no longer shown wrapped in quotes. Status words and exit codes 0/1/2 are unchanged.

`ARCH-PY-ERROR` and `ARCH-PY-COMPOSITION` are now `status: active` (9 of 27 Python docs).
