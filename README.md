# ESC AI Orchestrator

Central control plane for scheduling, executing, observing, and resuming AI-assisted
software tasks across registered repositories and agent runtimes.

This repository implements the portable contracts defined by
`esc-ai-execution-framework`. It does not redefine those schemas or repository routing
conventions.

## Bootstrap architecture

```text
HTTP API -> SQLite task/run/event store -> in-process scheduler -> runtime adapter
                                                               -> OpenCode initially
```

The bootstrap intentionally uses the Python standard library. Store, scheduler, and
runtime are separate boundaries so PostgreSQL, a distributed queue, remote workers,
authentication, and richer APIs can be introduced later.

## Run locally

Install both sibling repositories in editable mode, then:

```bash
esc-orchestrator --port 8042 --opencode http://127.0.0.1:4097
```

## `escape-ai` — the user-facing CLI

`escape-ai` is a separate, self-contained entry point from `esc-orchestrator` above —
it does not require the HTTP daemon to be running; it talks to the same `Store` and
`esc_exec` onboarding logic in-process.

```bash
escape-ai                      # interactive menu; all six top-level items are implemented
                                # (Onboard a repository, Plan new work, Resume active work,
                                # Observe a run, Configure system, Validate the system)

escape-ai repository add <id> <path>
escape-ai repository analyze <id-or-path>       # read-only proposal, safe to re-run
escape-ai repository answer <id-or-path> <answers.json>   # stage answers
escape-ai repository apply <id-or-path>         # the explicit write step
escape-ai repository validate <id-or-path>
escape-ai repository status <id-or-path>

escape-ai plan draft <initiative-id> <request.json>       # {work_type, objective, repositories}
escape-ai plan answer <initiative-id> <answers.json>      # stage answers
escape-ai plan apply <initiative-id>                      # the explicit write step
escape-ai plan status <initiative-id>
escape-ai plan ready <initiative-id>                      # tasks in this initiative that are
                                                           # unblocked and never submitted

escape-ai provider auth <name> [--route subscription|api-key]   # connect an AI provider once;
                                                                 # required before `task run`
                                                                 # can submit anything for real

escape-ai policy show                                     # the default policy profile a task
                                                            # starts from (falls back to
                                                            # standard-autonomous if unset)
escape-ai policy set <profile-id>                          # standard-autonomous | readonly-review

escape-ai resume [--json]                                 # active work across registered
                                                           # repositories: latest run status,
                                                           # attempt count, checkpoint presence
escape-ai task run <repository-id> <task-id> [--yes]      # preview by default; --yes submits
                                                           # the task through the real scheduler
                                                           # and runtime adapter
escape-ai task promote-checkpoint <repository-id> <task-id> [--yes]   # preview the failure
                                                                       # candidate, or promote it
                                                                       # into a durable
                                                                       # .esc-ai/workflows/active/
                                                                       # checkpoint with --yes
escape-ai task impact <task-id>                           # show which other initiative tasks
                                                           # this completed task unblocks
```

`analyze`/`answer`/`apply` (and `plan draft`/`answer`/`apply`) are deliberately
separate steps: analysis/drafting never writes anything, staging an answer never
writes anything, and `apply` is the one explicit approval boundary where files
actually get written — manifests, indexes, verification/architecture profiles, and
`.esc-ai/INSTRUCTIONS.md`/`.esc-ai/workflows/` for onboarding (everything escape-ai
generates and manages lives under `.esc-ai/`, component manifests flat and keyed by
stable component ID); `.esc-ai/workflows/active/<task-id>/task.yaml`
and `README.md` for planning. A plan spanning more than one repository generates one
cross-linked task per repository, with a `depends_on` question per repository asking
which others in the initiative must complete first — defaulting to the repository
immediately before it in declared order if left unanswered (so an initiative with no
real branching still gets the old straight-chain behavior for free), but a genuine
graph (branching, diamonds, independent subgraphs) is fully supported, not just a
chain — validating every repository/component reference before writing anything to
any of them. Nothing is ever committed automatically — both the interactive and
non-interactive paths print exactly which files to review and commit yourself.

