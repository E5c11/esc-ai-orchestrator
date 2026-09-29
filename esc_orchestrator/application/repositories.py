from __future__ import annotations

from pathlib import Path
from typing import Any
from esc_exec.adapters import detect_build_system
from esc_exec.dependencies import validate_dependency_graph
from esc_exec.indexing import validate_indexes
from esc_exec.manifests import validate_repository
from esc_exec.measurement import process_metrics
from esc_exec.model import ValidationResult
from esc_exec.onboarding import analyze_repository, apply_onboarding_answers
from esc_exec.registry import add_route, read_registry, resolve_route
from esc_orchestrator.application.ports import StateStore


# ---------------------------------------------------------------------------
# Operations -- delegate to esc_exec/Store only, no prompts/printing. These are
# what the end-to-end test exercises against a real repository.
# ---------------------------------------------------------------------------

def resolve_repository(value: str, registry: Path) -> tuple[str, Path]:
    """Resolve `value` as a registered repository ID, or as a filesystem path --
    registering it under its detected repository ID if it isn't registered yet."""
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        path = candidate.resolve()
        repository_id, _, _ = detect_build_system(path)
        try:
            resolve_route(registry, "repositories", repository_id)
        except (KeyError, FileNotFoundError):
            add_route(registry, "repositories", repository_id, path)
        return repository_id, path
    return value, resolve_route(registry, "repositories", value)


def analyze(
    store: StateStore, registry: Path, repository_id: str, repository_path: Path,
    extra_resolved_components: dict[str, str] | None = None,
) -> dict[str, Any]:
    proposal = analyze_repository(repository_path, registry, extra_resolved_components)
    store.save_onboarding_proposal(repository_id, proposal)
    return proposal


def apply_answers(
    store: StateStore, registry: Path, repository_id: str, repository_path: Path, answers: dict[str, Any],
    resolved_components: dict[str, str] | None = None, excluded_component_ids: list[str] | None = None,
) -> dict[str, Any]:
    record = store.get_onboarding_proposal(repository_id)
    if record is None:
        raise ValueError(f"no onboarding proposal for `{repository_id}`; analyze first")
    result = apply_onboarding_answers(
        repository_path, record["proposal"], answers, registry, resolved_components, excluded_component_ids,
    )
    store.save_onboarding_answers(repository_id, answers, result)
    return result


def onboarding_process_metrics(store: StateStore, repository_id: str) -> dict[str, Any] | None:
    """None until both a proposal and applied answers exist -- there is no elapsed
    time to report for an in-progress or never-started onboarding."""
    proposal_record = store.get_onboarding_proposal(repository_id)
    answers_record = store.get_onboarding_answers(repository_id)
    if proposal_record is None or answers_record is None:
        return None
    return process_metrics(
        "onboarding", repository_id,
        proposal_record["created_at"], answers_record["updated_at"],
        len(proposal_record["proposal"].get("semantic_questions", [])),
        len(answers_record["answers"]),
    )


def planning_process_metrics(store: StateStore, initiative_id: str) -> dict[str, Any] | None:
    draft_record = store.get_plan_draft(initiative_id)
    result_record = store.get_plan_result(initiative_id)
    if draft_record is None or result_record is None:
        return None
    return process_metrics(
        "planning", initiative_id,
        draft_record["created_at"], result_record["updated_at"],
        len(draft_record["questions"]), len(result_record["answers"]),
    )


def repository_status(store: StateStore, registry: Path, repository_id: str) -> dict[str, Any]:
    proposal_record = store.get_onboarding_proposal(repository_id)
    pending_record = store.get_pending_answers(repository_id)
    answers_record = store.get_onboarding_answers(repository_id)
    try:
        path = resolve_route(registry, "repositories", repository_id)
    except (KeyError, FileNotFoundError):
        path = None
    return {
        "repository_id": repository_id,
        "registered": path is not None,
        "has_proposal": proposal_record is not None,
        "has_pending_answers": pending_record is not None,
        "has_applied_answers": answers_record is not None,
        "instructions_file_present": bool(path and (path / ".esc-ai" / "INSTRUCTIONS.md").is_file()),
        "workflows_directory_present": bool(path and (path / ".esc-ai" / "workflows").is_dir()),
        "process_metrics": onboarding_process_metrics(store, repository_id),
    }


def validate_all(repository_path: Path, registry: Path) -> list[ValidationResult]:
    results = list(validate_repository(repository_path, registry))
    results += validate_indexes(repository_path)
    results.append(validate_dependency_graph(repository_path))
    return results


def registered_repository_ids(registry: Path) -> list[str]:
    return sorted(read_registry(registry).get("repositories", {}))


def validate_system(registry: Path) -> dict[str, list[ValidationResult] | str]:
    """
    `validate_all` for every registered repository, keyed by repository ID -- the
    "Validate the system" menu item, which `repository validate <id>` (the existing
    per-repository command) has no equivalent of. A repository whose registered path
    no longer resolves (moved/deleted since registration) reports as a plain error
    string instead of raising -- one stale registration must not hide every other
    repository's real validation result.
    """
    results: dict[str, list[ValidationResult] | str] = {}
    for repository_id in registered_repository_ids(registry):
        try:
            repository_path = resolve_route(registry, "repositories", repository_id)
        except (KeyError, FileNotFoundError) as exc:
            results[repository_id] = str(exc)
            continue
        results[repository_id] = validate_all(repository_path, registry)
    return results

