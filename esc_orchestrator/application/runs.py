from __future__ import annotations

from pathlib import Path
from typing import Any
from esc_exec.checkpoints import create_checkpoint, checkpoint_path, update_checkpoint
from esc_exec.worktree import merge_worktree
from esc_exec.registry import read_registry
from esc_exec.yaml_io import load_yaml
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.application.ports import StateStore

from esc_orchestrator.application.providers import (
    DEFAULT_OPENCODE_SERVER,
    default_adapter,
    default_workspace,
    resolve_default_policy,
    resolve_runtime,
)


def active_work(store: StateStore, registry: Path) -> list[dict[str, Any]]:
    """Read-only: every registered repository's `.esc-ai/workflows/active/*/task.yaml`,
    cross-referenced against this orchestrator's own run/attempt records. No writes."""
    catalog = read_registry(registry)
    items: list[dict[str, Any]] = []
    for repository_id, route in catalog.get("repositories", {}).items():
        repository_path = Path(route["path"])
        active_dir = repository_path / ".esc-ai" / "workflows" / "active"
        if not active_dir.is_dir():
            continue
        for task_dir in sorted(path for path in active_dir.iterdir() if path.is_dir()):
            task_path = task_dir / "task.yaml"
            if not task_path.is_file():
                continue
            task_document = load_yaml(task_path)
            task_id = task_document["task"]["id"]
            latest_run = store.get_latest_run_for_task(task_id)
            # "waiting-approval" (layer 6: a permission denial, not a code
            # failure) gets a checkpoint candidate the same way "failed" does --
            # both are a human-reviewable blocker, just a different kind of one.
            candidate_present = bool(
                latest_run and latest_run["status"] in ("failed", "waiting-approval") and latest_run.get("output_path")
                and (Path(latest_run["output_path"]) / "checkpoint.yaml").is_file()
            )
            items.append({
                "repository_id": repository_id,
                "task_id": task_id,
                "objective": task_document["task"]["objective"],
                "attempts": store.get_attempt_count(task_id),
                "latest_run_status": latest_run["status"] if latest_run else None,
                "checkpoint_present": candidate_present,
            })
    return items


def prior_consent(store: StateStore, task_id: str) -> dict[str, Any] | None:
    """
    The most recent run's recorded `bindings.consent`, if any -- see
    plan/future/pre-flight-consent-and-bounded-autonomy.md layer 1. Only the
    latest run is consulted (Store doesn't expose full run history queried by
    task), which is sufficient: a task's consent history only matters for
    deciding whether *this* dispatch needs to re-explain scope, and the most
    recent attempt is always the relevant precedent for that. A task with no
    prior run, or whose most recent run's adapter never wrote a consent
    binding (e.g. a fake/legacy runtime in tests), returns None -- treated by
    render_execution_preview as "not yet consented," never as an error.
    """
    run = store.get_latest_run_for_task(task_id)
    if run is None:
        return None
    run_document = store.output_document(run["id"], "run.json")
    if not run_document:
        return None
    return run_document.get("bindings", {}).get("consent")


def run_detail(store: StateStore, task_id: str) -> dict[str, Any]:
    """
    "Observe a run" -- a read-only drill-down over a task's latest recorded run,
    for the "Observe a run" menu item. Every value read here already exists in
    `Store` and is already exposed once, over HTTP, by `api.py`'s `GET /runs/<id>`
    family; this just surfaces the same reads through `escape-ai` directly, since
    `execute_task` runs synchronously (the run named is always already finished by
    the time this is callable -- there is nothing to tail live here, only to
    review after the fact).

    `checkpoint`, when present, is wrapped with a top-level `run_id` the same way
    `checkpoint_candidate` already does, so `render_checkpoint_candidate` (built
    for `promote-checkpoint`) can be reused verbatim rather than duplicated.
    """
    run = store.get_latest_run_for_task(task_id)
    if run is None:
        return {"run": None, "events": [], "summary": None, "checkpoint": None}
    checkpoint_document = store.output_yaml(run["id"], "checkpoint.yaml")
    checkpoint = {"run_id": run["id"], **checkpoint_document} if checkpoint_document else None
    return {
        "run": run,
        "events": store.events(run["id"]),
        "summary": store.summary(run["id"]),
        "checkpoint": checkpoint,
    }


