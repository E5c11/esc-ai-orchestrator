# Cohesive ESC AI System — Integration and Onboarding Plan

**Status:** Active
**Date:** 2026-07-18
**Objective:** Compose the architecture knowledge, execution framework, and central
orchestrator into one coherent product flow for repository onboarding, planning,
execution, verification, and resumption.

## Settled product identity

The user-facing product is **Escape AI**. The name combines the creator's `Esc`
initials with the product's purpose: escaping the mainstream pattern of unchecked AI
autonomy by making AI reasoning architecture-aware, human-guided, observable, bounded,
and resumable.

**Positioning:**

> **Escape AI — AI autonomy within boundaries you control.**

Naming conventions are locked as follows:

| Surface | Canonical name |
|---|---|
| Product and interface title | `Escape AI` |
| User-facing CLI command | `escape-ai` |
| Internal technical namespace | `esc-ai` |
| Machine configuration directory | `~/.config/esc-ai/` |
| Architecture repository/ID | `esc-ai-architecture-framework` |
| Execution repository/ID | `esc-ai-execution-framework` |
| Orchestrator repository/ID | `esc-ai-orchestrator` |

Existing schema IDs, configuration paths, package namespaces, and repository names
retain `esc-ai` for technical stability. Users interact with Escape AI; Escape AI uses
the orchestrator; the orchestrator composes the frameworks and AI providers.

## Executive summary

The envisioned flow is correct:

1. Start one CLI or interface.
2. Onboard or select a repository.
3. Scan reliably detectable repository structure.
4. Construct shared repository/component manifests with human review.
5. Generate routing indexes, dependency graphs, and profile skeletons.
6. Bootstrap a thin repository workflow package that inherits central instructions.
7. Start the interface again to plan a feature, fix, or maintenance task.
8. Produce one or more linked workflows across registered repositories.
9. Execute, observe, verify, checkpoint, resume, and measure through the orchestrator.

The current repositories provide most underlying primitives, but not this composed
experience. Significant integration work remains: framework identity and discovery,
the architecture-framework rename, a shared manifest model, an idempotent onboarding
state machine, human interview flows, instruction inheritance, and multi-repository
workflow planning.

## Who this is for

Escape AI's onboarding flow targets **new repositories, and repositories that already
substantially follow this system's manifest/index conventions.** It is not a tool for
reconciling a repository with extensive undocumented legacy structure, an
unsupported build system, or heavy pre-existing conventions that conflict with its
own. That is a distinct, harder problem this plan does not attempt to solve.

This is a deliberate scope boundary, not an accidental limitation. A repository that
needs manual scoping before it can be onboarded is a real, common case; the honest
response is for the system to detect that and say so, not to attempt reconciliation
and quietly produce a worse result than a human would have. The onboarding analysis
engine (Phase 3) already classifies every manifest as create/update/preserve and
surfaces conflicts and semantic questions — that classification is the mechanism for
detecting fit, not just a bookkeeping step. A proposal dominated by `update`/conflict
entries rather than clean `create`/`preserve` ones is the system's own signal that a
repository is a poor fit for automated onboarding.

Phase 10's `ampm-backend` pilot validates onboarding against a repository that is a
*good fit* by this definition — new or already largely compliant — not a demonstration
that this system handles arbitrary messy legacy code. It was never meant to prove that,
and claiming otherwise would be dishonest about what got tested.

## Canonical system names and responsibilities

The system consists of three cooperating products. Only two are frameworks; the third
is the running control plane.

| Product | Canonical ID | Responsibility |
|---|---|---|
| Architecture framework | `esc-ai-architecture-framework` | Engineering principles, patterns, architecture styles, platform guidance, quality rules, feature playbooks, and the Gap Protocol |
| Execution framework | `esc-ai-execution-framework` | Manifests, indexes, execution contracts, profiles, verification, reports, checkpoints, dependency impact, adapters, and efficiency measurement |
| Orchestrator | `esc-ai-orchestrator` | Unified CLI/API/UI, repository catalog, onboarding state machine, human approvals, planning, scheduling, credentials, workspaces, observation, and cross-repository coordination |

The GitHub repository has been renamed and cloned locally as
`esc-ai-architecture-framework`. Internal titles and consuming references still need
migration. The old ID should produce a migration diagnostic, not remain a permanent
second identity.

Consuming repositories remain authoritative for project-specific facts, active
workflows, exceptions, and internal architecture extensions such as
`ampm-backend-framework`.

## Composition model

```text
Escape AI (`escape-ai` CLI/UI owned by orchestrator)
  |
  +-- machine-local catalog
  |     +-- architecture framework route
  |     +-- execution framework route
  |     +-- orchestrator configuration
  |     +-- registered repository routes
  |     +-- ecosystem groupings (named sets of related repository IDs)
  |
  +-- selected repository/ies
        +-- context/project-profile.yaml       architecture defaults during migration
        +-- .esc-ai/                            repository-local Escape AI directory
              +-- esc-execution.yaml           shared repository identity
              +-- esc-index.json               first repository routing read
              +-- esc-dependencies.json        component impact graph
              +-- INSTRUCTIONS.md              thin generated inheritance pointer
              +-- components/<component-id>/   flat, keyed by stable component ID
              |     +-- esc-component.yaml     shared component identity and selectors
              |     +-- esc-index.json         bounded structural routing
              |     +-- esc-*-profile.yaml     execution/architecture policies
              +-- workflows/README.md           repository-specific workflow policy
              +-- workflows/active|archive/     durable project work (committed)
              +-- runs/<run-id>/                transient per-run task context (gitignored)
```

### Repository-local Escape AI directory

`.esc-ai/` is the single, obvious location for everything Escape AI tracks about a
repository — its shared identity/discovery manifests and indexes as much as its
ongoing work — mirroring how `.claude/` holds Claude Code's own committed,
human-authored material without the dot-prefix meaning "hidden" or "uninteresting."

