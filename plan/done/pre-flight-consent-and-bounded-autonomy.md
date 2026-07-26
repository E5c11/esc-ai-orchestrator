# Pre-Flight Consent and Bounded Autonomy — Plan

**Status:** Implemented
**Date:** 2026-07-24
**Implemented:** 2026-07-25 — all four layers shipped and tested: layer 3
(esc-ai-execution-framework `34395ae`), layer 4
(esc-ai-execution-framework `35bc50c`, esc-ai-orchestrator `a61e820`), layer 1
(esc-ai-execution-framework `9760d74`, esc-ai-orchestrator `8ce01f1`), layer 6
(esc-ai-execution-framework `67f5660`, esc-ai-orchestrator `b1eaa7d`). Landing
here doesn't mean every open question below was resolved -- several
sub-questions (exact discard-without-merging verb, the budget/cost hard-stop,
the initial hard-deny list's completeness) are explicitly still open, not
designed further than what's already noted.
**Objective:** Replace the Claude Code adapter's current all-or-nothing,
category-level permission gate with a three-layer model — one upfront human
sign-off on what a task is allowed to do, then full autonomy within that scope, a
small universal hard-deny list for categorically hazardous operations, and disposable
git-worktree isolation as the actual containment backstop — instead of trying to build
or rely on a mid-run classifier that stops the task every time it hits something
unanticipated.

## Why this is a separate plan

Same relationship to `native-cli-provider-adapters.md` as before: that plan
(Implemented) established the adapter's policy-to-permission mapping exists and is
per-adapter; this plan changes what that mapping should actually optimize for. It
supersedes an earlier draft of this same doc (`auto-permission-mode-for-task-
execution.md`, deleted rather than kept alongside this one, since nothing about it was
committed yet) that proposed leaning on Claude Code's `--permission-mode auto` as a
dynamic per-action classifier. That approach was tested and abandoned — see "What we
found" below — in favor of the model here.

## What we found

**First hypothesis, tested and falsified (2026-07-24):** `claude --help` documents
`--permission-mode auto` as a real mode, and this exact session witnessed a live Bash
call (`escape-ai repository apply ampm-backend`) get denied mid-session with a message
attributing it to "the Claude Code auto mode classifier." The working hypothesis was
that passing `--permission-mode auto` to the headless `claude -p` subprocess Claude
Code adapter shells out to would give task runs the same graduated, per-action risk
judgment.

Two direct tests against a real `claude -p` subprocess disproved this for the class of
action that matters most:

- `claude -p --permission-mode auto --tools Bash </dev/null "rm -rf <scratch-dir>"` —
  executed with **zero** intervention (`permission_denials: []`). No classifier
  judgment occurred at all for a generically destructive command once `Bash` was in
  the tool allowlist.
- The same command with an explicit `--settings '{"permissions":{"deny":
  ["Bash(rm -rf*)"]}}'` — cleanly denied (`permission_denials` populated, exit 0,
  `terminal_reason: "completed"`, no hang).

Conclusion: the live denial witnessed earlier in this session almost certainly came
from *this interactive session's own harness-level settings/rules* (deny-by-default
unless matched by an explicit allow pattern), not from `--permission-mode auto`'s own
dynamic judgment on an otherwise-ungoverned headless subprocess. `auto` mode alone,
with just a tool-category allowlist and no explicit rules, behaves close to
`bypassPermissions` for at least this class of action. Any real protection has to come
from explicit, deterministic `--settings` patterns — which do work, fail closed, and
don't hang — not from hoped-for dynamic classification.

