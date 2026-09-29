from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.planning import (
    WORK_TYPES,
    generate_multi_repository_workflow,
    generate_single_repository_workflow,
    planning_questions,
    route_objective,
)
from esc_orchestrator.application.ports import StateStore
from esc_orchestrator.application.repositories import resolve_repository


def draft_plan(store: StateStore, registry: Path, initiative_id: str, work_type: str, objective: str, repository_values: list[str]) -> dict[str, Any]:
    if work_type not in WORK_TYPES:
        raise ValueError(f"work_type must be one of: {', '.join(WORK_TYPES)}")
    repositories: list[str] = []
    routing: dict[str, list[dict[str, Any]]] = {}
    matches_by_repo: dict[str, list] = {}
    for value in repository_values:
        repository_id, repository_path = resolve_repository(value, registry)
        repositories.append(repository_id)
        matches = route_objective(repository_path, objective)
        matches_by_repo[repository_id] = matches
        routing[repository_id] = [
            {"component_id": match.component_id, "score": match.score, "reasons": list(match.reasons)}
            for match in matches
        ]
    questions = planning_questions(matches_by_repo)
    store.save_plan_draft(initiative_id, work_type, objective, repositories, routing, questions)
    return {
        "initiative_id": initiative_id, "work_type": work_type, "objective": objective,
        "repositories": repositories, "routing": routing, "questions": questions,
    }


def apply_plan(
    store: StateStore, registry: Path, initiative_id: str, answers: dict[str, Any],
    local_architecture_notes_by_repo: dict[str, list[str]] | None = None,
) -> tuple[dict[str, Any], dict[str, list[str]] | None]:
    """
    A single-repository plan writes one task directly. A multi-repository plan
    writes one cross-linked task per repository, using each repository's own
    `answers["depends_on"]` entry (see plan/active/multi-repository-dependency-
    graph-planning.md) -- an arbitrary acyclic graph, not just a straight chain --
    when one was actually supplied; a repository missing from that dict (or the
    whole `depends_on` key missing entirely, e.g. an answers.json predating this
    field) falls back to depending on the repository immediately before it in
    declared order, exactly reproducing this function's original behavior. Both
    paths validate every reference before writing anything (see
    generate_single_repository_workflow/generate_multi_repository_workflow, which
    also rejects an unresolvable or cyclic depends_on graph).

    local_architecture_notes_by_repo (see
    offer_local_architecture_note_interactive) is keyed by repository_id, same
    shape as answers["components"] -- omitted entirely for the non-interactive
    CLI path, which never runs that check.

    Returns `(result, dependency_graph)` -- `dependency_graph` (plan/done/
    run-outcome-surfacing.md finding #7, generalized from a single chain to a real
    graph by the plan above) maps each repository to the list of other repository
    ids its task actually depends on, surfaced explicitly so `render_plan_result`
    can print it instead of leaving it only discoverable by reading `task.yaml`.
    None for a single-repository plan. `result` itself is unchanged in shape --
    only `store.save_plan_result` persists it, and that call site doesn't need the
    graph, which is fully re-derivable from the written task.yaml files at any time.
    """
    draft = store.get_plan_draft(initiative_id)
    if draft is None:
        raise ValueError(f"no plan draft for `{initiative_id}`; draft first")
    repositories = draft["repositories"]
    components_by_repo = answers.get("components", {})
    completion_conditions = answers.get("completion_conditions", [])
    scope_boundary = answers.get("scope_boundary", "")
    rollout_needs = answers.get("rollout_needs", "")
    notes_by_repo = local_architecture_notes_by_repo or {}

    if len(repositories) == 1:
        repository_id = repositories[0]
        _, repository_path = resolve_repository(repository_id, registry)
        written = generate_single_repository_workflow(
            repository_path, repository_id, initiative_id, draft["objective"], draft["work_type"],
            components_by_repo.get(repository_id, []), scope_boundary, completion_conditions, rollout_needs,
            local_architecture_notes=notes_by_repo.get(repository_id),
        )
        result = {repository_id: [str(path.relative_to(repository_path)) for path in written]}
        dependency_graph = None
    else:
        depends_on_answers = answers.get("depends_on")
        tasks: dict[str, Any] = {}
        dependency_graph = {}
        for index, repository_id in enumerate(repositories):
            previous_repository_id = repositories[index - 1] if index > 0 else None
            if depends_on_answers is not None and repository_id in depends_on_answers:
                dependency_repo_ids = depends_on_answers[repository_id]
            else:
                dependency_repo_ids = [previous_repository_id] if previous_repository_id else []
            dependency_graph[repository_id] = dependency_repo_ids

            task_id = f"{initiative_id}-{repository_id}"
            task: dict[str, Any] = {
                "task_id": task_id,
                "components": components_by_repo.get(repository_id, []),
                "scope_boundary": scope_boundary,
                "completion_conditions": completion_conditions,
                "rollout_needs": rollout_needs,
            }
            if dependency_repo_ids:
                task["depends_on"] = [f"{dep_repo}/{initiative_id}-{dep_repo}" for dep_repo in dependency_repo_ids]
            if notes_by_repo.get(repository_id):
                task["local_architecture_notes"] = notes_by_repo[repository_id]
            tasks[repository_id] = task
        written_paths = generate_multi_repository_workflow(registry, initiative_id, draft["objective"], draft["work_type"], tasks)
        result = {}
        for repository_id, paths in written_paths.items():
            _, repository_path = resolve_repository(repository_id, registry)
            result[repository_id] = [str(path.relative_to(repository_path)) for path in paths]

    store.save_plan_result(initiative_id, answers, result)
    return result, dependency_graph

