# Interactive Menu Completeness and Run Observability — Plan

**Status:** Proposed
**Date:** 2026-08-03
**Objective:** Close the gap between what `escape-ai`'s boot-time interactive menu
advertises and what it actually does. Three of the six top-level menu items
(`Observe a run`, `Configure system`, `Validate the system`) print
`NOT_YET_IMPLEMENTED` today; a first-time user's first instinct after onboarding is
to click exactly these. Separately, fold the already-built `task doctor` pre-flight
check into the guided "Resume active work → Execute now" path, which currently
bypasses it entirely.

## Why this is a separate plan

Distinct from `plan/active/escape-ai-improvements.md`'s findings (already fully
distilled into `pre-flight-doctor-and-gate-prerequisites.md`,
`run-outcome-surfacing.md`, and `cli-discoverability.md`, all Implemented) — those
were about the *raw, non-interactive* `escape-ai <subcommand>` surface used in
BACKDOOR/headless mode. This plan is about the *guided interactive menu*
(`escape-ai` with no args), a different entry point with its own, separate gap: most
of what this plan needs already exists as working non-interactive subcommands or
internal `Store`/registry functions — the work here is almost entirely wiring, not
new capability, with one exception (a registered-repository listing primitive that
doesn't exist anywhere yet).

Also distinct from `cohesive-system-integration-and-onboarding.md`'s Phase 9
("Optional richer interfaces" — a web or JetBrains UI as an alternative client of the
same orchestrator). This plan doesn't touch that; it's about the one interface that
already exists (the terminal menu) actually doing what it claims to do.

## What we found (grounding, same session)

- `MENU` (`escape_ai_cli.py:49`) lists all six items; `run_interactive`
  (`escape_ai_cli.py:1095`) only branches on `choice in {0, 1, 2}` (Onboard, Plan,
  Resume) — everything else falls through to a flat `print(NOT_YET_IMPLEMENTED)`
  (`escape_ai_cli.py:1106`).
- **"Validate the system"** has real backing logic already:
  `validate_all(repository_path, registry)` (`escape_ai_cli.py:417`) and
  `repository validate <id>` (`_dispatch_repository`, `escape_ai_cli.py:2048`) — but
  it's scoped to one repository at a time, and there is no menu path to it at all.
- **"Configure system"** has real backing logic already: `connect_provider`
  (`escape_ai_cli.py:577`), used by `provider auth <name>` (`_dispatch_provider`,
  `escape_ai_cli.py:2213`) and by the lazy `prompt_provider_setup_interactive` path
  triggered mid-execution — but nothing lets a user see or change the *already*
  connected provider outside of falling back to that one raw subcommand.
- **"Observe a run" has no backing logic anywhere**, interactive or not — this is the
  one real capability gap, not just a wiring gap. But the data it would need mostly
  already exists and is already exposed once, just not through `escape-ai`:
  - `Store` (`store.py`) already has `events(run_id)`, `summary(run_id)` (reads
    `verification-summary.json`), `output_document(run_id, filename)`, and
    `output_yaml(run_id, filename)` — all populated today (`_event` calls in
    `submit`/`submit_if_new`/`update_run`; `Scheduler` writes
    `verification-result.json`/`checkpoint.yaml`/`run.json` into the run's
    `output_path` as a normal part of `execute_task`).
  - `api.py`'s `server()` — the separate `esc-orchestrator` daemon (`cli.py`), not
    `escape-ai` — already exposes exactly this over HTTP: `GET /runs/<id>`,
    `/runs/<id>/events`, `/runs/<id>/summary`, `/runs/<id>/context`,
    `/runs/<id>/verification-plan`, `/runs/<id>/checkpoint`,
    `/runs/<id>/metrics`. None of these reads are wired into `escape-ai` at all —
    they're read-only `Store` calls, callable directly without needing the HTTP
    server or `Scheduler` in the loop.
  - Important constraint this shapes design around: `execute_task`
    (`escape_ai_cli.py:685`) is synchronous — it calls `scheduler.queue.join()`
    before returning, so by the time `escape-ai`'s interactive flow (or the raw
    `task run --yes`) prints anything, the run named is already finished. There is
    no "currently in progress, watch it live" case inside this CLI's process model
    today — a run is either not yet started or already done. "Observe a run" is
    therefore a **read/drill-down view of a recorded run's outcome**, not a live
    tail of an in-progress one.
