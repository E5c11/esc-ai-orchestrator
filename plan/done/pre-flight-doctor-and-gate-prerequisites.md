# Pre-Flight Doctor and Declared Gate Prerequisites — Plan

**Status:** Implemented
**Date:** 2026-08-03
**Implemented:** 2026-08-03 — all five designs shipped and tested in both repos:
`esc_exec.environment.check_prerequisites` (esc-ai-execution-framework), the
`PreDispatchBlockerError`/`EnvironmentPrerequisiteError` pre-dispatch gate and
`escape-ai task doctor` CLI command (esc-ai-orchestrator), `worktree_inherit` +
`copy_inherited_files` (esc-ai-execution-framework), `classify_failure` +
`failure_category` (esc-ai-execution-framework, folded into
`Scheduler._verification_failures`), and `scope.verification_scope: task` gate
narrowing (esc-ai-execution-framework). Landing here doesn't mean every open
question below was resolved — see each one's own note.
**Objective:** Let escape-ai catch environment/infrastructure gaps that would make a
task's verification gates fail — missing auth tokens, unreachable services, port
mismatches, missing credential files — *before* spending a real, subscription-metered
agent dispatch on them, by (a) letting a verification gate check declare the external
prerequisites it needs, (b) running those declarations through a cheap local
pre-flight pass ahead of dispatch, (c) classifying a gate failure that does happen by
which of these categories it falls into, and (d) letting a task narrow the `final`
gate to its own declared scope instead of always running the whole repository's test
suite. Distilled from `plan/active/escape-ai-improvements.md` findings #3, #4, #5, #6,
and the scoped-verification half of #8.

## Why this is a separate plan

Distinct from three existing plans that sit close to this one:

- **`task-orchestration-and-verification-loop.md`** (Implemented) is about
  independently verifying *what an agent actually did* after a run, and ordering
  tasks in a dependency graph. This plan is about validating *whether the
  environment can even execute the declared gates at all*, strictly before an agent
  is dispatched — a different question asked at a different time.
- **`pre-flight-consent-and-bounded-autonomy.md`** (Implemented) gates what a task is
  *allowed* to do (permission scope, hard-deny list, worktree containment). This
  plan gates whether the *infrastructure* the task's own declared verification needs
  is actually reachable. Orthogonal: a fully-permitted task can still fail for
  reasons that have nothing to do with what it's allowed to touch.
- **`provider-agnostic-gate-execution.md`** (Implemented) is what actually runs a
  gate's command and captures its exit code. This plan adds a cheaper check that
  runs *instead of* the real command, and a classifier for when the real command's
  failure needs explaining.

All four dogfooding findings this distills (#3, #4, #5, #6) trace back to one root
cause: a verification gate's `check` dict (`esc_exec/task_context.py`'s
`build_verification_plan`) declares a `command` and, for the `focused` gate, a
`requires: ["test_filter"]` runtime input — but nothing declares what *external*
things (a service, a credential, a token) that command needs to even attempt running.
Fixing that one gap is what makes a pre-flight check, a worktree-inheritance
declaration, and a failure classifier all buildable on the same data, which is why
they're one plan instead of four unrelated small ones.

## What we found (grounding, same session)

- `_AdapterRuntime.execute` (`esc_orchestrator/runtime.py`) already has the exact
  shape this needs: it calls `build_task_context`, checks
  `_architecture_coverage_blockers(context)`, and raises `ArchitectureCoverageError`
  — a typed exception carrying a `blockers: list[str]` — *before* `self.adapter.execute(...)`
  is ever called, i.e. strictly before a real agent dispatch happens. That exception
  is caught by `Scheduler`'s existing exception-driven checkpoint path, the same path
  an uncaught exception during a run already uses. A pre-flight environment check
  belongs in exactly this spot, as a sibling check alongside the coverage gate, not a
  new dispatch-time mechanism.
- `checkpoints.py`'s `checkpoint_document`/`update_checkpoint` carry `blockers` as a
  flat `list[str]` — no structure, no category. This confirms finding #6 directly:
  there's nowhere today for "dependency-resolution auth failure" vs.
  "connection-refused" vs. "assertion failure" to be distinguished in the data model,
  only in prose a human writes by hand.