The identity/discovery files — `esc-execution.yaml`, `esc-index.json`,
`esc-dependencies.json`, `INSTRUCTIONS.md`, and every per-component
`esc-component.yaml`/`esc-index.json`/`esc-*-profile.yaml` — live under `.esc-ai/`,
not at repository/component root. This reverses an earlier version of this plan,
which argued these files needed root placement "for discoverability, the same reason
`package.json` isn't inside `.npm/`." That argument's premise doesn't hold: escape-ai
never discovers a repository by scanning a directory tree for these files. It always
resolves a repository through the machine-local registry by ID first (see Machine-local
catalog), then reads a conventional relative path under an already-known root — a hard
non-goal already states "the system must never scan arbitrary parent directories for
repositories." There is no scan to be discoverable to, so the `package.json` analogy
was defending against a scenario that can't occur here.

The real cost the old layout produced, found by actually onboarding a representative
10-component repository, was the opposite problem: every component directory got 4
escape-ai files (`esc-component.yaml`, `esc-index.json`,
`esc-verification-profile.yaml`, `esc-architecture-profile.yaml`) mixed in with its
real source/build/README files. Consolidating everything under `.esc-ai/` fixes that
directly.

Per-component files are **flat under `.esc-ai/components/<component-id>/`, keyed by
the component's stable ID — not mirroring its real filesystem path**. Component IDs
are the stable identifier this system already resolves everything else by (routing,
task scope, dependency graphs); physical paths are expected to change through
refactors and renames. Mirroring a component's path under `.esc-ai/` would just
recreate the coupling-to-something-that-moves problem in a new location instead of
removing it. The component's real source location (its `path` field, e.g. `content`)
is completely unaffected by any of this: it always resolves as `repository_root /
component.path`. Only the manifest bundle's own storage location changed — the two
are decoupled by design, and a generator must never resolve one against the other's
directory.

Everything Escape AI manages as a record of *work being done*, rather than *what the
repository is*, also lives under `.esc-ai/`, alongside the identity/discovery files
above:

- `.esc-ai/workflows/README.md`, `.esc-ai/workflows/active/`,
  `.esc-ai/workflows/archive/` — the durable, human-reviewed workflow package (formerly
  `workflows/` at root). Committed.
- `.esc-ai/runs/<run-id>/` — transient per-run task context, verification plans,
  events, and run metrics, written next to the actual repository being worked on
  rather than the orchestrator process's own working directory. Regenerable from
  indexes and task specs each run; gitignored.

Durable handoff between people or agents is the job of a **checkpoint**
(`.esc-ai/workflows/active/<task-id>/checkpoint.yaml`), not of committing raw run
history. A checkpoint is a curated summary — completed steps, decisions, remaining
work, blockers, referenced artifacts — produced by promoting the parts of a run worth
keeping. Committing every `.esc-ai/runs/` attempt would duplicate that mechanism with
an uncurated, ever-growing alternative.

### Instruction precedence

The composed instruction order must be explicit and machine-readable:

1. Safety and operator policy.
2. Execution framework core instructions.
3. Architecture framework core instructions and matched profile layers.
4. Repository-specific instructions and workflow policy.
5. Component-specific manifests and profiles.
6. Active workflow/task specification.

Project-specific rules may specialize generic architecture guidance only in their
declared namespace. Conflicts must be surfaced in the context bundle; agents must not
silently choose whichever document they read last.

## Shared manifest direction

`esc-execution.yaml` (at `.esc-ai/esc-execution.yaml`) and `esc-component.yaml` (at
`.esc-ai/components/<component-id>/esc-component.yaml`) should become the shared
discovery boundary for both frameworks. They should link to architecture selection and
execution policy without embedding entire framework documents.

Repository-level information should include:

- stable repository ID, type, purpose, and route-independent identity;
- architecture and execution framework IDs and compatible schema versions;
- default platform, architecture, build, and quality selections;
- optional project-specific framework extensions;
- declared components and workflow package location; and
- canonical generated index and dependency-graph paths.

Component-level information should include:

- component identity, purpose, ownership, aliases, and paths;
- language, build project, runtime role, and architecture overrides;
- architecture-framework profile selectors/document IDs;
- execution profile paths for verification, reporting, and architecture fitness
  functions; and
- generated source facts separated from human-authored semantic facts.

During migration, `context/project-profile.yaml` remains supported. Onboarding should
import it and propose equivalent root/component selectors. It should not create a
second competing profile model indefinitely.

## Machine-local catalog

Absolute checkout paths and credentials must never be committed. Replace the current
route-only concept with a versioned machine-local system catalog, retaining backward
compatibility with `~/.config/esc-ai/repositories.yaml` during migration.

Proposed Linux location:

```text
~/.config/esc-ai/system.yaml
```

It contains logical framework/repository IDs, absolute checkout paths, orchestrator
endpoint preferences, and non-secret UI defaults. Secrets remain in environment or a
dedicated credential provider. Because the catalog is outside consuming repositories,
it does not need each repository's `.gitignore`; any optional repo-local override must
use a canonical ignored filename and be validated as uncommitted.

The catalog also supports naming an **ecosystem** — a set of repository IDs that are
commonly orchestrated together (for example `ampm: [ampm-backend, ampm-mobile,
ampm-contracts]`). Ecosystem membership is static, user-declared grouping data, so it
belongs in the catalog beside `repositories:`/`frameworks:`, validated so every member
ID is itself a registered repository. It is a convenience for routing and
multi-repository planning, not a source of truth: the *live* coordination view of an
in-flight cross-repository initiative (task graph, cross-repo status) is run state, not
catalog data, and belongs in the orchestrator's existing persistence layer (new
`initiatives`/`initiative_tasks` tables) rather than a second file-based cache.

Missing or stale routes must produce an exact repair action. The system must never scan
arbitrary parent directories for repositories.

## Unified CLI and interface

The orchestrator should own a single command named `escape-ai`. Interactive mode is the
default for humans; equivalent non-interactive subcommands are mandatory for CI,
scripts, and AI tools.

```text
$ escape-ai

What would you like to do?
  1. Onboard a repository
  2. Plan new work
  3. Resume active work
  4. Observe a run
  5. Configure system
  6. Validate the system
```

The first interface can be a terminal wizard. A web or JetBrains UI should consume the
same orchestrator API and state machine later; business logic must not live in prompts
or terminal rendering.