**Second, more important realization, from directly living through today's task
runs:** even a working per-action classifier would have been the wrong tool for the
actual friction encountered. Every real stop today — wrong JDK on `PATH`, a stopped
Postgres container, a missing Firebase credentials path, two pre-existing stale tests
unrelated to the task — was legitimate, in-scope work hitting an environment gap, not
the agent attempting something hazardous. A fine-grained allow/deny classifier doesn't
prevent that category of stop; it just adds a *second* category of stop ("action not
on the recognized list") on top of it, and headless mode can't turn either kind into a
real "ask" — per the adapter's own documented design, "ask" is already treated as
denied, since there's no mid-run human-escalation mechanism. So a classifier-heavy
design converges on exactly the outcome being designed against: frequent stop → human
triages → retry, instead of a task running to completion unattended.

**Third finding, reframing what "scope" should mean:** the AMPM scoping run
(`legacy-migration-rollout-AMPM`) found that the correct interface change for a
per-userId DataStore flag lives in `core/session`, outside the three components
(`feature-launcher`, `feature-profile`, `core-backend`) the task actually declared.
A strict per-task path allowlist — the refinement the earlier draft of this plan
proposed as its main deliverable — would have hard-blocked exactly the move the task
legitimately needed to make. Real tasks routinely discover they need to touch code
outside where anyone thought to scope them; a permission model that treats "outside
the declared paths" as something to block, rather than something to just contain the
consequences of, fights the way this work actually unfolds.

## Design

Three layers, replacing the single category gate:

### 1. Pre-flight consent — one sign-off per task, not per action

Before dispatch, present the human with what the task is about to be allowed to do —
derived from the task's declared categories (edit/execute/network) and, informationally
(not as an enforced restriction — see finding 3 above), its declared components. One
explicit approval covers the whole run. This is the actual job the stub "Configure
system" menu item should grow into: not a generic global settings screen, but a
per-task review-and-approve step that happens once, before autonomous execution starts.
`escape-ai task run ... --yes` already exists as a flag; under this model it becomes
the thing that's actually confirming something concrete (a real scope), not just
skipping a boilerplate notice.

**Recording the approval (resolves open question 2 below):** `run.json`'s existing
`bindings` object already records `adapter`/`workspace`/`policy`/`tool_grant` for every
run (`claude_code_adapter.py:262`) — the same place a `"consent"` entry belongs:
`{granted_categories: [...], granted_at: <timestamp>, granted_by: "cli" | task_id of
the run that requested it}`. A fresh `task run` on a task with no prior consent record
prints the scope and requires `--yes`; a `resume` of an already-consented task reads the
existing record and does not re-prompt, since the scope hasn't changed. If a human later
broadens a task's granted categories (e.g. via `promote-checkpoint` after a category-
denial blocker — see layer 3 and open question 6 below), that write is itself a new
consent record, not a silent mutation of the old one — the run history keeps both, so
"what was this task actually allowed to do, and when" stays reconstructable from
`run.json`/`checkpoint.yaml` the same way task status already is.

### 2. Full autonomy inside the approved scope

Once approved, run with the granted tool categories under `--permission-mode
bypassPermissions` (not `auto` — evidence above shows no benefit to `auto` for this
purpose, and `bypassPermissions` is simpler and matches the adapter's own documented
safety reasoning: the tool allowlist, not the permission mode, is the actual
enforcement boundary). No mid-run classifier, no per-action escalation attempt. The
task works through incidental friction — adjusting a build flag, creating a helper
file, touching an adjacent module it discovers it needs — the same way a trusted human
contributor would, without stopping to ask about each one.

### 3. A small, static, universal hard-deny list

Not a per-task, per-path allow/deny schema (that was the abandoned refinement from the
earlier draft). A short, curated list of categorically hazardous operations that apply
to *every* task regardless of its declared scope, enforced via `--settings` deny
patterns (proven above to work headlessly). Kept intentionally small and stable across
all tasks, not task-specific — the goal is a short list of things that are *never* fine
autonomously, not an attempt to enumerate everything that *is* fine (that's what layer
1's consent already covers). Fixed in escape-ai's own code, not task- or
policy-configurable — a task can be granted less than this list implies (a narrower
category grant already blocks more), but nothing about a task's declared scope can
widen past it. **Resolves open question 3 below** with a concrete starting list, split
by the tool category it rides on top of:

- **`Bash` (only matters once `execute` is granted):** `Bash(git push --force*)`,
  `Bash(git push -f*)`, `Bash(git reset --hard*)`, `Bash(git clean -f*)`,
  `Bash(git branch -D*)`, `Bash(git filter-branch*)`, `Bash(rm -rf*)`, `Bash(sudo*)`.
  `rm -rf` denied unconditionally rather than trying to distinguish "safe" targets
  (e.g. `rm -rf build/`) from unsafe ones — `--settings` patterns match against the
  command string, not a resolved path, so there's no reliable way to tell those apart
  at the pattern level; legitimate cache-clearing goes through the build tool's own
  clean task (`./gradlew clean`) instead, which every task run already has access to
  once `execute` is granted at all.
- **`Read`/`Edit` (only matters once `edit` is granted, `Read` is granted by default):**
  `Read(**/*firebase-adminsdk*.json)`, `Read(**/*.pem)`, `Read(**/.env)`,
  `Edit(**/.git/**)` — never let the agent edit git internals directly, even with
  `edit` granted for ordinary source files.
- **Budget (not yet enforceable — see below):** a hard stop once a run's own recorded
  cost or elapsed time crosses a fixed threshold. `run.json`'s `adapter_metadata`
  already captures `total_cost_usd` per run today (`claude_code_adapter.py`'s
  `outcome.get("total_cost_usd")`, sourced from the Claude CLI's own stream-json result
  message) — the data needed for this already exists per run, it just isn't wired to a
  stop condition. This item can't use a `--settings` deny pattern (cost isn't known
  until the run is already in progress); it needs the orchestrator's own `Scheduler` to
  watch the running adapter process and kill it if the threshold is crossed mid-run —
  a real piece of implementation, not just a config value, and explicitly not designed
  further here.

