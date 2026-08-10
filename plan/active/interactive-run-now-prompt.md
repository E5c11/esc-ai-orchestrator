# Interactive "Run now or save for later" Prompt — Plan

**Status:** Proposed
**Date:** 2026-08-10
**Objective:** After `plan apply` (and, for multi-repository plans, after `plan
ready`) writes a task's `task.yaml`, offer an inline `Run now? [y/N]` prompt so a
human operating the CLI directly doesn't have to separately look up and retype
`escape-ai task run <repository> <task-id> --yes` in a new invocation. Answering
gets a real human keystroke, not a flag — see "Non-goals" for why that distinction
is the entire point of this doc.

## Why this is a separate plan

Adjacent to `cli-discoverability.md` (done) — both are "reduce a human's manual
copy/retype step between two CLI commands" — but that plan was about *finding* the
right task ID when it's ambiguous; this one is about *acting* on a task ID escape-ai
already just told you, one step earlier in the same command's output. Also adjacent
to `pre-flight-consent-and-bounded-autonomy.md` (implemented) — that plan is what
`--yes` actually means today (an informed, scoped consent, not boilerplate) and
established `bypassPermissions` + worktree isolation as the real containment. This
plan does not touch either of those; it only changes how a human *reaches* the
already-existing `task run ... --yes` call, for the case where a human is typing
commands themselves rather than reviewing/approving something an AI operator
prepared.

## What we found

Real friction hit while dogfording escape-ai from inside a Claude Code session
(2026-08-10, `ampm-kmp`, the `offline-retry-queue-*` initiative): `plan draft` /
`plan answer` / `plan apply` all ran fine as Bash tool calls from the AI operator.
The dispatch step (`task run <repository> <task-id> --yes`) got denied twice by
"the Claude Code auto mode classifier" — confirmed as the same phenomenon
`pre-flight-consent-and-bounded-autonomy.md`'s "What we found" section already
documented on 2026-07-24 (this session's own `settings.local.json`
`autoMode.allow` entries, not `--permission-mode auto` doing dynamic judgment).
Adding a detailed `autoMode.allow` justification did not unblock it — plausibly
because the justification text is itself editable by the same agent attempting the
action, which is a weak trust boundary for specifically this category of action
(one agent triggering another unattended, broad-scope agent run). The one thing
that worked immediately: the human ran the identical `task run ... --yes` command
directly in their own terminal, outside the Claude Code session's tool-permission
pipeline entirely.

That's the shape this plan optimizes for: an AI operator (Claude or otherwise)
prepares everything up to the point of dispatch — drafts the plan, answers the
onboarding questions, applies it, regenerates indexes if `repository validate`
flags staleness — and hands the human one thing to do. Today that "one thing" is
"copy this exact command and paste it into your own terminal." This plan makes it
"press y."

## Design

### Where the prompt lives

On `plan apply <initiative-id>`, after the existing file-list output
(`render_apply_result`), when the invocation is running with an interactive
stdin (see "TTY detection" below):

- **Single-repository plan** (`apply` wrote exactly one `task.yaml`): prompt
  `Run {task_id} now? [y/N]`. A yes internally calls the same code path
  `task run {repository} {task_id} --yes` already uses — not a reimplementation,
  a direct call into the existing dispatch function so behavior can't drift
  between the two entry points.
- **Multi-repository plan** (`apply` wrote one `task.yaml` per repository,
  chained by declared order per `multi-repository-dependency-graph-planning.md`):
  only the first, dependency-free task is actually runnable yet. Prompt
  `Run {repository}/{task_id} now? [y/N]` for that one task only — do not prompt
  for the still-blocked ones. This mirrors `plan ready`'s own existing "only
  surfaces what's unblocked right now" behavior; the prompt is a shortcut into
  `plan ready` + `task run`, not a new readiness concept.
