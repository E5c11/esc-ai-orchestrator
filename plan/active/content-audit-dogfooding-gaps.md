# escape-ai — gaps found running the AMPM content audit at scale (2026-11-09..11)

**Status:** Active — findings to be distilled into design docs, as `escape-ai-improvements.md` was.

## Why this is a separate plan

The content audit was the first time escape-ai ran long judgement tasks unattended, at volume (about 45 runs over
three days, two machines, one shared Claude account, 7-hour timeouts), instead of single coding tasks with a human
watching. Almost every gap below was worked around with ad-hoc shell scripts in `~/content-audit-runs/` (otherpc) and
`~/audit-run.sh` (the Mac). Each section says what happened, the workaround, and the fix.

## 1. Usage limits are not a run state

**What happened.** At 16:28 on 2026-11-10 both machines hit the account's 5-hour limit. Every queued task after that
failed in seconds and was recorded `failed`; the queues finished "done" with nothing done, and six hours were lost
until a human noticed. On otherpc the limit message ("You've hit your 5-hour limit · resets 5:50pm") is not in the run
log or `events.jsonl`, so nothing downstream can tell a limit from a real failure.
**Workaround.** The queue scripts probe `claude -p "Reply with OK"` before each task and after a failure; while limited,
sleep 15 minutes and retry the same task on its branch (up to 4 attempts).
**Fix.** The claude-code adapter detects the limit (and its reset time) and ends the run in a distinct state
(`paused_limit`, with `resets_at`); the scheduler holds the queue until then and resumes the same run. This is the
"run states and resumable runs" work (AMPM `plan/active/content-rules-framework.md` Phase 5).

## 2. Resume is informal

**What happened.** "Re-running the same task continues from its branch" works only because the agent sees earlier
commits; nothing tells it that it is resuming. The Mac runner adds a note to the prompt ("if this branch already has
commits from an earlier attempt, continue from where they stopped").
**Fix.** A resumed run gets an explicit resume block in its context: the previous attempt's last commit, status, and
the reason it stopped; checkpoints (`task promote-checkpoint`) used for long tasks.

## 3. A permission denial ends the run as "unknown"

**What happened.** Agents that ran `ss -ltnp` / `ps` / `pgrep` to check the database proxy were refused by the policy;
five runs then ended `unknown`, two of them before their dev upload, so their work had to be redone by follow-up tasks.
**Fix.** A denied command returns to the agent as a normal tool error with the reason, and the run continues; the final
status comes from the completion conditions and the commits, never `unknown` when the run produced work. Separately,
give the run the environment facts it keeps probing for (proxy up, `.env` present) through the pre-flight doctor, so it
has no reason to probe.

## 4. No sub-sessions inside a run

**What happened.** The audit needs an independent reviewer that has not seen the work (a blind verifier, run twice, a
third on a split; an image reviewer). The agent has no Agent tool, so tasks shell out to `claude -p` from Bash, which
works but is invisible to escape-ai (no record, no cost, no limit handling).
**Fix.** A first-class sub-session step (or allowing the provider's subagent tool under policy), recorded in the run
with its own transcript, and covered by the limit handling in 1.

## 5. State lives in the target repository

**What happened.** `.orchestrator/orchestrator.db` is tracked in the framework repo. Every `git pull` on otherpc needed
back up / checkout / pull / restore, and swapping the file under a running process made it read-only and lost a run's
result ("unknown"). Run folders under `.esc-ai/runs/` accumulate untracked, and 56 were committed by accident.
**Fix.** Keep orchestrator state outside the repository (e.g. `~/.local/state/esc-ai/<repo>.db`), and decide once whether
run records are committed (by escape-ai, in its own commit) or ignored.

## 6. No queue

**What happened.** Ordering, priorities, "stop after the current task", re-ordering mid-flight and keep-awake were all
shell: `run-one.sh`, `queue-all.sh` with a list file, `requeue.sh`, stop and done marker files, `hold-awake-*.py` (GNOME
inhibit) and `caffeinate` on the Mac. Reordering a running queue meant replacing the list file by inode and waiting for
a stop marker.
**Fix.** `escape-ai queue add|list|reorder|pause|resume|stop-after-current`, priorities, a queue that survives a reboot,
and sleep inhibition while it has work (systemd-inhibit / caffeinate), released when it is empty or paused.

## 7. One account, several machines

**What happened.** otherpc and the Mac share one Claude account, so they share one usage window; a third runner would
only drain it faster. The Mac has no escape-ai at all (Python repos can't be onboarded, and it was never installed), so
it ran a separate `claude -p` runner with its own history.
**Fix.** Install escape-ai on the Mac; machines take work from one shared queue; the scheduler knows which machines draw
on which account and paces them as one pool (and can route to another provider account where policy allows).

## 8. The base of a run is whatever the clone has

**What happened.** Tasks ran on rules that were hours old because otherpc's clone was only pulled by hand. The workaround
pulls `origin/main` (with the `orchestrator.db` dance from 5) at the start of each run.
**Fix.** A per-repo setting: fetch and base each worktree on the remote's default branch (or a named ref) at dispatch.

## 9. Task definitions repeat themselves

**What happened.** About 60 audit task.yaml files were generated by script, each with the same SETUP, HARD LIMITS and
VERIFY paragraphs; a change to one rule meant regenerating or hand-editing all of them, and tasks queued earlier kept
the old text.
**Fix.** Task templates with parameters and shared includes (`objective: !include audit-common.md`), resolved at
dispatch so queued tasks pick up the current version.

## 10. Results are hard to collect

**What happened.** Each run's real outcome (slips vs gaps, verifier verdicts, flags) lives in a markdown section of a
record on the run's branch, on whichever machine ran it. Summarising a night's work meant ssh, `git show`, and greps.
**Fix.** A structured final summary per run (JSON beside the human summary, declared by the task), and
`escape-ai results [--since] [--repo]` across machines.

## 11. Providers need a qualification gate

**What happened.** Codex was considered as extra capacity and scored 95% recall but 81–87% precision on the AMPM
reference set (Claude: 95% / 98.5%), so it was not used for audits.
**Fix.** A provider is routed judgement work only after passing the repository's scored evaluation (a reference set
with a gate); the routing policy records which providers passed which evaluations.

## 12. Cost and usage per run

Tokens and wall-clock per run (and per sub-session) were not visible, so capacity was estimated by hand ("3 to 4 tasks
per 5-hour window"). Links to `future/usage-analytics-and-telemetry.md`.

## Order

1, 3 and 5 first (they lost work); then 6 and 8 (they cost the most hand-holding); then 2, 4, 7; then 9–12.