Only **Onboard a repository** and **Plan new work** are multi-step negotiated proposals
that can be interrupted mid-negotiation and resumed later — they get the full typed
state-machine treatment (see API/state-machine boundaries). **Resume active work**,
**Observe a run**, **Configure system**, and **Validate the system** are simpler
direct-action flows — list, select, act — with no pending-question state to track, and
do not need their own state machine.

**Configure system** is the front door for machine-local, not per-repository, setup:

- providers/runtimes — adapter, model, endpoint;
- repositories and ecosystems — register, remove, group;
- permission policies — author or select a named `policy.yaml` profile;
- credentials — a pointer to the environment or credential provider, never a value
  entered or stored inline.

Policies configured here become the default applied when planning or executing a task;
a task may still override its policy explicitly. This is also the mechanism that must
exist before the policy-to-tool-grant enforcement gap (see Open follow-up in the Phase 1
workflow tracking docs) is closed — a policy a user configures here has to actually
change what an execution run is permitted to do.

## Repository onboarding flow

Onboarding is an idempotent, resumable state machine rather than one opaque scan.

### 1. Select and register

Interactive questions:

- Repository path?
- Proposed stable repository ID correct?
- Is this a new adoption or repair/re-onboarding?
- Which architecture/execution framework versions should be used?
- Does the repository have a project-specific framework extension?

The orchestrator validates the Git root and writes the route only to the machine-local
catalog after confirmation.

### 2. Detect structure

Build-system adapters detect reliably derivable facts:

- build system and declared modules/packages;
- languages and source/test/resource roots;
- build projects and project dependencies;
- test/report locations and existing workflow directories;
- current project profile and framework references; and
- existing manifests, indexes, profiles, and stale state.

“Scan thoroughly” means exhaustive within declared build roots and supported adapters,
not reading every source file into AI context. Deterministic tools collect structure;
AI reads bounded evidence only where semantics are required.

### 3. Produce onboarding proposal

Before changing the repository, show a proposal containing:

- detected repository identity and components;
- files to create, update, preserve, or deprecate;
- generated facts and their evidence;
- semantic questions requiring a human;
- current instruction/profile conflicts;
- unsupported build syntax or uncertain module boundaries; and
- an explicit diff/write approval boundary.

The proposal is retained so onboarding can resume without rescanning unchanged inputs.

### 4. Human-assisted manifest construction

For each component, the interface presents detected evidence and asks only questions
that cannot be derived safely:

- What is this component's purpose?
- Which domains and concerns does it own?
- Is the proposed type/platform/architecture correct?
- Which aliases should route tasks here?
- Are detected dependency directions intentional?
- Which architecture-framework layers and project extension rules apply?
- Are generated verification/report commands correct?

Answers are written into the shared component manifest or the appropriate colocated
profile. Generated and human-owned fields remain distinguishable so regeneration does
not overwrite intent.

### 5. Generate derived artifacts

Once required semantic fields are complete:

- generate root and component `esc-index.json` files;
- generate `esc-dependencies.json`;
- generate verification and report profiles;
- generate an incomplete architecture-profile skeleton and request authored rules;
- validate architecture framework selectors and referenced document IDs;
- validate all framework routes, manifests, profiles, graphs, and indexes; and
- produce a machine-readable onboarding report.

Missing architecture or verification semantics block readiness. There is no implicit
global fallback.

### 6. Bootstrap workflow inheritance

Onboarding creates or minimally adapts:

```text
.esc-ai/INSTRUCTIONS.md
.esc-ai/workflows/README.md
.esc-ai/workflows/active/README.md
.esc-ai/workflows/archive/README.md
```

Generated `INSTRUCTIONS.md` is a thin pointer; it does not copy either framework. Since
`workflows/README.md` and `INSTRUCTIONS.md` are now siblings under the same `.esc-ai/`
parent, `INSTRUCTIONS.md`'s cross-reference to it is a same-directory-relative link
(`workflows/README.md`), not a repo-root-relative one.
`.esc-ai/workflows/README.md` contains only repository-specific policy such as:

- project-specific framework extensions and precedence;
- workflow naming/location rules;
- repository-specific final gates and commit conventions;
- deployment or environment constraints;
- where active business roadmaps live; and
- exceptions that cannot belong in a generic framework.

It references canonical architecture Gap Protocol and execution workflow instructions
by stable framework ID/document ID. Existing workflow content is migrated through a
reviewed proposal, never overwritten wholesale.

### 7. Readiness review

The repository reaches `ready` only when:

- routes resolve;
- framework versions and IDs are compatible;
- manifests and required semantics validate;
- indexes and dependency graph are current;
- every participating component has required profiles;
- architecture selectors resolve to real, non-stub documents;
- workflow inheritance files exist and contain no duplicated core instructions; and
- the human approves the onboarding report and repository diff.

The tool then prints the exact files that should be committed. It does not commit
without explicit instruction.

## Planning new work

After onboarding:

```text
$ escape-ai
  Plan new work

Work type: feature | fix | refactor | maintenance | investigation
Objective: ...
Repositories: auto-suggested, then confirmed
```

### Planning conversation

The orchestrator:

1. Loads registered repository root indexes first.
2. Routes the objective to candidate components.
3. Loads matched architecture-framework profiles and feature playbooks.
4. Loads repository workflow policy and project-specific extensions.
5. Identifies cross-repository dependencies and missing routes.
6. Asks the user for product decisions, scope boundaries, rollout needs, and completion
   conditions that cannot be derived.
7. Produces a proposed initiative and task graph before writing workflows.

Step 6 (`planning.awaiting-product-input`) is where the interaction stops being a fixed
menu and becomes free-form conversation — but it is conversation with the *configured
runtime*, not a raw, unmediated chat window onto whichever AI provider happens to be
behind it. The user always talks to the orchestrator; the orchestrator relays each turn
to the configured runtime and relays the response back. The runtime's own chat/API is
never exposed directly. That mediation is what makes "free-form" and "bounded"
compatible instead of contradictory — the boundary is not the AI choosing to stay in
scope, it is the orchestrator enforcing the following on every turn:

1. **Tool/permission grant, not AI good behavior.** The active policy decides what the
   runtime is *capable of* that turn — during planning this defaults to read/glob/grep
   only, no bash/edit/write/network unless the policy explicitly allows it. This is the
   tool grant passed to the runtime, not a prompt instruction the AI could ignore; a
   disallowed tool call fails at the runtime boundary regardless of what the
   conversation contains.