- **`plan ready <initiative-id>`**, run standalone (the case after an earlier
  task in the chain has since completed, surfacing newly-unblocked tasks): same
  prompt shape, once per newly-ready task it prints, offered as multiple
  independent y/N prompts in sequence — not a single "run all?" prompt, since
  the existing "treat this list as a menu, not a mandate" guidance
  (`BACKDOOR.md`) applies exactly as much to a human choosing interactively as it
  does to an AI operator choosing which ready tasks are actually relevant.

### "Save for later" is doing nothing, not a distinct code path

Answering `N` (or hitting enter on the `[y/N]` default) leaves the task exactly
as `plan apply` already left it today — `status: ready` in its `task.yaml`,
nothing dispatched. There's no separate "saved" state to track; the task was
already durably saved the moment `apply` wrote the file. The prompt only ever
skips a redundant second command, it never gates whether the work persists.

### TTY detection — fail closed to "save for later"

The prompt only fires when stdin is a real interactive terminal
(`sys.stdin.isatty()`). When it isn't — a script, a CI job, or exactly the case
that motivated this doc, an AI operator invoking `plan apply` as a Bash tool
call with no attached TTY — `apply` behaves exactly as it does today: prints the
file list, prints the existing "Nothing has been committed... run `task run`
yourself" hint, and returns. No prompt is shown, and critically, **the absence
of a TTY must never be silently treated as an implicit "no" that then falls
through to running anyway** — it has to be treated as "cannot ask, so don't
run," the same fail-closed posture `--settings` deny patterns already use per
`pre-flight-consent-and-bounded-autonomy.md`'s finding 1.

## Non-goals

- **No non-interactive flag that answers the prompt.** No `--run`,
  `--yes-to-all`, `--auto-run`, nothing an AI operator (or a script) could pass
  to satisfy `[y/N]` without a human physically present. That would just be
  `task run ... --yes` wearing a different name — the entire reason this plan
  exists is that dispatch should cost a human one keystroke, not that dispatch
  should become easier for something *other than* a human to trigger. If a
  future need for fully unattended, no-human-in-the-loop dispatch shows up,
  that's a different, explicit design question — argued for on its own, not
  smuggled in as a flag on this feature.
- **Does not change what `--yes` means or what it's gated by.** `task run`'s
  existing scope/consent/worktree-isolation model
  (`pre-flight-consent-and-bounded-autonomy.md`) is untouched; this only changes
  how a human reaches that same call.
- **Does not attempt to make an AI operator's Bash-tool-call path succeed
  where it's currently blocked.** That block (Claude Code's own auto-mode
  classifier refusing to let an agent trigger another unattended agent run) is
  arguably a correct, structural boundary, not a bug this plan is trying to
  route around by relocating the trigger somewhere the classifier doesn't look.
  See "TTY detection" above — a piped/non-interactive invocation gets exactly
  today's behavior, on purpose.
- **Does not add a prompt to `task run` itself.** `task run <repository>
  <task_id>` (no `--yes`) already is the preview step; adding a second
  confirmation prompt on top of an already-explicit `--yes` flag would be
  redundant. This plan's prompt only replaces the *separate, later* invocation
  a human would otherwise have to construct by hand.

## Open questions

1. Exact wording/formatting of the multi-task-ready case — one prompt per task
   in sequence (current lean) vs. listing all newly-ready tasks and asking once
   with a task-selection prompt (closer to a real "menu"). Leaning toward
   sequential y/N since it needs no new input-parsing UX, but not decided.
2. Whether `plan ready` (run standalone, not right after `apply`) should gain
   the same inline prompt, or stay read-only and let `task run` remain the only
   dispatch entry point for the "resuming later" case. Leaning toward yes, for
   consistency, but the "print-then-prompt" UX for N tasks needs the answer to
   open question 1 first.
3. Whether a declined (`N`) prompt should still print the equivalent `task run`
   command as a hint (so a human who says no now can still find it easily
   later, without re-running `plan apply`) — likely yes, cheap to add, not yet
   specified to exact wording.
