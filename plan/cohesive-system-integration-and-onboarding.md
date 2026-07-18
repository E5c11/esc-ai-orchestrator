# Cohesive ESC AI System — Integration and Onboarding Plan

**Status:** Proposed
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
        +-- esc-execution.yaml                 shared repository identity
        +-- esc-index.json                     first repository routing read
        +-- esc-dependencies.json              component impact graph
        +-- context/project-profile.yaml       architecture defaults during migration
        +-- <component>/esc-component.yaml     shared component identity and selectors
        +-- <component>/esc-index.json          bounded structural routing
        +-- <component>/esc-*-profile.yaml      execution/architecture policies
        +-- INSTRUCTIONS.md                     thin generated inheritance pointer
        +-- .esc-ai/                            repository-local Escape AI directory
              +-- workflows/README.md           repository-specific workflow policy
              +-- workflows/active|archive/     durable project work (committed)
              +-- runs/<run-id>/                transient per-run task context (gitignored)
```

### Repository-local Escape AI directory

`.esc-ai/` is the single, obvious location for everything Escape AI tracks about
ongoing work in a repository — mirroring how `.claude/` holds Claude Code's own
committed, human-authored material without the dot-prefix meaning "hidden" or
"uninteresting."

The identity/discovery files — `esc-execution.yaml`, `esc-index.json`,
`esc-dependencies.json`, `<component>/esc-component.yaml`, and `INSTRUCTIONS.md` — stay
at repository/component root. Their entire purpose is to be the first thing any tool or
human discovers when it opens the repository or a component, the same reason
`package.json` is not nested inside `.npm/`. Hiding them would defeat that purpose.

Everything else Escape AI manages as a record of *work being done*, rather than *what
the repository is*, lives under `.esc-ai/`:

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

`esc-execution.yaml` and `esc-component.yaml` should become the shared discovery
boundary for both frameworks. They should link to architecture selection and execution
policy without embedding entire framework documents.

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
  5. Validate the system
```