2. **Context scoping, not open filesystem access.** The runtime only ever sees the
   indexes, architecture profiles, and workflow policy the orchestrator already routed
   and loaded for the selected repositories — it has nothing to reference or act on
   outside that.
3. **Output gating, not trust in the transcript.** No matter how the conversation
   proceeds, its only output is a *proposed* task graph and diff. Nothing is written or
   committed until the human approves that structured proposal — the conversation
   produces a proposal, never a direct write.
4. **Full event logging.** Every turn is a portable event, like any other run. Bounded
   means observable, not just prevented — a complete record survives even if something
   goes wrong.
5. **A defined lifecycle, not an open chat.** The conversation lives inside one state
   with a start and an end (`planning.awaiting-product-input` →
   `planning.awaiting-plan-approval`), not an infinite window — it has a job to finish.

None of this multi-turn conversation exists yet. What is built today
(`OpenCodeAdapter.execute`) is single-shot: the orchestrator builds one fully-formed
prompt from a fixed task context, sends it, and gets one response back — that is
execution-time context-gathering, not planning-time back-and-forth dialogue. Real
multi-turn planning conversation, where the orchestrator re-checks policy and re-scopes
context on every turn rather than once, is unbuilt; it belongs to Phase 7.

### Single-repository output

Create:

```text
.esc-ai/workflows/active/<task-id>/task.yaml
.esc-ai/workflows/active/<task-id>/README.md
```

The README is a human view generated from or aligned with the task specification. It
contains the objective, decisions, phases, referenced architecture document IDs,
verification gates, and completion conditions. Durable checkpoints live beside it.

### Multi-repository output

Create one initiative ID and one linked task per repository. Each repository commits
only its own workflow and references other tasks through stable repository/task IDs,
never absolute paths.

```text
initiative: feature-user-export
  ampm-contracts/task-contract
      -> ampm-backend/task-api
          -> ampm-mobile/task-client
```

The orchestrator owns the live coordination view, but committed repository workflows
remain sufficient to resume independently. Cross-repository ordering, shared decisions,
compatibility gates, and artifact/version handoffs must be explicit.

Cross-repository initiatives commonly span a declared ecosystem (see Machine-local
catalog), but an initiative may include repositories outside any declared ecosystem
too — ecosystem membership is a routing convenience, not a requirement for an
initiative to exist.

The user approves the task graph and per-repository diffs before files are written.

## Execution and resumption flow

For an approved task, the orchestrator should:

1. Generate bounded task context from current indexes.
2. Resolve architecture framework documents from shared manifest selectors.
3. Build dependency-aware progressive verification plans.
4. Select runtime, workspace, permissions, and approval policy.
5. Execute and stream portable events.
6. Store compact summaries with full artifact references.
7. Stop on failed gates or missing framework coverage.
8. Produce a transient checkpoint candidate on interruption/failure.
9. Promote reviewed durable state into the repository workflow.
10. Record efficiency metrics and support comparable cohort analysis.

Per-run artifacts (task context, verification plan, events, run metrics) are written
to `.esc-ai/runs/<run-id>/` inside the target repository, resolved from the task's
declared repository — never a location relative to wherever the orchestrator process
happens to run. Checkpoints, once promoted, are the durable artifact that lives in
`.esc-ai/workflows/active/<task-id>/`.

Starting `escape-ai` later should show active tasks and checkpoints across registered
repositories, allowing the user to resume without reconstructing context.

## API/state-machine boundaries

Only onboarding and planning are typed state machines — they are negotiated proposals
that can be interrupted and resumed. Resume/Observe/Configure/Validate (see Unified CLI
and interface) are direct-action flows and do not get their own state machine.

Interactive questions must be driven by typed states so any interface can render them:

```text
onboarding.detected
onboarding.awaiting-semantic-input
onboarding.awaiting-write-approval
onboarding.validating
onboarding.ready | onboarding.incomplete

planning.routing
planning.awaiting-product-input
planning.awaiting-plan-approval
planning.writing-workflows
planning.ready
```

Each state records input digests, answered questions, pending questions, proposed
writes, validation results, and resumable next actions. Re-running a completed step
with unchanged inputs must be safe and byte-identical.

## Implementation sequence

### Phase 0 — Naming and ownership contracts

- Treat the GitHub and local checkout rename as complete. **Done.**
- Update titles, package/repository metadata, route IDs, links, schema IDs where
  appropriate, and consuming references. **Done.**
- Publish the three-product ownership matrix and conflict precedence.
- Add migration diagnostics for the old framework ID. **Done** — `resolve_route` names
  the exact renamed ID, and `validate_registry`/`validate_repository` mark it `STALE`.

**Exit:** every repository refers to the canonical IDs and all existing validation
suites pass.

### Phase 1 — Framework composition protocol — **Complete**

- Introduce `.esc-ai/` as the repository-local Escape AI directory; move `workflows/`
  to `.esc-ai/workflows/` in all three existing repositories and every reference to it.
  **Done.**
- Resolve per-task `.esc-ai/runs/<run-id>/` output from the task's target repository
  instead of a single orchestrator-process-relative output root. **Done.**
- Add `ecosystems:` grouping to the machine-local catalog schema and registry. **Done.**
- Define a versioned framework descriptor for architecture and execution frameworks.
  **Done** — `esc-framework.yaml` + compatible-major-version checking.
- Extend repository/component manifests with framework versions, architecture
  selectors, extensions, workflow location, and derived-artifact declarations.
  **Done** for architecture selectors (`architecture: {profile_ids: [...]}`); project
  extensions and derived-artifact declarations remain open for later phases.
- Define instruction resolution and conflict reporting. **Done** as a thin slice — the
  6-level precedence order and the one named conflict case (reserved document-ID
  prefixes); not yet wired into a live task context, since the other instruction
  sources aren't structured data until planning/execution (Phase 7/8) produce them.
- Implement route resolution and compatibility validation for both frameworks. **Done.**
- Implement architecture profile/index lookup as a library callable by the
  orchestrator. **Done** — `esc_exec/architecture_lookup.py`, consuming the
  architecture framework's `index.json` as a data contract, no code dependency.

