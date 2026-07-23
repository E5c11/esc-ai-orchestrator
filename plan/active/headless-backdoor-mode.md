# Headless Backdoor Mode — Plan

**Status:** Active
**Date:** 2026-07-23
**Objective:** Let the user drive escape-ai's multi-repo indexing, architecture-framework
resolution, and task-orchestration/verification loop directly — as a trusted sole
operator, and as the architecture framework's own maintainer — without the CLI's
interactive onboarding/planning conversation. In exchange for skipping that ceremony,
add a safeguard the interactive mode never needed as urgently: a hard stop when a
task's required architecture-framework coverage is incomplete, since nothing should
proceed on under-specified guidance when there's no human in a live conversation to
notice the gap. Also gives a stable, tool-agnostic entry point — one file, pointed
to by path — so any AI coding session, with whichever provider is active that day,
in any repo, can be told to follow this mode directly.

## Why this is a separate plan

Distinct from both existing plans it sits next to:

- [`cohesive-system-integration-and-onboarding.md`](cohesive-system-integration-and-onboarding.md)
  is about the *interactive* CLI UX — guided questions, menus, confirmations. This plan
  is about removing that surface for a specific, narrower audience (the user, solo,
  across their own projects) while keeping everything underneath it.
- [`framework-contribution-and-extensibility.md`](../future/framework-contribution-and-extensibility.md)
  designs a governed contribution flow — dry-run CI reports, PR review, `stub` before
  `active` — for *external* contributors to `esc-ai-architecture-framework`. The user
  is that framework's maintainer, not an external contributor to it; their own edits
  are already the reviewed step, by definition. This plan's "add the missing doc and
  resume" loop is deliberately lighter than that one, not a duplicate of it.

## What was checked, not assumed

- **Most of the non-interactive path already exists.** `repository add/analyze/answer/
  apply` and `plan draft/answer/apply` already take answers from a JSON/YAML file
  path, not a live conversation — confirmed directly from this session's own test
  code (e.g. `run(["repository", "answer", "repo", str(answers_file)])`,
  `run(["plan", "answer", "feature-export", str(answers_file)])` throughout
  `tests/test_escape_ai_cli.py`). The *interactive* conversational surface
  (`run_resume_interactive`, `prompt_provider_setup_interactive`, etc.) is a
  genuinely separate code path from these — skipping it doesn't require new plumbing,
  just not calling it.
- **The headless multi-repo engine this needs is already built.**
  `task-orchestration-and-verification-loop.md` (done 2026-07-23, now in `plan/done/`)
  shipped a real cross-repository `depends_on` graph (task 1), task-impact analysis
  (task 2), independently-verified run status replacing agent self-report (task 5),
  and event-driven automatic advancement (task 7) — the exact "headless, unattended,
  durable execution" mode that plan's own "Two execution modes" section named as the
  target for this kind of use. Once the first task in an initiative is submitted,
  dependents run themselves; nothing here needs to reinvent that loop.
- **Architecture coverage is already computed, just never enforced.**
  `build_task_context` (`esc-ai-execution-framework/esc_exec/task_context.py:76-88`)
  already calls `resolve_architecture_docs`, and for each in-scope component sets
  `entry["architecture"]["missing"]` (doc IDs that don't resolve at all) and
  `entry["architecture"]["stubs"]` (doc IDs that resolve but are `status: stub`, not
  yet promoted) whenever either is non-empty. Today this is purely informational —
  it flows into the task-context document and, from there, into what the agent is
  instructed with — but nothing anywhere reads it back to decide whether to proceed.
- **`build_task_context` is not currently called by `_AdapterRuntime.execute`.** Each
  adapter (`ClaudeCodeAdapter.execute`, etc.) calls it internally to build its own
  instruction bundle. `_AdapterRuntime.execute` (`esc_orchestrator/runtime.py`)
  currently only calls `build_verification_plan` directly before handing off to the
  adapter — proving direct calls to `task_context.py` functions from this layer are
  already an established pattern, not a new kind of coupling this plan would be
  introducing.
- **The checkpoint mechanism this should reuse is already generic enough.** Task 6
  factored `Scheduler`'s checkpoint-building into a shared `_write_checkpoint_candidate`
  helper used by both an uncaught exception and a not-clean verification result. A
  third trigger (incomplete architecture coverage) is exactly the same shape of
  addition task 6 already was — "a new trigger for an existing path, not a new
  mechanism."
- **The user shuffles between AI coding providers** (Claude Code, Codex, others),
  so any entry-point design tied to one tool's own config/discovery mechanism (a
  `~/.claude/CLAUDE.md` entry, a Claude Code skill) is the wrong shape regardless of
  whether it's technically available — it would need to be re-solved per tool, and
  re-solved again for whatever tool comes next. A plain file, referenced by path in
  conversation, works identically with every tool that can read a file and follow
  instructions — no provider-specific integration to build or maintain at all.

