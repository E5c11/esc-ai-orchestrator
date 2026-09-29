from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.registry import active_provider
from esc_exec.yaml_io import load_yaml
from esc_orchestrator.application.providers import resolve_default_policy
from esc_orchestrator.application.repositories import resolve_repository
from esc_orchestrator.application.runs import (
    active_work,
    checkpoint_candidate,
    execute_task,
    prior_consent,
    promote_checkpoint,
    run_detail,
    run_worktree_diff,
    worktree_diff,
)
from esc_orchestrator.entrypoints.cli.interactive.configure import (
    prompt_provider_setup_interactive,
)
from esc_orchestrator.entrypoints.cli.render import (
    render_active_work,
    render_checkpoint_candidate,
    render_execution_preview,
    render_execution_result,
    render_run_detail,
)
from esc_orchestrator.entrypoints.cli.terminal import confirm, select_menu
from esc_orchestrator.runtime import doctor_check
from esc_orchestrator.store import Store


def _resume_item_label(item: dict[str, Any]) -> str:
    status = item["latest_run_status"] or "never run"
    checkpoint = " [checkpoint pending]" if item["checkpoint_present"] else ""
    return f"{item['repository_id']}/{item['task_id']} -- {status}, {item['attempts']} attempt(s){checkpoint} -- {item['objective']}"


def run_observe_interactive(store: Store, registry: Path) -> int:
    """
    "Observe a run" -- a task picker (reusing `active_work`, same as "Resume active
    work") followed by a read-only drill-down over that task's latest recorded run
    (see `run_detail`'s docstring for why this is a post-hoc view, not a live tail).
    """
    items = active_work(store, registry)
    if not items:
        print(render_active_work(items))
        return 0
    choice = select_menu("Observe a run -- select a task:", [_resume_item_label(item) for item in items])
    if choice is None:
        return 0
    selected = items[choice]
    repository_id, task_id = selected["repository_id"], selected["task_id"]
    _, repository_path = resolve_repository(repository_id, registry)
    detail = run_detail(store, task_id)
    print(render_run_detail(repository_id, task_id, detail, run_worktree_diff(detail, repository_path)))
    return 0


def run_resume_interactive(store: Store, registry: Path) -> int:
    items = active_work(store, registry)
    if not items:
        print(render_active_work(items))
        return 0

    choice = select_menu("Active work -- select a task:", [_resume_item_label(item) for item in items])
    if choice is None:
        return 0
    selected = items[choice]
    repository_id, task_id = selected["repository_id"], selected["task_id"]
    _, repository_path = resolve_repository(repository_id, registry)

    action_choice = select_menu(
        f"{repository_id}/{task_id} -- choose an action:",
        ["Execute now", "Promote checkpoint candidate", "Observe latest run"],
    )
    if action_choice is None:
        return 0

    if action_choice == 0:
        provider = active_provider(registry)
        if provider is None:
            provider = prompt_provider_setup_interactive(registry)
            if provider is None:
                print("Cancelled -- no provider connected.")
                return 0
        task_path = repository_path / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml"
        # Cheap, no-dispatch pre-flight (see plan/done/pre-flight-doctor-and-gate-
        # prerequisites.md and plan/active/interactive-menu-completeness.md design
        # 5) -- the same check `task doctor`/`task run`'s automatic gate already
        # run, surfaced here too so the guided path doesn't burn a real dispatch
        # attempt on a gap this would have caught for free. Non-blocking, same as
        # `task run`: a blocker is shown, not enforced -- `--yes`/this confirm
        # remains the actual gate.
        blockers = doctor_check(repository_path, task_path, registry)
        if blockers:
            print(f"BLOCKED    {len(blockers)} pre-flight issue(s) found; a real dispatch would likely fail before this task even starts:")
            for blocker in blockers:
                print(f"  - {blocker}")
        print(render_execution_preview(
            repository_id, task_id, load_yaml(task_path), provider,
            resolve_default_policy(registry), prior_consent(store, task_id),
        ))
        if not confirm("Execute this task now?"):
            print("Cancelled -- nothing was executed.")
            return 0
        result = execute_task(store, registry, repository_id, repository_path, task_id, provider)
        print(render_execution_result(result))
        return 0

    if action_choice == 1:
        try:
            candidate = checkpoint_candidate(store, repository_path, task_id)
        except ValueError as exc:
            print(f"Cannot promote: {exc}")
            return 1
        print(render_checkpoint_candidate(candidate, worktree_diff(repository_path, task_id)))
        if not confirm("Promote this checkpoint into the durable workflow?"):
            print("Cancelled -- nothing was promoted.")
            return 0
        path = promote_checkpoint(repository_path, task_id, candidate)
        print(f"Merged worktree back; no checkpoint to record." if path is None else f"Promoted checkpoint to {path}")
        return 0

    if action_choice == 2:
        detail = run_detail(store, task_id)
        print(render_run_detail(repository_id, task_id, detail, run_worktree_diff(detail, repository_path)))
        return 0

    return 0