**Exit:** a task context identifies the exact execution and architecture documents to
load without hard-coded checkout paths, and per-run artifacts land inside the target
repository's `.esc-ai/` directory rather than the orchestrator's own working directory.
Both conditions hold: `build_task_context` resolves each component's declared
architecture documents, and run artifacts resolve to `<repository>/.esc-ai/runs/`.

### Phase 2 — Unified machine-local catalog — **Complete**

- Define `system.yaml` schema and migration from `repositories.yaml`. **Done** —
  `esc_exec/registry.py`'s `validate_registry` enforces the de facto schema
  (`schema_version`, `repositories`/`frameworks`/`ecosystems`/`orchestrator`/`ui`/
  `credentials`/`providers` as the only known top-level fields); `default_registry_path()`
  resolves the real machine-local location per platform (`ESC_AI_REGISTRY` env var
  override, else `~/Library/Application Support/esc-ai/system.yaml` on macOS,
  `%APPDATA%\esc-ai\system.yaml` on Windows, `$XDG_CONFIG_HOME/esc-ai/system.yaml`
  — i.e. `~/.config/esc-ai/system.yaml` by default — on Linux). `migrate_legacy_registry`
  is the explicit, non-interactive, never-automatic path from the old
  `repositories.yaml` filename, exposed as `esc-exec system migrate`.
- Support framework, repository, and optional extension routes. **Done** — `add_route`
  handles repository/framework routes generically by category; `add_ecosystem` groups
  repository IDs under a named ecosystem (the "optional extension" route this item
  meant).
- Add stale/missing route repair actions and credential-provider references. **Done**
  — `validate_registry` detects a route pointing at a missing directory and emits the
  exact repair command (`esc-exec route add <kind> <id> /new/path`), and separately
  detects a framework ID that was renamed out from under an existing reference
  (`RENAMED_FRAMEWORK_IDS`) with its own exact repair message. `credentials.provider`
  is the credential-provider reference this item meant — a pointer to which secrets
  provider is configured (e.g. `env`, a secrets-manager name), deliberately never an
  actual secret value.
- Keep catalog operations available as non-interactive commands. **Done** —
  `esc-exec route add/resolve/list/validate/ecosystem` and `esc-exec system migrate`,
  all argument-driven, no prompts.

**Exit:** one catalog resolves all three products and every registered repository. Met
— the same `system.yaml`/route-resolution mechanism (`resolve_route`) is what both
`esc-ai-execution-framework` and `esc-ai-orchestrator` use to resolve every repository
and framework route in this document's other phases, and what `BACKDOOR.md` documents
as the standing machine-local catalog across sessions.

### Phase 3 — Onboarding analysis engine — **Complete**

- Generalize build-system adapter interfaces. **Done** — `BuildSystemAdapter` protocol
  and dispatch in the execution framework; one concrete adapter (Gradle) today,
  extensible without changing calling code.
- Implement idempotent repository analysis and proposal contracts. **Done** —
  `analyze_repository` classifies each manifest as create/update/preserve/deprecate
  with evidence and computes a stable input digest; a new `onboarding-proposal`
  contract kind carries the result.
- Detect existing profiles, instructions, workflow packages, and conflicts. **Done**
  for presence detection (`INSTRUCTIONS.md`, `.esc-ai/workflows/`,
  `context/project-profile.yaml`) and manifest drift; deeper conflict reconciliation
  is bounded by this system's declared scope (see the new "Who this is for" section) —
  it detects and reports drift, it does not attempt to resolve arbitrary pre-existing
  conventions.
- Persist onboarding state and input digests in the orchestrator. **Done** — a new
  `onboarding_proposals` table plus `POST /repositories/{id}/analyze` and
  `GET /repositories/{id}/proposal`.
- Separate read-only proposal from approved repository writes. **Done** — verified
  both in tests (file-listing snapshot before/after) and manually against a real
  checkout.

**Exit:** `escape-ai repository analyze /path` produces a complete proposal without
modifying the repository. Satisfied today via `esc-exec repository analyze <path>`
(execution framework, works now) and the orchestrator's HTTP endpoints above; the
unified `escape-ai` CLI itself is still Phase 6.

### Phase 4 — Human-assisted manifest/profile construction — **Complete**

- Define typed semantic questions and evidence attachments. **Done** — one bounded
  question per component (purpose, or frameworks/targets when needed), not a
  question per possible field.
- Merge answers into generated manifests without overwriting authored fields.
  **Done** — `apply_onboarding_answers`, the first onboarding step that writes to a
  repository.
- Import existing `context/project-profile.yaml` and framework references. **Done**
  — `import_project_profile`; a repository that already has one needs little to no
  semantic questioning.
- Generate/complete verification, report, and architecture profile flows. **Done** —
  auto-generated for any component that lacks one, as part of applying answers.
- Detect unresolved architecture stubs through the Gap Protocol. **Done** — surfaced
  in the result, never silently treated as complete.
- Recommend `architecture.profile_ids`, not a template library: extend the
  architecture framework's existing `PROFILE_DOC_MAP`-style signal-to-doc-ID mapping
  (`tools/lookup.py`) — already proven, already maintained — to also emit a
  suggested `profile_ids` list from Phase 3's detected signals, instead of building a
  separate template content type, schema, and matching engine. A poor/empty
  suggestion feeds the Who this is for readiness judgment. **Done** —
  `profile-doc-map.json` export + `suggest_profile_ids`.
- For a brand-new project (no detected signals to match against), reuse the planning
  conversation mechanism (see Planning conversation) rather than a separate flow:
  describe the project, get curated profile options from the existing
  platform/architecture/frameworks enums, confirm, and let the same signal-to-doc-ID
  mapping resolve `profile_ids` from there. **Not yet built** — depends on Phase 7's
  planning conversation existing first.

**Exit:** an incomplete proposal can resume after user input and reach valid shared
manifests/profiles.

### Phase 5 — Workflow inheritance bootstrap — **Complete**

- Define thin instruction-pointer templates. **Done** — `INSTRUCTIONS.md` referencing
  both frameworks by stable ID, no duplicated content.