## Design

### 1. Architecture-coverage hard stop

Extend `_AdapterRuntime.execute` (`esc_orchestrator/runtime.py`) to call
`build_task_context` itself, before calling `self.adapter.execute(...)` — purely to
check coverage, independent of whatever the adapter separately builds for its own
instruction bundle. If any in-scope component's `architecture.missing` or
`architecture.stubs` is non-empty, don't dispatch the agent at all: write a
checkpoint candidate (reusing `_write_checkpoint_candidate`, the same helper task 6
already built) with one blocker per missing/stub doc ID, and mark the run `"failed"`
— same shape as a not-clean verification result, just triggered earlier, before any
agent work happens instead of after.

This means `build_task_context` runs twice per task (once here for the gate, once
again inside the adapter for the real instruction bundle) — accepted as a minor,
harmless redundancy: the function is a pure, cheap, local-file-only read with no
subprocess/network cost, and avoiding the duplication would mean either adapters
stop building their own context (a bigger, riskier refactor) or the gate reads the
adapter's internal state after the fact (defeats the point of gating *before*
dispatch).

Both `missing` and `stubs` gate — `missing` (doesn't exist at all) is strictly worse
than `stub` (exists, not yet reviewed), and the framework's own Gap Protocol already
treats `stub` as "not yet selectable" for exactly this reason.

Because this hooks into `_AdapterRuntime.execute` (the same layer task 5 wired
`execute_verification_plan` into), every task in an auto-advancing chain gets the
same gate for free — not just the first one a human or script explicitly submitted.

### 2. Non-interactive multi-repo entry sequence

Not new engine work — a documented, scripted sequence of commands that already
compose non-interactively today:

```text
repository add <id> <path>          (if not already registered)
repository analyze <id>
repository answer <id> <answers.json>
repository apply <id>
  ... repeat per repository in the initiative ...
plan draft <initiative-id> <request.json>
plan answer <initiative-id> <plan-answers.json>
plan apply <initiative-id>
task run <first-repo> <first-task-id> --yes
  ... task 7's auto-advancement takes it from here ...
```

The only genuinely open design question here is where `answers.json`'s content comes
from without a human answering questions live — Tier 1 static detection already
covers a real amount of this (frameworks/targets/architecture-profile suggestions
from build files), and what it can't infer (purpose, scope boundary, completion
conditions) the user supplies once, up front, as a file, rather than turn by turn.
This plan doesn't need to solve that generation problem — it's the same shape of
input either way, just delivered as a file instead of a conversation.

### 3. Owner fast-track for the architecture framework itself

No new tooling. When a run stops on section 1's gate, the resolution is: the user
(or Claude, directed by the user) edits/adds the doc directly in
`esc-ai-architecture-framework`, sets `status: active` immediately (no `stub`
intermediate — the maintainer editing their own repository *is* the review step),
regenerates the index (`tools/index.py`), and re-runs the previously-stopped task.
Explicitly not routed through `framework-contribution-and-extensibility.md`'s future
dry-run/PR/CI ceremony — that plan's governance exists for people who aren't the
maintainer; forcing the maintainer through review of their own single-operator edits
would be exactly the kind of self-imposed ceremony this whole plan exists to remove.

### 4. Any-repo, any-provider entry point

One canonical, self-contained runbook file at a stable path in this repo (exact
name/location TBD, see Open Questions) — no per-tool config, no global CLAUDE.md
entry, no skill registration anywhere. The user invokes this mode in any session, in
any repo, with whatever provider is active, by pointing at the file directly (e.g.
"read and follow `/home/emmanuel/PycharmProjects/esc-ai-orchestrator/<runbook>` ").

