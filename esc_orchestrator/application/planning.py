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
from esc_orchestrator.application.errors import translates_engine_errors
from esc_orchestrator.application.ports import StateStore
from esc_orchestrator.application.repositories import planning_process_metrics, resolve_repository
from esc_orchestrator.domain.errors import ConflictError, IncompleteError, InvalidInputError, NotFoundError
from esc_orchestrator.domain.intents import initiative_id_for, work_type_for


def root_cause_from_answers(answers: dict[str, Any]) -> dict[str, Any] | None:
    """The root cause a plan's answers carry, or None if they carry none.

    Accepted in two shapes: a nested `root_cause` object (an answers file written by hand or by an AI
    operator), or the flat `root_cause_statement` / `root_cause_evidence` / `root_cause_reproduction` fields
    the interactive flow collects one question at a time. A blank flat field counts as not given."""
    nested = answers.get("root_cause")
    if nested is not None:
        return nested
    statement = (answers.get("root_cause_statement") or "").strip()
    evidence = (answers.get("root_cause_evidence") or "").strip()
    if not statement and not evidence:
        return None
    root_cause: dict[str, Any] = {"statement": statement, "evidence": evidence}
    reproduction = (answers.get("root_cause_reproduction") or "").strip()
    if reproduction:
        root_cause["reproduction"] = reproduction
    return root_cause


@translates_engine_errors
def draft_plan(store: StateStore, registry: Path, initiative_id: str, work_type: str, objective: str, repository_values: list[str]) -> dict[str, Any]:
    if work_type not in WORK_TYPES:
        raise InvalidInputError(f"work_type must be one of: {', '.join(WORK_TYPES)}")
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
    questions = planning_questions(matches_by_repo, work_type)
    store.save_plan_draft(initiative_id, work_type, objective, repositories, routing, questions)
    return {
        "initiative_id": initiative_id, "work_type": work_type, "objective": objective,
        "repositories": repositories, "routing": routing, "questions": questions,
    }


@translates_engine_errors
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
        raise NotFoundError(f"no plan draft for `{initiative_id}`; draft first")
    repositories = draft["repositories"]
    root_cause = root_cause_from_answers(answers)
    if draft["work_type"] == "fix" and root_cause is None:
        raise IncompleteError(
            f"a fix needs a root cause before `{initiative_id}` can be planned",
            hint=(
                "answer `root_cause`: {\"statement\": \"what is actually wrong\", \"evidence\": [\"how you know\"]} "
                "(the cause, not the symptom)"
            ),
        )
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
            local_architecture_notes=notes_by_repo.get(repository_id), root_cause=root_cause,
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
        written_paths = generate_multi_repository_workflow(
            registry, initiative_id, draft["objective"], draft["work_type"], tasks, root_cause=root_cause,
        )
        result = {}
        for repository_id, paths in written_paths.items():
            _, repository_path = resolve_repository(repository_id, registry)
            result[repository_id] = [str(path.relative_to(repository_path)) for path in paths]

    store.save_plan_result(initiative_id, answers, result)
    return result, dependency_graph



def store_plan_answers(store: StateStore, initiative_id: str, answers: dict[str, Any]) -> None:
    """Record plan answers for a later `apply`."""
    store.save_plan_pending_answers(initiative_id, answers)


@translates_engine_errors
def apply_pending_plan(store: StateStore, registry: Path, initiative_id: str) -> tuple[dict[str, Any], dict[str, list[str]] | None]:
    """Apply the answers recorded by `store_plan_answers`; `IncompleteError` when there are none."""
    pending = store.get_plan_pending_answers(initiative_id)
    if pending is None:
        raise IncompleteError(f"no pending answers for `{initiative_id}`; run `escape-ai initiative answer` first.")
    return apply_plan(store, registry, initiative_id, pending["answers"])


def plan_status(store: StateStore, initiative_id: str) -> dict[str, Any]:
    """Where an initiative is in draft -> answer -> apply, plus its still-unanswered `questions`."""
    draft = store.get_plan_draft(initiative_id)
    return {
        "initiative_id": initiative_id,
        "has_draft": draft is not None,
        "has_pending_answers": store.get_plan_pending_answers(initiative_id) is not None,
        "has_result": store.get_plan_result(initiative_id) is not None,
        "process_metrics": planning_process_metrics(store, initiative_id),
        "questions": draft["questions"] if draft else [],
    }


@translates_engine_errors
def draft_intent(
    store: StateStore, registry: Path, verb: str, objective: str, repositories: list[str] | None,
    initiative_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Draft an initiative for an intent verb. Returns `(initiative_id, draft)`.

    The verb only selects the procedure (`work_type_for`); every gate lives in the procedure and the
    execution machinery."""
    work_type = work_type_for(verb)
    if not repositories:
        raise InvalidInputError(f"`{verb}` needs at least one repository: -r <repository-id-or-path>")
    initiative_id = initiative_id or initiative_id_for(verb, objective)
    if store.get_plan_draft(initiative_id) is not None:
        raise ConflictError(f"initiative `{initiative_id}` already exists; pass --initiative-id to use another name.")
    return initiative_id, draft_plan(store, registry, initiative_id, work_type, objective, repositories)