- Define repository-specific workflow policy schema/template. **Done** —
  `schemas/workflow-policy.schema.yaml` (structured frontmatter) +
  `.esc-ai/workflows/README.md`'s prose skeleton.
- Analyze and migrate existing workflow READMEs without deleting project history.
  **Done** as create-if-missing/report-if-existing — a repository with mature,
  hand-written content is left completely untouched and reported back, not migrated
  by template (a full migration-diff proposal for existing content remains future
  work if evidence from Phase 10's pilot shows it's needed).
- Validate that core framework instructions are referenced rather than duplicated.
  **Done** as an advisory self-check on freshly generated files only (never on
  pre-existing content, which isn't reliably checkable this way).
- Bootstrap active/archive directories idempotently. **Done** —
  `bootstrap_workflow_inheritance`, verified idempotent and non-destructive by test.

**Exit:** a newly onboarded repository has a minimal, valid workflow package; an
existing repository receives a reviewed migration diff. First half holds; the
"reviewed migration diff" for a repository with existing content is the
future-work item noted above, not yet built.

### Phase 6 — Interactive CLI onboarding wizard — **Complete**

- Add the `escape-ai` top-level interactive menu and Escape AI product branding.
  **Done** — new `escape-ai` command, self-contained (no HTTP daemon required),
  separate from `esc-orchestrator`. Only "Onboard a repository" is functional; the
  other five items say so honestly and exit cleanly rather than faking behavior —
  they're later phases.
- Render onboarding state-machine questions and proposals. **Done** — pure rendering
  functions over plain data, tested independently of the real onboarding logic they
  render.
- Add non-interactive equivalents: `repository add`, `analyze`, `answer`, `apply`,
  `validate`, and `status`. **Done** — `answer` stages answers without writing;
  `apply` is the one deliberate write step, genuinely separate actions, not the same
  one under two names.
- Add cancellation, resumption, and dry-run behavior. **Done** — Ctrl-C/EOF at any
  prompt exits cleanly with nothing written; re-onboarding with an unchanged input
  digest detects the existing proposal and, if already applied, asks before
  redoing it; analysis and proposal viewing are unambiguously previews ("Nothing
  has been committed" prints after every apply).
- Test terminal output separately from onboarding business logic. **Done** — the
  business logic was already decoupled from Phase 3/4/5's work; this phase only
  needed wizard-layer tests, not a refactor.

**Exit:** a user can onboard a representative new repository from path to approved,
commit-ready files through one interface. Verified against two real temp Gradle
repositories, both non-interactively (`add`→`analyze`→`answer`→`apply`→`validate`→
`status`) and interactively (scripted `input()` through the full menu → prompts →
apply flow) — not just that the test suite passes.

### Phase 7 — Feature/fix planning and workflow generation — **Complete** (typed-question scope)

- Define initiative and linked task-graph contracts. **Done** —
  `schemas/initiative.schema.yaml` plus an optional `task.initiative` extension on
  the task-specification schema, registered as an `initiative` contract kind.
- Add task-type selection and conversational product questions. **Done** for
  selection and typed questions; **not done** as a live conversation -- see below.
- Build multi-turn planning conversation: the orchestrator mediates every turn to the
  configured runtime, re-checking the active policy's tool grant and re-scoping loaded
  context each turn rather than once (see Planning conversation). **Deliberately not
  built.** Still gated on the policy-to-tool-grant enforcement gap (flagged since
  Phase 1, still open) -- building a "bounded conversation" with no real enforcement
  behind it would be the same false-safety problem this project avoids elsewhere.
  `esc_exec.planning.planning_questions` is the typed-question substitute: the same
  bounded, non-conversational pattern onboarding already proved out, answering
  exactly what the plan says can't be derived (component selection, scope boundary,
  completion conditions, rollout needs) without requiring a live AI conversation to
  turn an objective into a task graph. Upgrading this to a real mediated conversation
  remains future work, gated on the enforcement fix landing.
- Route across registered repository indexes and architecture playbooks. **Done** for
  indexes (`route_objective` reuses the existing keyword-routing index lookup);
  architecture playbook routing beyond a component's own `architecture.profile_ids`
  is not yet a separate step.
- Generate reviewed single-repository workflow packages. **Done** —
  `generate_single_repository_workflow` validates every referenced component
  against the repository's own index before writing `task.yaml`/`README.md`.
- Generate linked multi-repository workflows with ordering and handoff contracts.
  **Done** — `generate_multi_repository_workflow` chains each repository's task to
  the previous one in declared order (the plan's own contracts → backend → mobile
  example is exactly this linear shape), referencing other tasks by stable
  repository/task ID, never an absolute path.
- Validate all referenced repository/framework IDs before writing. **Done** — every
  repository ID, task ID, component reference, and `depends_on` cross-reference is
  validated before a single file is written to any repository; a bad reference in
  one repository blocks writes to every repository in the initiative, not just the
  bad one.

**Exit:** an approved feature or fix produces complete, independently resumable
workflows in every participating repository. Verified end to end via the real
`escape-ai plan draft/answer/apply` chain for both a single-repository and a
two-repository initiative (see each repo's Phase 7 tracking doc for the specifics).

### Phase 8 — Integrated execution lifecycle — **Complete**

- Connect approved task graphs to scheduler runs. **Done** — `escape-ai task run
  <repository-id> <task-id> [--yes]` resolves a task's stored contracts and submits
  through the same `Scheduler`/`Store`/runtime path the HTTP daemon already used
  internally; follows the established preview-then-`--yes` boundary (no submission
  without `--yes`).
- Resolve architecture instructions into bounded task context. **Done** — the
  execution framework's `OpenCodeAdapter._instruction_bundle` composes the plan's
  six precedence levels (policy, execution-framework core, architecture
  documents, workflow policy, component manifests, active task) via
  `order_instruction_bundle`, writing `instruction-bundle.json` per run; see that
  repo's Phase 8 tracking doc. The extension-namespace-conflict check is wired in
  but currently a no-op — no manifest field yet enumerates specific document IDs
  for a project extension.
- Add approval gates, retry/attempt semantics, and checkpoint promotion. **Done** —
  `Store.submit()` now upserts the `tasks` table so retrying the same task id never
  raises a uniqueness error; a new `task_attempts` table tracks attempt counts;
  `task promote-checkpoint <repository-id> <task-id> [--yes]` promotes a transient
  failure candidate (`.esc-ai/runs/<run-id>/checkpoint.yaml`) into the durable,
  committable `.esc-ai/workflows/active/<task-id>/checkpoint.yaml`.
- Provide active-work/resume views across repositories. **Done** — `escape-ai resume
  [--json]` and interactive menu choice "3" show every known task's latest run
  status, attempt count, and checkpoint presence across every registered
  repository in one place.
- Retain metrics for onboarding, planning, execution, and rework. **Done** — the
  execution framework's new `process_metrics()` (elapsed time, questions asked vs.
  answered) is now surfaced through `repository status`/`plan status`; execution/
  rework metrics already existed from earlier phases.

Workspace, adapter, and policy selection are deliberately **not** part of this
phase's scope: `default_workspace`/`default_adapter`/`default_policy` are
placeholder defaults (a workspace rooted at the repository path, the
`OpenCodeAdapter`, and a conservative read-only policy), pending real "Configure
system" support that doesn't exist yet. `task run`'s preview output says this
explicitly rather than presenting placeholder configuration as finished. The
policy-to-tool-grant enforcement gap flagged above was closed in a prior round
(tool grants are now actually derived from `policy.yaml` permissions, not a
hardcoded constant) — this phase builds on that fix rather than deferring it
further.

**Exit:** the user can plan, execute, stop, and resume a linked task through the same
CLI/API. Verified end to end against a real temp Gradle repository (`repository
add/analyze/answer/apply` → `plan draft/answer/apply` → `resume` → `task run`
preview) and, since no live OpenCode server was available in this environment, the
execute → fail → checkpoint → promote → retry → succeed → resume cycle was verified
directly against the CLI's own functions with an injected fake runtime (see each
repo's Phase 8 tracking doc for specifics) — live-server behavior remains
unverified.

### Phase 9 — Optional richer interfaces

- Stabilize the API/state contracts first.
- Build a web or JetBrains UI without duplicating orchestration logic.
- Display repository readiness, manifest questions, task graphs, approvals, runs,
  checkpoints, and metrics.

**Exit:** alternative interfaces are interchangeable clients of the same orchestrator.

### Phase 10 — Migration and evidence

- Pilot full onboarding against `ampm-backend` without destroying its mature workflow
  history. **Done** — re-migrated by hand to the `.esc-ai/`-consolidated layout (23
  old-location tracked files removed, all 9 components' human-authored purposes
  recovered from git history and preserved); `repository validate` all-VALID;
  uncommitted, sitting as a reviewable diff.
- Pilot a smaller new repository to prove clean bootstrap behavior. **Done** —
  `/home/emmanuel/StudioProjects/CatchMeIfYouCan` (fresh KMP Android app, 6 Gradle
  modules, no prior Escape AI adoption). `repository analyze` → `answer` (12 semantic
  questions answered from the repo's own README/module map/`build.gradle.kts`, not
  fabricated) → `apply` produced a clean, fully valid manifest set on the first pass;
  `repository validate` all-VALID. Uncommitted, sitting as a reviewable diff.
  - Evidence found and fixed: this repo builds exclusively with Gradle's type-safe
    project accessors (`implementation(projects.core.common)`, enabled via
    `enableFeaturePreview("TYPESAFE_PROJECT_ACCESSORS")`), which
    `esc_exec/dependencies.py`'s `PROJECT_DEPENDENCY` regex — written against
    `ampm-backend`'s string-literal `project(":x")` style — didn't recognize at all,
    silently producing a 0-edge dependency graph despite 11 real inter-module
    dependencies. Fixed by adding `TYPESAFE_PROJECT_DEPENDENCY` plus
    `_project_path_to_accessor`, which reproduces Gradle's own segment-to-camelCase
    accessor naming from each component's already-known declared project path (no
    guessing from the accessor text alone, which would be ambiguous). Verified against
    the real repo (all 11 edges now present, matching the module map) and by new
    regression tests in `test_dependencies.py`. This is exactly the kind of
    evidence-driven refinement this phase exists to surface — a second real repository
    with a different but common Gradle convention exposed a gap the first pilot
    couldn't.