def _task_id_suggestions(repository_path: Path, task_id: str) -> list[str]:
    """
    Sibling task IDs under `.esc-ai/workflows/active/` for `repository_path`
    whose directory name starts with the given (likely wrong) `task_id` -- the
    documented multi-repo convention is always `<initiative-id>-<repository-id>`,
    so a plain prefix match against the initiative-id-only guess a user is
    likely to type covers the real dogfooding case directly (see
    plan/done/cli-discoverability.md finding #1). No fuzzy-matching library;
    scoped to this one repository, not a cross-repository search (see that
    plan's open question 1). Returns `[]` when the active workflows directory
    doesn't exist or nothing matches, so callers fall back to their own plain
    "not found" message unchanged.
    """
    active_dir = repository_path / ".esc-ai" / "workflows" / "active"
    if not active_dir.is_dir():
        return []
    return sorted(
        path.name for path in active_dir.iterdir()
        if path.is_dir() and path.name != task_id and path.name.startswith(task_id)
    )


def execute_task(
    store: StateStore, registry: Path, repository_id: str, repository_path: Path, task_id: str, provider: dict[str, Any],
    runtime: Any = None, opencode_server: str = DEFAULT_OPENCODE_SERVER,
) -> dict[str, Any]:
    """
    Connects an approved, already-written task.yaml to real execution via the same
    Scheduler/Store the HTTP daemon uses -- submit, wait for the background worker to
    finish (queue.join()), then close. A CLI invocation is inherently one task at a
    time, so this reuses Scheduler's exact submit/execute/update_run sequence
    without needing a long-lived daemon around it.

    `provider` must be an already-connected provider record (see
    ensure_provider_configured) -- this function does not prompt or default one.
    """
    task_path = repository_path / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml"
    if not task_path.is_file():
        suggestions = _task_id_suggestions(repository_path, task_id)
        hint = f"did you mean: {', '.join(suggestions)}?" if suggestions else "plan apply first"
        raise ValueError(f"no task.yaml found for `{task_id}` in `{repository_id}`; {hint}")
    contracts = {
        "task": load_yaml(task_path),
        "workspace": default_workspace(repository_id),
        "adapter": default_adapter(provider),
        "policy": resolve_default_policy(registry),
    }
    attempt = store.record_attempt(task_id)
    scheduler = Scheduler(store, runtime or resolve_runtime(provider, registry, opencode_server), registry)
    try:
        _, run_id = scheduler.submit(contracts)
        scheduler.queue.join()
    finally:
        scheduler.close()
    run = store.get_run(run_id)
    return {
        "task_id": task_id, "run_id": run_id, "attempt": attempt,
        "status": run["status"], "error": run.get("error"), "output_path": run.get("output_path"),
    }


