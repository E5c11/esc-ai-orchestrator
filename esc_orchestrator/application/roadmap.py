from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.roadmap import load_project_roadmap, save_project_roadmap
from esc_orchestrator.application.errors import translates_engine_errors
from esc_orchestrator.application.repositories import resolve_repository


@translates_engine_errors
def show_roadmap(registry: Path, repository: str) -> dict[str, Any] | None:
    """The repository's saved project roadmap document, or None when it has none."""
    _, repository_path = resolve_repository(repository, registry)
    return load_project_roadmap(repository_path)


@translates_engine_errors
def update_roadmap(registry: Path, repository: str, answers: dict[str, Any]) -> str:
    """Update a repository's roadmap. A field omitted from `answers` keeps its current saved value rather
    than being blanked -- a roadmap update is normally a small delta, not a full restatement (see
    plan/done/project-vision-and-direction.md open question 1). Returns the resolved repository id."""
    repository_id, repository_path = resolve_repository(repository, registry)
    current = (load_project_roadmap(repository_path) or {}).get("project_roadmap", {})
    save_project_roadmap(
        repository_path, repository_id,
        answers.get("purpose", current.get("purpose", "")),
        answers.get("current_stage", current.get("current_stage", "")),
        answers.get("direction", current.get("direction", "")),
        answers.get("durable_decisions", current.get("durable_decisions", [])),
    )
    return repository_id