- Pilot one linked multi-repository feature. **Done** — the `ampm` ecosystem has 10+
  repositories; rather than onboard all of them, scoped to the real 3-repo slice that
  already exists as an in-flight cross-repo dependency: `ampm-contracts` (shared
  DTO/path-constant library, Wave 1 "user profile" published) consumed by
  `ampm-backend` (source of truth) and `AMPM` (28-module KMP mobile app). Onboarded
  `ampm-contracts` and `AMPM` fresh; drafted a real next initiative —
  `ampm-contracts-wave2-auth-config`, migrating the Auth + Config DTOs per
  `ampm-contracts`'s own deferred "Waves 2-7" plan — via `plan draft` → `answer` →
  `apply`. Produced correctly-chained per-repo `task.yaml` files (`ampm-contracts` →
  `ampm-backend` → `AMPM`, each `depends_on` the previous) with real, resolvable
  component scopes; `build_task_context` successfully resolved routing, search_roots,
  and architecture-framework documents for the `AMPM` task against real data. Not
  executed (no task run) — this validates the planning/task-graph mechanism itself,
  not the migration. Nothing committed in `ampm-contracts`/`ampm-backend`/`AMPM`.
  - Evidence found and fixed (`esc_exec/gradle.py`, `esc_exec/dependencies.py`):
    `ampm-contracts` is a single-module Gradle repository (no `include(...)`
    subprojects) — `detect_gradle_repository` only ever looked for included
    subprojects, so it detected zero components and could never be onboarded at all.
    Fixed by treating the root project as the sole component when no includes exist.
  - Evidence found and fixed (`esc_exec/gradle.py`, `esc_exec/indexing.py`): every KMP
    component in both `CatchMeIfYouCan` and `AMPM` (56 components across the two)
    uses `src/commonMain/kotlin`, `src/androidMain/kotlin`, etc., never the plain JVM
    `src/main/kotlin`/`src/test/kotlin` layout `component_structure` assumed — so
    every KMP component's manifest had empty `search_roots`, giving an AI agent no
    indication of where to look. Fixed by detecting per-source-set paths
    (`source_<Main>`/`tests_<Test>`) and having index generation scan/merge across
    all of them. This is the same "second real repo, different real convention"
    pattern as the type-safe-accessor fix above, just found one repo later.
  - Evidence found, not fixed (recorded for later prioritization): when a repository
    already has a `context/project-profile.yaml`, `_architecture_signals` applies
    that one repository-wide framework list to *every* component identically, with
    no per-component override path surfaced through `analyze`'s questions (though
    `apply_onboarding_answers` does honor an explicit per-component `frameworks`
    answer if one is supplied programmatically). At `AMPM`'s real scale (28
    components) this produced identical `PLAT-MOB-HTTP`/`PLAT-MOB-ROOM`/
    `PLAT-MOB-FIREBASE` suggestions for modules that plainly don't touch Room or
    Firebase (e.g. `core-designsystem`, `tools-detekt-rules`). Worked around for this
    pilot by hand-supplying accurate per-component `frameworks` answers grounded in
    each module's real `build.gradle.kts` dependencies; the underlying default
    behavior is still imprecise at multi-module scale and worth revisiting.