The first interface can be a terminal wizard. A web or JetBrains UI should consume the
same orchestrator API and state machine later; business logic must not live in prompts
or terminal rendering.

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
INSTRUCTIONS.md
.esc-ai/workflows/README.md
.esc-ai/workflows/active/README.md
.esc-ai/workflows/archive/README.md
```

Generated `INSTRUCTIONS.md` is a thin pointer; it does not copy either framework.
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

### Phase 1 — Framework composition protocol

- Introduce `.esc-ai/` as the repository-local Escape AI directory; move `workflows/`
  to `.esc-ai/workflows/` in all three existing repositories and every reference to it.
- Resolve per-task `.esc-ai/runs/<run-id>/` output from the task's target repository
  instead of a single orchestrator-process-relative output root.
- Add `ecosystems:` grouping to the machine-local catalog schema and registry.
- Define a versioned framework descriptor for architecture and execution frameworks.
- Extend repository/component manifests with framework versions, architecture
  selectors, extensions, workflow location, and derived-artifact declarations.
- Define instruction resolution and conflict reporting.
- Implement route resolution and compatibility validation for both frameworks.
- Implement architecture profile/index lookup as a library callable by the
  orchestrator.

**Exit:** a task context identifies the exact execution and architecture documents to
load without hard-coded checkout paths, and per-run artifacts land inside the target
repository's `.esc-ai/` directory rather than the orchestrator's own working directory.

### Phase 2 — Unified machine-local catalog

- Define `system.yaml` schema and migration from `repositories.yaml`.
- Support framework, repository, and optional extension routes.
- Add stale/missing route repair actions and credential-provider references.
- Keep catalog operations available as non-interactive commands.

**Exit:** one catalog resolves all three products and every registered repository.

### Phase 3 — Onboarding analysis engine

- Generalize build-system adapter interfaces.
- Implement idempotent repository analysis and proposal contracts.
- Detect existing profiles, instructions, workflow packages, and conflicts.
- Persist onboarding state and input digests in the orchestrator.
- Separate read-only proposal from approved repository writes.

**Exit:** `escape-ai repository analyze /path` produces a complete proposal without
modifying the repository.

### Phase 4 — Human-assisted manifest/profile construction

- Define typed semantic questions and evidence attachments.
- Merge answers into generated manifests without overwriting authored fields.
- Import existing `context/project-profile.yaml` and framework references.
- Generate/complete verification, report, and architecture profile flows.
- Detect unresolved architecture stubs through the Gap Protocol.

**Exit:** an incomplete proposal can resume after user input and reach valid shared
manifests/profiles.

### Phase 5 — Workflow inheritance bootstrap

- Define thin instruction-pointer templates.
- Define repository-specific workflow policy schema/template.
- Analyze and migrate existing workflow READMEs without deleting project history.
- Validate that core framework instructions are referenced rather than duplicated.
- Bootstrap active/archive directories idempotently.

**Exit:** a newly onboarded repository has a minimal, valid workflow package; an
existing repository receives a reviewed migration diff.

### Phase 6 — Interactive CLI onboarding wizard

- Add the `escape-ai` top-level interactive menu and Escape AI product branding.
- Render onboarding state-machine questions and proposals.
- Add non-interactive equivalents: `repository add`, `analyze`, `answer`, `apply`,
  `validate`, and `status`.
- Add cancellation, resumption, and dry-run behavior.
- Test terminal output separately from onboarding business logic.

**Exit:** a user can onboard a representative new repository from path to approved,
commit-ready files through one interface.

### Phase 7 — Feature/fix planning and workflow generation

- Define initiative and linked task-graph contracts.
- Add task-type selection and conversational product questions.
- Route across registered repository indexes and architecture playbooks.
- Generate reviewed single-repository workflow packages.
- Generate linked multi-repository workflows with ordering and handoff contracts.
- Validate all referenced repository/framework IDs before writing.

**Exit:** an approved feature or fix produces complete, independently resumable
workflows in every participating repository.

### Phase 8 — Integrated execution lifecycle

- Connect approved task graphs to scheduler runs.
- Resolve architecture instructions into bounded task context.
- Add approval gates, retry/attempt semantics, and checkpoint promotion.
- Provide active-work/resume views across repositories.
- Retain metrics for onboarding, planning, execution, and rework.

**Exit:** the user can plan, execute, stop, and resume a linked task through the same
CLI/API.

### Phase 9 — Optional richer interfaces

- Stabilize the API/state contracts first.
- Build a web or JetBrains UI without duplicating orchestration logic.
- Display repository readiness, manifest questions, task graphs, approvals, runs,
  checkpoints, and metrics.

**Exit:** alternative interfaces are interchangeable clients of the same orchestrator.

### Phase 10 — Migration and evidence

- Pilot full onboarding against `ampm-backend` without destroying its mature workflow
  history.
- Pilot a smaller new repository to prove clean bootstrap behavior.
- Pilot one linked multi-repository feature.
- Compare context/tool/token/rework metrics against representative prior workflows.
- Refine prompts and defaults only from observed evidence.

**Exit:** the cohesive flow is validated on existing, new, and cross-repository cases.

## Decisions required before implementation

1. Decide whether `system.yaml` replaces or wraps the existing route registry.
2. **Decided:** compatible major version. A repository manifest declares a framework's
   major version; the resolver accepts any checked-out minor/patch sharing that major.
   Matches how the frameworks already evolve — additive, non-breaking minor bumps
   (e.g. `document.yaml` 1.1 → 1.2 added enum values without breaking consumers).
3. Decide whether `context/project-profile.yaml` is migrated immediately or supported
   through a deprecation window.
4. Define which repository-specific instruction fields are structured versus free-form
   Markdown.
5. Define the canonical initiative/task ID strategy across repositories.
6. Decide whether workflow writes are applied directly after approval or first emitted
   as a patch bundle.
7. Select the second, smaller repository for clean onboarding validation.
8. Configure a working runtime provider before measuring real end-to-end efficiency.
9. **Decided:** `workflows/` moves under `.esc-ai/workflows/`, consolidated with the
   new `.esc-ai/runs/` — one obvious repository-local root for everything Escape AI
   tracks about ongoing work, rather than splitting activity/history across a visible
   and a hidden location. Identity/discovery manifests stay at root (see
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

## Recommended next task

Start with **Phase 0 and Phase 1 together as a bounded design/migration slice**:

1. Complete the architecture framework's internal title and reference migration.
   **Done.**
2. Add a migration diagnostic for the renamed framework ID. **Done.**
3. Introduce `.esc-ai/` as the repository-local Escape AI directory (move
   `workflows/` into it, add `runs/`), and resolve per-task run output from the
   target repository instead of the orchestrator process's own working directory.
4. Add `ecosystems:` grouping to the machine-local catalog.
5. Define the framework descriptor and shared manifest extensions.
6. Implement composed framework resolution for one read-only task.
7. Migrate `ampm-backend` references and validate all three repositories.

Do not begin the interactive wizard before this composition protocol is stable. The
wizard would otherwise encode temporary naming, manifest, and instruction assumptions
that would immediately need migration.