Because nothing loads this file automatically, it must be fully self-contained —
correct absolute paths to the CLI/registry, no assumption of prior conversation
context, and the complete step-by-step sequence (section 2's commands, section 1's
gate behavior, section 3's resolution loop) written so a fresh session in an
unrelated repo, with zero shared context, can follow it correctly on the first read.
This is a stricter bar than a CLAUDE.md entry would have needed (which could lean on
already being loaded, always, everywhere) — worth calling out explicitly since it
changes how the runbook needs to be written, not just where it lives.

## Non-goals

- Do not rebuild any part of the multi-repo dependency graph, verification, or
  auto-advancement engine — already done, reused as-is.
- Do not route the user's own architecture-framework edits through the future
  external-contributor CI/PR flow — that governance is for other people.
- Do not remove, weaken, or fork the existing interactive CLI — this is an additional
  entry point alongside it, not a replacement.
- Do not invent a new checkpoint/gate concept — the coverage hard stop reuses the
  exact mechanism task 6 already generalized for a second trigger; this is a third,
  not a new design.
- Do not build the entry point as a provider-specific integration (a Claude Code
  skill, a `~/.claude/CLAUDE.md` entry, or equivalent for any other tool) — the user
  shuffles between providers, and a plain file referenced by path is the one
  mechanism that doesn't need re-solving per tool.

## Open questions

1. **Exact runbook location/filename.** Leaning: a single markdown file at this
   repo's root or under `docs/` (e.g. `BACKDOOR.md` or `docs/backdoor-mode.md`) —
   root is more discoverable at a glance, `docs/` is more consistent with where
   other reference material tends to live; not decided.
2. **Whether the coverage gate needs a `--force`/override escape hatch** for a
   deliberate case where the user wants to proceed on a `stub` doc anyway (e.g.
   drafting the doc and the task that needs it in the same sitting). Leaning: no
   override initially — if this turns out to be needed in practice, add it once
   there's a real case, not speculatively now.
3. **How `answers.json`/`plan-answers.json` content gets generated when Tier 1
   detection can't infer it** (purpose, scope boundary, completion conditions) — out
   of this plan's scope per section 2 above, but worth a follow-up note if it turns
   out to be more friction than expected in practice.
4. **Whether the gate should also run for the *first*, human/script-submitted task**
   in an initiative, not just auto-advanced ones. Leaning: yes, unconditionally — the
   gate lives in `_AdapterRuntime.execute`, which every task goes through regardless
   of how it was submitted, so this isn't actually a design choice, just confirming
   the placement in section 1 already gives this for free.

## Tasks

1. **Architecture-coverage hard stop.** `_AdapterRuntime.execute` calls
   `build_task_context` before dispatching the adapter; on any in-scope
   `missing`/`stubs`, writes a checkpoint candidate via `_write_checkpoint_candidate`
   and marks the run `"failed"` without ever invoking the adapter. Tests: a task
   whose component cleanly resolves proceeds normally; a task referencing a missing
   doc ID stops with a checkpoint naming it; a task referencing a `stub` doc stops
   the same way; an auto-advanced (not just directly-submitted) task is gated too.
   No dependencies.
2. **Confirm and document the non-interactive multi-repo sequence end to end.**
   A test (or a real run against a scratch multi-repo setup) driving
   `repository add/analyze/answer/apply` → `plan draft/answer/apply` →
   `task run --yes` for the first task only, confirming task 7's auto-advancement
   completes the rest with zero further commands. Depends on nothing (task 1 changes
   what a gated run does, not whether this sequence itself works) but should run
   after (1) exists so the confirmation test also covers a clean-coverage path
   through the gate.
3. **Runbook document.** The single, self-contained, tool-agnostic file a session
   reads and follows when pointed at it by path — the real version of section 2's
   command sequence, plus section 1's gate behavior and section 3's resolution loop,
   written for a fresh session with zero prior context, concretely enough that
   following it doesn't require re-deriving any of this plan's reasoning. This *is*
   the entry point — no separate registration/pointer step anywhere else needed.
   Depends on (1) and (2) existing so the runbook describes real, working behavior,
   not aspiration.