Explicitly a starting point, not exhaustive — the list should grow only when a real
task run demonstrates a category of harm the above doesn't cover, not speculatively.

### 4. Disposable workspace isolation — the actual containment, not a separate concern

This is what makes layers 1-3 safe to rely on even when a task legitimately does
something no one anticipated (finding 3). Currently `default_workspace()` always
emits `kind: "local", isolation: "process"` — the agent operates directly on the real
repository checkout, live, in the same working tree a human might be using
concurrently. There is no `git worktree add` call anywhere in
`esc-ai-execution-framework` despite `workspace.kind: "worktree"` already being a
schema-legal value (`contracts.py:110`).

Under this plan, that's no longer an independent, lower-priority gap — it's the reason
layers 1-3 are sufficient without also needing a fine-grained classifier: if the task
wanders somewhere unexpected, the blast radius is a disposable worktree, reviewable and
discardable, not the live checkout.

**A working convention for this already exists on disk, unrelated to escape-ai — mirror
it rather than designing from zero (resolves open question 4 below).** Found live in
`ampm-backend` during this same session: `.claude/worktrees/agent-<hash>/`, on branch
`worktree-agent-<hash>`, created by Claude Code's own `Agent` tool (`isolation:
"worktree"` option) for an earlier, unrelated task. Confirmed directly:
- Excluded via `.git/info/exclude` (line `**/.claude/worktrees/`) — a *local*, per-clone
  exclusion, not a change to the repository's own `.gitignore`. Nothing about adopting
  this convention requires touching a target repository's tracked files.
- Each worktree has its own full checkout — `gradlew`, `build.gradle.kts`, every
  module directory, a per-worktree `.gradle` directory — and is independently listed by
  `git worktree list` alongside the main checkout, each on its own branch.
- **Host-level state is unaffected by which worktree is active, which resolves the
  environment-inheritance half of open question 4 directly:** `JAVA_HOME`, `PATH`,
  Docker containers (the Postgres container from today's real run), and absolute-path
  credential files (the Firebase service-account JSON, referenced by an absolute path
  outside any checkout) are all host-level, not per-checkout — a subprocess launched
  with cwd inside a worktree inherits exactly the same environment a subprocess in the
  main checkout would. The only per-checkout cost is losing Gradle's *incremental*
  build-output cache (`build/`) on the first run in a fresh worktree — Gradle's
  dependency-jar/wrapper cache (`~/.gradle/caches`, `GRADLE_USER_HOME`) is outside any
  checkout and stays shared. A performance cost on first use, not a correctness
  problem, and not something blocking adoption.
- Cleanup policy mirrors the `Agent` tool's own documented behavior (see this session's
  tool description for `isolation: "worktree"`): a worktree that ends with no changes is
  removed automatically; one with real changes is kept, named and branched, for
  deliberate follow-up. The same rule fits here directly — an escape-ai task run that
  produces no diff needs no review step at all; one that does keeps its worktree until
  reviewed.

**Review-before-merge reuses the existing checkpoint preview, not a new UX (resolves
open question 5 below):** `escape-ai task promote-checkpoint <repo> <task-id>` already
previews a stopped task's checkpoint without `--yes`, and only acts with it
(`escape_ai_cli.py`'s `promote_checkpoint`, confirmed via `--help`). Once a task's
workspace is worktree-isolated, that same preview is the natural place to also surface
`git diff` against the base branch inside the worktree — extending the checkpoint
artifact with a diff/changed-files summary the same way `verification-result.json` is
already an artifact of a run, rather than inventing a separate diff-review surface
(a draft PR, a dashboard) alongside it. `--yes` on `promote-checkpoint` becomes "merge
the worktree branch back and remove the worktree"; declining (no further action, or a
future explicit "discard" verb) leaves it in place for now or removes it without
merging. This turns "did the agent do something we didn't expect" from a question that
needs preventing mid-run into a question answered at the same review point that
already exists for a blocked/failed task, not a new one.

## Non-goals

- Do not build a per-task, per-path permission schema (`edit_patterns`,
  `execute_allow`/`execute_deny` scoped to a task's declared components). This was the
  earlier draft's main proposed deliverable; finding 3 above is a direct
  counter-example to why it's the wrong shape for real tasks.
- Do not rely on `--permission-mode auto` for anything safety-relevant. Keep it out of
  the design; use `bypassPermissions` inside the approved scope, per the adapter's own
  documented reasoning about where the real enforcement boundary lives.
- Do not attempt a general mid-run "ask a human" escalation mechanism. Headless
  execution has no one to ask; the adapter's existing "ask == deny" discipline stays
  correct. The hard-deny list (layer 3) and worktree containment (layer 4) are the
  substitutes for escalation, not an escalation mechanism itself.
- Do not make the hard-deny list (layer 3) task-configurable or large. It's meant to
  stay short and universal — if it grows into a large, task-specific ruleset, it's
  drifted back into the rejected fine-grained-classifier shape.
- Do not attempt this for the Codex adapter — unchanged from the earlier draft's
  finding: Codex has no per-action granularity to build any of this on top of.

## Open questions

1. **Resolved 2026-07-24, verified live:** `--permission-mode auto` does not reliably
   intervene on a generic destructive Bash command with no explicit rules; explicit
   `--settings` deny patterns do, cleanly, with no hang. See "What we found" above for
   the two test transcripts.
2. **Resolved 2026-07-24, design only, not yet built:** approval is recorded, not
   re-prompted per run. See layer 1's "Recording the approval" above — a `"consent"`
   entry alongside `run.json`'s existing `adapter`/`workspace`/`policy`/`tool_grant`
   bindings. A fresh task needs a fresh `--yes`; a `resume` of an already-consented task
   doesn't re-prompt; a broadened grant after a category-denial blocker (question 6)
   writes a new consent record rather than mutating the old one. Remaining open
   sub-question: exact field names/schema location — sketched, not specified to
   implementation detail.
3. **Resolved 2026-07-24, first concrete list, not exhaustively tested:** see layer 3
   above for the split `Bash`/`Read`+`Edit`/budget list. Explicitly a starting point —
   grows only from a real task run demonstrating a gap, not speculatively. The budget
   item specifically stays open past this: it needs `Scheduler`-level process
   monitoring, not a `--settings` pattern, and is real, unscoped implementation work.
4. **Resolved 2026-07-24, verified against a real example on disk:** mirror
   `.claude/worktrees/agent-<hash>/` (branch `worktree-agent-<hash>`, `.git/info/exclude`
   entry, per-worktree `.gradle` dir) rather than designing worktree layout from
   scratch. Environment inheritance (JDK, Postgres, credential paths) is confirmed
   host-level, not per-checkout, so today's real environment gaps would have hit exactly
   the same way inside a worktree as they did in the live checkout — worktree isolation
   changes *where edits land*, not *what environment the task sees*. Remaining open
   sub-question: exact on-disk location for escape-ai's own worktrees specifically
   (mirror `.claude/worktrees/` verbatim, or a parallel `.esc-ai/worktrees/` alongside
   the existing `.esc-ai/runs/` convention?) — leaning toward the latter, for
   consistency with escape-ai's own existing per-run artifact layout, but not decided.
5. **Resolved 2026-07-24, design only, not yet built:** reuse `escape-ai task
   promote-checkpoint`'s existing preview-without-`--yes` / act-with-`--yes` UX,
   extended with a diff/changed-files summary sourced from the worktree, rather than
   building a separate review surface. `--yes` becomes "merge the worktree branch back
   and remove the worktree." Remaining open sub-question: the exact verb/flag for
   "discard without merging" as distinct from "haven't decided yet" — not designed.
6. **Resolved 2026-07-24, reuses an existing mechanism rather than needing a new one:**
   no live escalation — confirmed correct, not just assumed, by
   `task-orchestration-and-verification-loop.md`'s already-implemented task 6
   ("not-clean path into existing checkpoints"), which routes a verification failure
   into `checkpoint_document`/`promote_checkpoint` as a *new trigger for an existing
   path*, not a new mechanism. A category-denial (hard-deny hit, or a task discovering
   mid-run it needs an ungranted category) should become a third trigger into that same
   `_write_checkpoint_candidate` path, alongside verification-failure and
   uncaught-exception — exactly the shape today's real blockers already took
   (`checkpoint.yaml`'s `blockers: [...]`, `"Resolve the recorded blocker and resume the
   task"`) for the JDK/Postgres/credentials/stale-test stops encountered live this
   session. Remaining open sub-question: whether a category-denial blocker needs a
   distinguishable shape from a verification-failure blocker in `checkpoint.yaml`
   (probably yes, so a human previewing it immediately knows "this needs a wider grant"
   versus "this needs a code fix") — not designed to schema detail.