def checkpoint_candidate(store: StateStore, repository_path: Path, task_id: str) -> dict[str, Any]:
    """
    A failed run's real checkpoint.yaml is the usual candidate. A *succeeded*
    run has no checkpoint.yaml at all (that file is only ever written on the
    failure/blocked path) -- but if it kept a worktree (a real diff worth
    reviewing, see plan/future/pre-flight-consent-and-bounded-autonomy.md
    layer 4), it still needs the same review-before-merge step, so one is
    synthesized here rather than requiring `promote-checkpoint` to grow a
    second, parallel command just to reach the same merge step.

    A `succeeded-no-changes` run (see plan/done/run-outcome-surfacing.md) gets
    the same synthesized-candidate treatment for the opposite reason: there's no
    worktree diff to merge, but a human still needs a clear "here's why this
    needs attention" surface instead of `promote-checkpoint` just raising "no
    checkpoint candidate found."
    """
    run = store.get_latest_run_for_task(task_id)
    if run is None or not run.get("output_path"):
        raise ValueError(f"no run with a checkpoint candidate for `{task_id}`")
    candidate_path = Path(run["output_path"]) / "checkpoint.yaml"
    if candidate_path.is_file():
        return {"run_id": run["id"], **load_yaml(candidate_path)}
    task_path = repository_path / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml"
    objective = load_yaml(task_path)["task"]["objective"] if task_path.is_file() else task_id
    if run["status"] == "succeeded":
        run_document = store.output_document(run["id"], "run.json")
        worktree = (run_document or {}).get("bindings", {}).get("worktree")
        if worktree and worktree.get("kept"):
            return {
                "run_id": run["id"], "worktree_merge_only": True,
                "checkpoint": {"id": f"checkpoint-{task_id}", "task_id": task_id, "status": "ready-to-resume", "objective": objective},
                "progress": {
                    "completed": [], "decisions": [],
                    "remaining": ["Review the worktree diff below, then re-run with --yes to merge it."],
                    "blockers": [], "artifacts": [],
                },
            }
    if run["status"] == "succeeded-no-changes":
        return {
            "run_id": run["id"], "no_changes": True,
            "checkpoint": {"id": f"checkpoint-{task_id}", "task_id": task_id, "status": "ready-to-resume", "objective": objective},
            "progress": {
                "completed": [], "decisions": [],
                "remaining": [
                    "This run produced no changes and may need clarification or a different "
                    "approach -- review the run's own summary/artifact before deciding whether "
                    "to retry.",
                ],
                "blockers": [], "artifacts": [],
            },
        }
    raise ValueError(f"no checkpoint candidate found for `{task_id}`")


def promote_checkpoint(repository_path: Path, task_id: str, candidate: dict[str, Any]) -> Path | None:
    """Promotes a transient run-failure checkpoint candidate into the durable,
    committable location -- always after human review of `candidate`'s contents,
    never a blind copy triggered automatically on failure.

    `candidate["worktree_merge_only"]` (set only by checkpoint_candidate's
    synthesized succeeded-run case above) merges the task's worktree branch
    back and removes the worktree instead of writing a durable checkpoint --
    there's no real blocker to record, the merge is the whole point, so this
    returns None rather than a checkpoint path. A *failed* run's checkpoint
    never auto-merges here, even if it kept a worktree: independent
    verification already confirmed a succeeded run was clean (see
    task-orchestration-and-verification-loop.md's "trust the artifact, not the
    agent"), but a failed run's worktree may hold half-finished or broken
    edits -- merging those needs a human decision this function doesn't make
    for them. That worktree stays in place for manual inspection
    (`esc_exec.worktree.merge_worktree`/`remove_worktree` directly) until a
    dedicated resolution verb exists (see that plan doc's open question 5).

    `candidate["no_changes"]` (set only by checkpoint_candidate's synthesized
    succeeded-no-changes case) is the same "nothing to persist" shape as
    worktree_merge_only, just with nothing to merge either -- there's no
    worktree and no durable blocker, only a human having read the notice above.
    Returns None the same way."""
    if candidate.get("worktree_merge_only"):
        merge_worktree(repository_path, task_id)
        return None
    if candidate.get("no_changes"):
        return None
    checkpoint, progress = candidate["checkpoint"], candidate["progress"]
    task_path = repository_path / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml"
    if not task_path.is_file():
        raise ValueError(f"no task.yaml for `{task_id}` to attach the checkpoint to")
    kwargs = dict(
        run_id=checkpoint.get("run_id"), status=checkpoint.get("status", "blocked"),
        completed=progress.get("completed"), decisions=progress.get("decisions"),
        remaining=progress.get("remaining"), blockers=progress.get("blockers"),
        artifacts=progress.get("artifacts"), last_event_sequence=progress.get("last_event_sequence"),
    )
    if checkpoint_path(repository_path, task_id).is_file():
        return update_checkpoint(repository_path, task_id, **kwargs)
    return create_checkpoint(repository_path, task_path, **kwargs)

