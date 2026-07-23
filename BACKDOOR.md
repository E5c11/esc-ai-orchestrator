# escape-ai Headless Backdoor Mode

You (any AI coding assistant, any provider) were pointed at this file directly, by
absolute path, in a repository that may have nothing to do with `esc-ai-orchestrator`
itself. Treat this file as fully self-contained — do not assume any other context from
this conversation carries over. It tells you how to drive `escape-ai`'s indexing,
architecture-framework resolution, and multi-repo task orchestration for the human
operator, without their normal interactive onboarding/planning conversation.

Design background, if you want it: `plan/done/task-orchestration-and-verification-loop.md`
and `plan/active/headless-backdoor-mode.md` in `esc-ai-orchestrator`. You don't need to
read either to follow this doc.

## Who this is for

The human operator is a **solo user** of `escape-ai` across several of their own
projects, and is also the **maintainer** of `esc-ai-architecture-framework` itself —
not an external contributor to it. Both facts matter below: they can add/promote
framework docs directly with no review ceremony, and they don't need to be walked
through onboarding questions one at a time.

## Fixed facts, not guesses

- CLI binary: `escape-ai` (installed from `esc-ai-orchestrator`'s `pyproject.toml`).
- Global flags, given *before* the subcommand: `--db <path>` (defaults to
  `.orchestrator/orchestrator.db` under the current directory) and `--registry <path>`
  (defaults to a machine-local path — `~/.local/share/esc-ai/system.yaml` on Linux,
  overridable via the `ESC_AI_REGISTRY` env var). Once a registry exists, reuse the
  same one across sessions — it's what remembers which repositories/frameworks/
  providers are registered.
- Every command below is already non-interactive: it takes a file path, not a live
  prompt. There is a *separate* interactive conversational mode this doc does not use.

## Step 0 — has this repository already been onboarded?

```
escape-ai repository status <repository-id>
```

If that fails with an unknown-repository error, register it first:

```
escape-ai repository add <repository-id> <absolute-path-to-repo-checkout>
```

## Step 1 — onboard each repository this work touches (per repository)

```
escape-ai repository analyze <repository-id> --json
```

This is Tier 1 static detection — it inspects build files and proposes
frameworks/targets/architecture profile suggestions per component without asking
anything. Read the JSON it prints; it tells you exactly what it could and couldn't
infer per component.

Write an answers file covering what Tier 1 couldn't infer (at minimum, each
component's `purpose` — a one-line description of what the component owns):

```json
{
  "component-id": {
    "purpose": "Owns X.",
    "frameworks": {"network": "ktor"},
    "targets": ["ios"],
    "architecture_style": "web-app"
  }
}
```

`frameworks`/`targets`/`architecture_style` are optional — omit anything Tier 1
already suggested correctly; only override what it missed or got wrong. Then:

```
escape-ai repository answer <repository-id> <answers.json>
escape-ai repository apply <repository-id>
```

`apply` writes `.esc-ai/` manifests, indexes, and dependency graphs into the real
repository checkout. Repeat steps 0-1 for every repository this initiative touches.

## Step 2 — draft, answer, and apply the plan

For an initiative spanning one or more of the repositories just onboarded, write a
request file:

```json
{
  "work_type": "feature",
  "objective": "One-sentence description of what this initiative delivers.",
  "repositories": ["repo-a", "repo-b"]
}
```

`work_type` is one of `feature`, `fix`, `refactor`, `maintenance`, `investigation`.

```
escape-ai plan draft <initiative-id> <request.json>
```

Write a plan-answers file — one entry needed regardless of how many repositories:

```json
{
  "components": {"repo-a": ["content"], "repo-b": ["api"]},
  "scope_boundary": "What's explicitly out of scope, or empty string.",
  "completion_conditions": ["Condition 1", "Condition 2"],
  "rollout_needs": ""
}
```

```
escape-ai plan answer <initiative-id> <plan-answers.json>
escape-ai plan apply <initiative-id>
```

For a **single-repository** plan, `apply` writes one `task.yaml`, and its task ID is
exactly `<initiative-id>` (no repository suffix). For a **multi-repository** plan, it
writes one `task.yaml` per repository, each in that repository's own
`.esc-ai/workflows/active/<task-id>/`, with task IDs `<initiative-id>-<repository-id>`
— and, today, chains them in the declared `repositories` list order (the first
repository's task has no dependency; each following one `depends_on` the one before
it). This naming difference matters for Step 4 below — check which shape you're in
before constructing the task ID.

## Step 3 — connect a provider once, if not already connected

```
escape-ai provider auth <name> [--route subscription|api-key]
```

`name` is one of the known providers (`claude`, `openai`, or a configured API-key
route). Skip this if `escape-ai resume` (below) already shows work with no provider
error.

## Step 4 — run whichever ready tasks are actually relevant, in parallel

Single-repository plan — there's only ever one task, nothing to parallelize:

```
escape-ai task run <repository-id> <initiative-id> --yes
```

Multi-repository plan — find every task that's unblocked *right now* and has never
been submitted:

```
escape-ai plan ready <initiative-id>
```

This prints a JSON list of `"repository/task-id"` entries — every task whose
`depends_on` is fully satisfied and that has no execution history yet. For an
initiative with several independent tasks (not chained to each other at all), this
can be more than one entry even before anything has run.

**Known limitation, as of this writing:** Step 2's `plan apply` always synthesizes a
straight chain across the declared `repositories` list order (each depends only on
the one before it), never genuine branching, regardless of whether the actual work
requires that ordering — so `plan ready` will usually return exactly one entry right
after `plan apply`, even for tasks that don't really need to run in sequence. If you
know two or more of the tasks Step 2 just wrote are genuinely independent, you can
hand-edit their `task.yaml`'s `task.initiative.depends_on` list directly (remove an
entry that isn't a real requirement) before running `plan ready` — `depends_on`
entries are just `"repository/task-id"` strings, safe to edit by hand. Once
multiple tasks are genuinely independent (whether by that edit or because a future
`plan apply` stops always-chaining), everything below already handles them correctly
in parallel — this limitation is about what Step 2 generates today, not what Step 4
can execute.

**Treat this list as a menu, not a mandate.** `plan ready` is scoped to one
initiative already, so it never shows you tasks from unrelated work — but an
initiative can still legitimately bundle several tasks that have nothing to do with
each other beyond sharing an `initiative_id` (e.g. a grab-bag of small independent
fixes planned together). Decide which entries are actually relevant to what you're
being asked to do right now — that might be all of them, or just one, or some
subset. Don't dispatch a ready task just because it's on the list.

**For whichever subset you decide to run, dispatch all of *those* at once, using
your own native parallel/subagent mechanism** (e.g. multiple tool calls in a single
message, or your equivalent) if there's more than one — one subagent per task, each
running:

```
escape-ai task run <repository-id> <task-id> --yes
```

for its own `"repository/task-id"` entry (split on the first `/`). Don't run several
relevant, independent tasks one at a time in a loop in the main session if you have
a way to run them concurrently — that's the whole point of this step, once you've
picked what's actually in scope. It's safe: `escape-ai` itself guards against two
processes racing to submit the same task twice, even across real separate OS
processes, not just threads. A task you deliberately leave undispatched just stays
that way — nothing auto-advances it on your behalf; it'll still be there next time
you run `plan ready` for this initiative.

You do not need to poll and re-dispatch after this wave finishes. Each dispatched
task's own process keeps running until nothing more auto-advances *from what you
dispatched* — and because completion state is shared through the same on-disk store
every process reads, a task blocked on outputs from *two* different parallel
branches you dispatched still gets picked up correctly, by whichever branch's
process happens to finish last. Confirm the wave you dispatched actually landed with:

```
escape-ai resume --json
```

which lists every active task across every registered repository with its latest run
status and whether a checkpoint is pending review — this will legitimately still show
`null`/no-run-yet for anything you deliberately left undispatched, that's expected,
not a problem to chase. If a task you *did* dispatch still shows no run status at all
once its subagent has returned, run `plan ready` again — that should be rare (it
means something is still catching up), not the normal case.

## When a run stops — two different reasons, two different responses

A `"failed"` run with a checkpoint pending (`resume --json` shows
`"checkpoint_present": true`) means one of two things. Read the checkpoint to find out
which:

```
escape-ai task promote-checkpoint <repository-id> <task-id>
```

(without `--yes`, this only *previews* the checkpoint — safe to run just to inspect it)

**If the blockers look like normal task failures** (a real test failure, a build
error, an unhandled exception) — this is an ordinary blocked task. Fix the underlying
issue in the repository, then re-run `task run ... --yes` for that same task, or once
satisfied, promote the checkpoint with `--yes` and move on.

**If a blocker reads like `"<component>: architecture doc <ID> does not exist"` or
`"<component>: architecture doc <ID> is still a stub, not yet active"`** — this is
`escape-ai`'s architecture-coverage hard stop. It never dispatched the agent at all;
nothing should proceed on incomplete framework guidance. Resolve it as the framework's
maintainer, not as an external contributor going through review:

1. In `esc-ai-architecture-framework`, add or edit the document at the path implied by
   the doc ID's layer (check `README.md`/existing documents under the matching
   `architectures/`, `platforms/`, `patterns/`, etc. directory for the naming
   convention) with the frontmatter shape `schemas/document.yaml` defines — critically,
   `status: active` directly, no `stub` intermediate. The maintainer editing their own
   repository *is* the review step.