Re-running onboarding for a repository with unchanged inputs resumes from the stored
proposal instead of re-analyzing from scratch.

`task run` and `promote-checkpoint` follow the same preview-then-`--yes` pattern:
without `--yes` they print what would happen (objective, components, and — for
`run` — the workspace/adapter/policy that would be used) and exit without submitting
or writing anything; `--yes` is the one explicit approval boundary that actually
submits the task to the `Scheduler`/runtime or writes the promoted checkpoint file.
A failed attempt is retained as a transient checkpoint candidate and does not block
retrying the same task; each retry increments an attempt count tracked per task.
Workspace defaults to `kind: worktree` — the agent edits a disposable git worktree,
not the live checkout, so an unanticipated change is contained and reviewable via
`task promote-checkpoint` rather than needing to be prevented mid-run; this is a
permanent default, not a placeholder. Adapter resolves to whichever provider is
actually connected via `provider auth`. Policy resolves to whichever named profile
is configured as the default (`policy show`/`policy set`, or "Configure system" →
"Show / select default policy") — falling back to `standard-autonomous` (full
read/edit/execute/network autonomy, contained by the hard-deny list and disposable
worktree isolation, not per-path scoping) if nothing has been configured yet, so an
installation that never touches this behaves exactly as it always has.
`external_paths` scoping and budget/cost limits are still unbuilt for every
profile — see `plan/done/pre-flight-consent-and-bounded-autonomy.md`.

Endpoints:

- `GET /health`
- `POST /tasks` — submit a JSON object containing `task`, `workspace`, `adapter`, and
  `policy` portable contracts
- `GET /tasks/{id}`
- `GET /runs/{id}`
- `GET /runs/{id}/events`
- `GET /runs/{id}/summary` — return the bounded `verification-summary.json` when the
  runtime produced one; complete reports remain in the run output directory
- `GET /runs/{id}/context` — return the generated bounded task-routing context
- `GET /runs/{id}/verification-plan` — return the ordered progressive verification gates
- `GET /runs/{id}/checkpoint` — return a checkpoint candidate retained after a failed run
- `GET /runs/{id}/metrics` — return provider-reported tokens and measured execution,
  context, tool-call, and rework dimensions when the runtime emitted them
- `POST /repositories/{id}/analyze` — run the read-only onboarding analysis engine
  against a registered repository and persist the resulting proposal; 404 if the
  repository ID isn't registered
- `GET /repositories/{id}/proposal` — return the latest persisted onboarding proposal
  for a repository; 404 if none has been generated yet
- `POST /repositories/{id}/answers` — apply human answers to the latest analyzed
  proposal, writing manifests for the first time (analysis itself stays read-only);
  404 if no proposal has been generated yet for that repository
- `GET /repositories/{id}/answers` — return the result of the latest applied answers
  for a repository; 404 if none have been applied yet

Failure candidates remain transient until reviewed. Promote durable handoff state into
the consuming repository with `esc-exec checkpoint create` or `checkpoint update`, then
commit `.esc-ai/workflows/active/<task-id>/checkpoint.yaml`.

The orchestrator's own SQLite database (`.orchestrator/orchestrator.db`) is
process-local state and is not committed. Per-run artifacts (task context,
verification plan, events, run metrics, and failure-checkpoint candidates) are no
longer stored there — they live in the target repository's `.esc-ai/runs/<run-id>/`,
resolved from the task's declared repository.

## Cohesive system roadmap

The next development phase composes the architecture framework, execution framework,
and orchestrator into unified repository onboarding and cross-repository planning. See
[`plan/active/cohesive-system-integration-and-onboarding.md`](plan/active/cohesive-system-integration-and-onboarding.md).

## Test

```bash
python -m unittest discover -v
```