- Compare context/tool/token/rework metrics against representative prior workflows.
  Blocked — needs a working AI provider connection with frictionless subscription-based
  execution, which no current adapter provides (see `native-cli-provider-adapters.md`).
- Refine prompts and defaults only from observed evidence.

**Exit:** the cohesive flow is validated on existing, new, and cross-repository cases.
Existing-, new-, and cross-repository cases done; only the efficiency comparison
remains, blocked on a working provider adapter.

## Decisions required before implementation

1. Decide whether `system.yaml` replaces or wraps the existing route registry.
2. **Decided:** compatible major version. A repository manifest declares a framework's
   major version; the resolver accepts any checked-out minor/patch sharing that major.
   Matches how the frameworks already evolve — additive, non-breaking minor bumps
   (e.g. `document.yaml` 1.1 → 1.2 added enum values without breaking consumers).
3. Decide whether `context/project-profile.yaml` is migrated immediately or supported
   through a deprecation window.
4. **Decided:** structured YAML frontmatter for the genuinely enumerable fields
   (project-specific extension reference, precedence, final-gate commands), free-form
   Markdown prose for the rest (deployment constraints, exceptions, roadmap
   pointers) — matching the frontmatter-plus-prose convention the architecture
   framework's own documents already use. See `schemas/workflow-policy.schema.yaml`.
5. Define the canonical initiative/task ID strategy across repositories.
6. Decide whether workflow writes are applied directly after approval or first emitted
   as a patch bundle.
7. Select the second, smaller repository for clean onboarding validation.
8. Configure a working runtime provider before measuring real end-to-end efficiency.
9. **Decided:** `workflows/` moves under `.esc-ai/workflows/`, consolidated with the
   new `.esc-ai/runs/` — one obvious repository-local root for everything Escape AI
   tracks about ongoing work, rather than splitting activity/history across a visible
   and a hidden location. **Superseded:** the identity/discovery manifests
   (`esc-execution.yaml`, `esc-index.json`, `esc-dependencies.json`, `INSTRUCTIONS.md`,
   and every per-component manifest/index/profile) also moved under `.esc-ai/` —
   flat and keyed by stable component ID for the per-component files — once real
   onboarding evidence showed the root-placement argument's premise didn't hold (see
   Repository-local Escape AI directory).
10. **Decided:** repositories may be grouped into a named ecosystem in the
    machine-local catalog; live cross-repository coordination state belongs in the
    orchestrator's own persistence layer, not a second file-based cache (see
    Machine-local catalog).

## Non-goals and safeguards

- Do not ingest every source file during onboarding.
- Do not infer business purpose, ownership, or architecture policy without review.
- Do not copy central framework instructions into every repository.
- Do not store absolute machine paths or credentials in committed files.
- Do not silently skip missing profiles, framework documents, consumers, or routes.
- Do not overwrite mature workflow packages with a generic template.
- Do not create cross-repository workflows that require the orchestrator database to be
  understood; committed tasks must carry stable references.
- Do not make the terminal wizard the system architecture; it is one API client.
- Do not claim efficiency gains without comparable provider-backed measurements.
- Do not commit transient `.esc-ai/runs/` history as a substitute for curated
  checkpoint promotion.
- Do not attempt to reconcile a repository with extensive pre-existing conventions
  that conflict with this system's own (see Who this is for); detect the drift and
  decline, rather than quietly producing a worse result than a human would have.

## Recommended next task

Phases 0 through 8 are all complete — see each phase's entry in Implementation
sequence for what shipped and what's honestly still open within each (Phase 5's
migration-diff handling for a repository with pre-existing content is deferred
pending Phase 10 evidence; Phase 6's index/dependency-graph staleness gap in
`apply_onboarding_answers` was found and fixed shortly after that phase landed;
Phase 7 built the typed-question substitute for planning, not the live conversation,
which stays gated on the policy-to-tool-grant enforcement gap; Phase 8's workspace/
adapter/policy selection is a placeholder pending real "Configure system" support,
and its execute/retry/promote cycle was verified against an injected fake runtime
rather than a live OpenCode server, since none was available in this environment).
`ampm-backend` migration was intentionally left out of this work; it belongs to
Phase 10's pilot validation against a repository that is a good fit by the Who this
is for definition, so it's still open but not blocking.

A user can now take an approved, independently resumable task graph — produced by
`escape-ai plan draft/answer/apply` — all the way through execution: submit it to
the real scheduler/runtime, retry a failed attempt, promote a failure into a durable
checkpoint, and see active work and its status across every registered repository,
all through `escape-ai task run`/`task promote-checkpoint`/`resume`. That closes out
onboarding, planning, and core execution (Phases 0-8); everything from here is about
richer interfaces on top of the same orchestrator, not new orchestration behavior.

Next: **Phase 9 — Optional richer interfaces.**

1. Stabilize the API/state contracts first.
2. Build a web or JetBrains UI without duplicating orchestration logic.
3. Display repository readiness, manifest questions, task graphs, approvals, runs,
   checkpoints, and metrics.

Do not start Phase 9 as part of this task — it is scoped separately.