- **No "list registered repositories" primitive exists anywhere** — not in
  `registry.py` (`read_registry` returns the raw catalog dict; nothing renders it),
  not in `escape_ai_cli.py`. Both "Validate the system" (needs to enumerate
  repositories to validate) and "Configure system" (a natural place to show what's
  registered) need this; it doesn't exist today even as a building block.
- `run_resume_interactive` (`escape_ai_cli.py:1849`) offers `["Execute now",
  "Promote checkpoint candidate"]` for a selected task, and "Execute now" goes
  straight to `render_execution_preview` — it never calls `doctor_check`
  (`runtime.py`, already used by both `task run`'s automatic pre-dispatch gate *and*
  the standalone `task doctor` subcommand). A user going through the guided menu
  hits exactly the friction `pre-flight-doctor-and-gate-prerequisites.md` was built
  to prevent, because the guided path never surfaces the check that already exists.
  (Note: `task run`'s automatic `EnvironmentPrerequisiteError`/`ArchitectureCoverageError`
  gate inside `_AdapterRuntime.execute` *does* still fire even from this interactive
  path, since `execute_task` goes through the same `Scheduler`/`Runtime` — so a real
  blocker isn't silently skipped. The gap is purely that it's a real dispatch attempt
  before the user finds out, when the same information is checkable up front for
  free via `task doctor`, exactly as findings #3/#5 already established for the raw
  CLI.)

## Design

### 1. A shared "list registered repositories" primitive

`registered_repository_ids(registry: Path) -> list[str]` (or similar, in
`escape_ai_cli.py` alongside its other pure-ish helpers) — reads
`read_registry(registry)["repositories"]` and returns the sorted key list. No new
schema; this only exposes data `read_registry` already returns. Both designs 2 and 3
below build on this.

### 2. "Validate the system" — wire into the menu, extend to all registered repositories