- `task_context.py`'s `GATES = ("focused", "component", "impact", "final")` — the
  `final` gate is unconditionally `["./gradlew", "test"]` at the repo root (see
  `manifests.py`'s generated verification profile). There is no field anywhere that
  lets a task narrow this to its own declared `scope.components`. Finding #8's
  eager-`@Bean` crash is a real product bug in `ampm-backend` (flagged separately,
  below), but the reason it blocked an `auth`/`core-api`/`core-web`-scoped task at
  all is that `final` has no scoping knob to opt out of pulling in unrelated modules
  like `:migration`.
- `esc_exec/worktree.py`'s `ensure_worktree` creates the disposable worktree but
  copies nothing into it beyond what git itself tracks — confirmed no file-copy step
  exists today.
- `manifests.py`'s repository manifest (`schema_version`, `repository`, `components`)
  is the existing place a repository-wide declaration lives (mirrors how
  `component.manifest`'s `paths.verification_profile` already works) — the natural
  home for a new "files to inherit into every fresh worktree" list, not a new file.

## Design

### 1. Gate checks declare external prerequisites, not just a command

Extend a verification-profile check's schema with a new key — `prerequisites`, kept
separate from the existing `requires` (which means "runtime input parameter needed,"
e.g. `test_filter`, and drives the existing `input-required` gate status; conflating
the two would break that meaning). Each prerequisite is a small typed descriptor, not
free text, so a pre-flight pass has something concrete to act on:

```yaml
prerequisites:
  - kind: env
    name: GITHUB_TOKEN
  - kind: tcp
    host: localhost
    port: 5432
  - kind: file
    path: /run/secrets/firebase-adminsdk.json
```

Adapter-/build-system-agnostic by construction: `kind: env`/`tcp`/`file` are generic
primitives any adapter's generated profile can populate (Gradle's generated profile
declaring `tcp: localhost:5432` for a Postgres-backed test module, an npm profile
declaring `env: NPM_TOKEN` for a private registry) rather than a Gradle-specific
mechanism — the same "generic extension point, one concrete consumer first" shape
`feedback_build_to_scale` calls for elsewhere in this codebase. Declaring these stays
manual/opt-in the same way `verification profile generate` is already a distinct step
from onboarding today — auto-*inferring* `tcp: localhost:5432` from a Spring
`application.yaml` is a real Tier-2-AI-suggestion candidate later, explicitly out of
scope for this plan's first cut (see Non-goals).

### 2. `escape-ai task doctor <repository> <task-id>` — a cheap, no-dispatch check

A new CLI command (and/or an automatic step folded into `task run` before the real
`--yes` dispatch, mirroring how `_architecture_coverage_blockers` already runs
unconditionally inside `_AdapterRuntime.execute`) that:

1. Builds the task's verification plan the same way `task run` already does
   (`build_verification_plan`) — no new plan-construction logic.
2. For every check across every gate, resolves its `prerequisites` list:
   - `env`: is the named variable set in the current shell environment?
   - `tcp`: can a socket connect to `host:port` within a short timeout?
   - `file`: does the path exist and is it readable?
3. Reports gaps in the same shape a blocker already takes today (`"gate final,
   check repository-tests: prerequisite tcp localhost:5432 unreachable"`), not a new
   report format.

This directly answers the four failed attempts in finding #3: attempt 1 (`env:
GITHUB_TOKEN` unset), attempt 2 (`tcp: localhost:5432` unreachable, no Postgres
running at all), attempt 3 (`tcp: localhost:5432` unreachable even with Postgres
running, because the declared port didn't match `application.yaml`'s real
`jdbc:postgresql://localhost:15433/ampm` — which is itself only catchable if the
prerequisite declaration is written to match the app's actual config, a real
authoring responsibility the doctor can't invent on its own, but *can* immediately
re-flag if a human fixes the declaration and the same wrong port is still there).

Wired into `_AdapterRuntime.execute` as a second pre-dispatch blocker source
alongside `_architecture_coverage_blockers`, raising a new typed exception (e.g.
`EnvironmentPrerequisiteError`, same `blockers: list[str]` shape as
`ArchitectureCoverageError`) so it flows through the exact same
`Scheduler`-exception-driven checkpoint path — no new checkpoint mechanism, per
finding #6/#5's own root-cause framing above.

### 3. Repository manifest declares gitignored files to inherit into worktrees

New optional field on the repository manifest (`manifests.py`'s generated
`repository.yaml`, alongside `schema_version`/`repository`/`components`):

```yaml
worktree_inherit:
  - local.properties
  - .env
```