2. Regenerate the index: `python3 tools/index.py` from
   `esc-ai-architecture-framework`'s repo root (writes `index.json`/`index.md`).
3. Re-run the same command from Step 4 for the task that stopped
   (`escape-ai task run <repository-id> <task-id> --yes`). It re-resolves coverage
   fresh; if it now resolves cleanly, the task (and anything chained behind it) proceeds.

Do **not** route this through `esc-ai-architecture-framework`'s external-contributor
CI/PR/dry-run process, if one exists there by the time you read this — that governance
is for people who aren't the maintainer.

## Quick reference

| Goal | Command |
|---|---|
| Check if a repo is onboarded | `escape-ai repository status <id>` |
| Register a repo | `escape-ai repository add <id> <path>` |
| Tier 1 detection | `escape-ai repository analyze <id> --json` |
| Submit onboarding answers | `escape-ai repository answer <id> <file>` |
| Apply onboarding | `escape-ai repository apply <id>` |
| Draft a plan | `escape-ai plan draft <initiative-id> <request.json>` |
| Submit plan answers | `escape-ai plan answer <initiative-id> <file>` |
| Apply a plan | `escape-ai plan apply <initiative-id>` |
| Find every currently-ready task | `escape-ai plan ready <initiative-id>` |
| Connect a provider | `escape-ai provider auth <name>` |
| Run a task | `escape-ai task run <repo-id> <task-id> --yes` |
| See what's active/blocked | `escape-ai resume --json` |
| Inspect/promote a checkpoint | `escape-ai task promote-checkpoint <repo-id> <task-id> [--yes]` |
| See what a completed task unblocks | `escape-ai task impact <task-id>` |