Loop `registered_repository_ids`, call `validate_all` (already resolves a
repository's path via `resolve_route`) for each, and render one combined report —
reusing `render_validation` per repository, prefixed with the repository ID, plus a
one-line total (`N/M repositories fully valid`). A repository whose path no longer
resolves (moved/deleted since being registered) is reported as its own line item
(`INVALID <id>: <path> no longer exists`), not a crash that aborts the whole loop —
one bad registration shouldn't hide every other repository's real validation result.
Matches `repository validate <id>`'s existing exit-code convention
(`overall_exit_code`) at the per-repository level; the combined interactive report
has no process exit code to set (same as every other interactive-menu action today).

### 3. "Configure system" — provider status/switch, repository listing

A small submenu (same `select_menu` pattern every other guided flow uses):

- **Show current provider** — `active_provider(registry)`; render `None` as
  "no provider connected yet" rather than an error, matching every other place this
  function's `None` case is already handled.
- **Connect / switch provider** — reuses `connect_provider` exactly as `provider auth`
  does today (same three-step subscription-route confirm: CLI installed → logged in
  → recorded), driven by `select_menu` over `KNOWN_PROVIDERS` instead of a typed
  `name` argument.
- **List registered repositories** — renders `registered_repository_ids` (design 1)
  with each one's resolved path, read-only.

Explicitly not a general settings/config editor (see Non-goals) — scoped to the two
things that already have a real backing mutation (`connect_provider`) or read
(`read_registry`) today.

### 4. "Observe a run" — read-only drill-down over a recorded run

New guided flow, reachable both as its own top-level menu item and (design 5) as a
third action alongside "Execute now"/"Promote checkpoint candidate" in
`run_resume_interactive`:

1. Reuse `active_work` (`escape_ai_cli.py:607`) to list tasks with a recorded run —
   already computes `repository_id`, `task_id`, `latest_run_status`, and whether a
   checkpoint is present, across every registered repository. Selecting one resolves
   its `latest_run` via `store.get_latest_run_for_task(task_id)` (already used by
   `prior_consent`).
2. Render, straight from existing `Store` reads — no new data collection, this is
   purely surfacing what's already recorded:
   - Run status/error/output_path (`store.get_run(run_id)`).
   - Event timeline (`store.events(run_id)` — currently just
     `run.queued`/`run.running`/`run.<final-status>`, which is the honest current
     granularity; see Non-goals).
   - Verification summary (`store.summary(run_id)`) when present.
   - Checkpoint (`store.output_yaml(run_id, "checkpoint.yaml")`) when present —
     reusing `render_checkpoint_candidate`, already built for `promote-checkpoint`.
3. A task with no recorded run at all (never dispatched) renders a plain "no runs
   yet for this task" instead of an error.

This is the one design in this plan that's a genuinely new rendering function
(`render_run_detail` or similar), not just wiring an existing one — but every value
it reads already exists in `Store` and is already exposed once, over HTTP, via
`api.py`. No change to `api.py` or the `esc-orchestrator` daemon; this reads the same
`Store` object `escape-ai` already opens for every other command.

### 5. Fold `task doctor` into "Resume active work → Execute now"

Before `render_execution_preview` in `run_resume_interactive`'s `action_choice == 0`
branch, call `doctor_check(repository_path, task_path, registry)` (same function
`task doctor` and `task run`'s automatic gate both already call) and print any
blockers the same way `_dispatch_task`'s `doctor` branch does, *before* asking
"Execute this task now?" — turning a blocker that would otherwise only surface after
a real (metered) dispatch attempt into an up-front, zero-cost warning, exactly
mirroring what `task doctor` already does standalone. Non-blocking: blockers are
shown, not enforced as a hard stop — the user can still choose to proceed (mirrors
`task run` today, where `--yes` alone is the actual gate, not a clean doctor result).

## Non-goals

- Live-tailing an in-progress run's agent output token-by-token. `execute_task` is
  synchronous within `escape-ai`'s process model (see grounding above) — there is
  nothing to tail; by the time the CLI can act, the run is done. A genuinely live
  view needs an async dispatch model (the `esc-orchestrator` HTTP daemon +
  `Scheduler` background queue already has one) fronted by a client that polls it
  while a run is in flight — that's `cohesive-system-integration-and-onboarding.md`
  Phase 9 territory (a richer interface as a client of the same orchestrator), not
  this plan.
- Adding new granular events (e.g. "gate N started/passed") to `Store.events`. The
  event timeline in design 4 renders whatever already exists
  (`run.queued`/`run.running`/`run.<final-status>`) — expanding the emitted event
  granularity is a separate, `Scheduler`-side change with its own tradeoffs, not
  bundled into a menu-wiring plan.
- A general system settings/config editor (default workspace kind, default policy,
  `--opencode` server URL, etc.). "Configure system" here is scoped to the two things
  that already have a real read/mutation today (provider, registered repositories) —
  broadening it to every tunable in `default_workspace`/`default_policy` is a
  separate decision (those are still explicitly marked as placeholders pending real
  "Configure system" support in the comment above `default_workspace`, but *which*
  knobs deserve a user-facing toggle first is its own design question, not decided
  here).
- Repository removal/de-registration. Design 1's listing primitive is read-only; no
  `repository remove` exists anywhere today (confirmed absent from both
  `registry.py` and `escape_ai_cli.py`), and adding one is a distinct, larger
  question (what happens to a removed repository's recorded runs/tasks/onboarding
  state in the `Store`?) than this plan's scope.
- Re-implementing any of `api.py`'s HTTP surface inside `escape-ai`, or vice versa.
  Design 4 reads the same `Store` both already read from independently; the two
  entry points (`escape-ai`'s synchronous CLI, `esc-orchestrator`'s async HTTP
  daemon) stay separate, as they are today.

## Open questions

1. Should "Observe a run" (design 4) let a user pick *which* run for a task (attempt
   history), or only ever the latest one? `Store` has `get_latest_run_for_task` but
   no "all runs for this task_id" query today — leaning toward latest-only for the
   first cut (matches `prior_consent`'s existing precedent of only ever consulting
   the latest run), extending to full history only if attempt-by-attempt comparison
   turns out to matter in practice.
2. Should design 5's doctor pre-flight be skippable (e.g. a "skip check" option) for
   a user who's already fixed a known-cosmetic blocker and doesn't want to re-run the
   check every time? Leaning toward no for the first cut — `doctor_check` is cheap
   (no dispatch, local-only checks), so re-running it every time costs nothing real,
   unlike the metered dispatch it's meant to protect.