`ensure_worktree` (`esc_exec/worktree.py`) copies each listed path from the main
checkout into the fresh worktree immediately after creating it, only if the source
file exists (a repository without `local.properties` configured yet shouldn't error).
Opt-in and explicit per finding #4's own caution — this is deliberately copying
secrets between working trees, so the field is never auto-populated by onboarding's
static detection, only hand-declared by whoever configures the repository, and the
onboarding/`repository validate` output should list what's currently declared so it's
visible, not silent.

### 4. Failure classification on a gate check's actual (post-dispatch) failure

For the case where prerequisites resolve fine but the real command still fails
(finding #6) — a lightweight classifier over a failed check's captured
`stdout`/`stderr` log (already written by `provider-agnostic-gate-execution.md`),
run by `execute_verification_plan` right after a check fails, before writing the
result:

- dependency-resolution/auth (`401`, `403`, `Could not resolve`, `PKIX`)
- connectivity (`Connection refused`, `ConnectException`, `timed out`)
- assertion/test failure (already partially covered by JUnit-report enrichment —
  this only adds a category label, not a new report)
- compile error (`error: `, `cannot find symbol`, language-specific patterns per
  build-system adapter)
- other/unclassified — always a valid, expected outcome for patterns that don't
  match anything above; the classifier degrades to "unclassified" rather than
  guessing, and stays additive to the raw log, never a replacement for it.

Attached as a `failure_category` field on the check's result in
`verification-result.json`, surfaced into `checkpoint.yaml`'s blocker string
(`"final gate check repository-tests failed: connectivity — see stderr log"`) instead
of the bare `status: failed` finding #6 describes.

### 5. `final` gate can be scoped to the task's declared components

New optional field on `task.yaml`'s scope (or a flag on `task run`) —
`verification_scope: task | repository`, defaulting to `repository` (today's
unconditional full-suite behavior, unchanged for every existing task). When set to
`task`, `build_verification_plan`'s `final` gate check is built from the task's own
`scope.components` (and their transitive consumers, which `impact` already computes)
instead of the generated profile's repo-root `./gradlew test` — reusing the same
`component`-gate check-collection logic that already exists for `impact`, not new
aggregation logic. This is the direct fix for finding #8: an `auth`/`core-api`/
`core-web`-scoped task never pulls in `:migration`'s Firebase-credential bean at all
if `final` only aggregates the declared and impacted components' own test tasks.

The underlying `ampm-backend` bug (an eager, non-conditional credential-backed
`@Bean` reachable from any full-context test, already flagged in that repo's own
`wiki/architecture/deployment.md` as a prior prod incident) is a real resiliency
issue in that repository, not escape-ai's to fix — worth a note back to that repo
independent of this plan, not part of it.

## Non-goals

- Auto-inferring `prerequisites` from a component's config files (parsing
  `application.yaml` for a JDBC URL, detecting `GITHUB_TOKEN` usage in a Gradle
  script). First cut is hand-declared, mirroring how `verification profile generate`
  is already a distinct, human-triggered step. A Tier-2-AI-suggestion pass for this
  is a plausible future extension, not part of this plan.
- A generic secret-management system for `worktree_inherit`-copied files (encryption,
  audit log of what got copied where). It's a plain file copy, opt-in and documented
  as copying secrets around, per finding #4's own suggested fix — nothing more.
- Making `verification_scope: task` the default. Existing tasks keep today's
  full-repository `final` gate unless a task explicitly opts into scoping down;
  changing the default is a separate, larger behavior change this plan doesn't make.
- A general log-parsing/observability pipeline. The failure classifier (design 4) is
  a small, fixed pattern list against one already-captured log per failed check, not
  a new logging or metrics system.

## Open questions

1. **Still open, shipped with the narrower first cut:** `kind` vocabulary is
   `env`/`tcp`/`file` only (`esc_exec/environment.py`) — no `kind: docker` primitive
   was added. A repository whose gate needs a reachable Docker daemon has to
   declare it as `tcp` against the Docker socket/a port a container publishes, not
   a first-class check. Grow this only when a real repository hits the gap, per
   this plan's own non-goals.
2. **Resolved 2026-08-03, shipped both, not an either/or:** the check runs
   automatically (`runtime.py`'s `_AdapterRuntime.execute` raises
   `EnvironmentPrerequisiteError` before `self.adapter.execute(...)`, exactly
   alongside the architecture-coverage gate) *and* stands alone as `escape-ai task
   doctor <repository> <task-id>`, which never dispatches and shares its
   implementation (`runtime.doctor_check`) with the automatic path so the two can
   never drift apart. The "escape hatch" concern turned out to be a non-issue as
   predicted: a check/gate with no declared `prerequisites` is trivially clean.
3. **Still open, unchanged:** `failure_category` (`esc_exec/verification_execution.py`'s
   `classify_failure`) only feeds human/next-agent-attempt visibility (folded into
   `Scheduler._verification_failures`'s blocker strings) — no automatic retry
   behavior consumes it. Not designed further here.
