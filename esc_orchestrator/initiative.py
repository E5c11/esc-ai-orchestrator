from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.json_io import write_json
from esc_exec.registry import read_registry
from esc_exec.yaml_io import load_yaml

from esc_orchestrator.store import Store


def _discover_initiative_graph(registry: Path, initiative_id: str) -> dict[str, list[str]]:
    """
    Scans every registered repository's `.esc-ai/workflows/active/*/task.yaml` (same
    precedent as `escape_ai_cli.py::active_work`) for tasks declaring
    `task.initiative.id == initiative_id`, returning {"repository/task_id": [depends_on,
    ...]} for every one found. Unlike a single repository's `esc-dependencies.json`, no
    document anywhere already aggregates this -- each task.yaml only knows its own
    depends_on, one file per repository, so the graph has to be reconstructed live.
    """
    catalog = read_registry(registry)
    graph: dict[str, list[str]] = {}
    for repository_id, route in catalog.get("repositories", {}).items():
        active_dir = Path(route["path"]) / ".esc-ai" / "workflows" / "active"
        if not active_dir.is_dir():
            continue
        for task_dir in active_dir.iterdir():
            task_path = task_dir / "task.yaml"
            if not task_path.is_file():
                continue
            task = load_yaml(task_path)["task"]
            initiative = task.get("initiative") or {}
            if initiative.get("id") != initiative_id:
                continue
            graph[f"{repository_id}/{task['id']}"] = list(initiative.get("depends_on", []) or [])
    return graph


def analyze_task_impact(
    store: Store, registry: Path, completed_task_id: str, output: Path | None = None,
) -> dict[str, Any]:
    """
    Given a task that just completed, determines which other tasks declared in the
    same initiative -- across every registered repository, not just the completed
    task's own -- are now fully unblocked (every depends_on entry satisfied), versus
    which remain blocked and on what. Mirrors dependencies.py::analyze_impact's
    document shape (schema_version, sorted string-list fields), but the underlying
    graph is reconstructed live via `_discover_initiative_graph` rather than loaded
    from one pre-built artifact.

    Only direct dependents of `completed_task_id` can change unblocked/blocked status
    as a result of this one completion -- a task blocked on a longer chain becomes
    unblocked only when its own immediate dependency later completes, a separate
    future call. Tasks unrelated to `completed_task_id` (not a direct dependent) are
    not reported here at all, even if coincidentally already unblocked by some earlier
    event; this is impact-of-this-completion, not a full initiative status dump.

    A completed task with no `task.initiative` at all (every single-repository plan
    today -- see task-orchestration-and-verification-loop.md task 1's note) is a
    de facto initiative-of-one: returns an empty result, not an error.
    """
    completed = store.get_task(completed_task_id)
    if completed is None:
        raise ValueError(f"no such task: {completed_task_id}")
    completed_task = store.contracts(completed_task_id)["task"]["task"]
    initiative = completed_task.get("initiative") or {}
    initiative_id = initiative.get("id")
    completed_node = f"{completed_task['repository']}/{completed_task_id}"
    document: dict[str, Any] = {
        "schema_version": 1,
        "initiative_id": initiative_id,
        "completed_task": completed_node,
        "newly_unblocked": [],
        "still_blocked": {},
    }
    if initiative_id:
        graph = _discover_initiative_graph(registry, initiative_id)

        def is_complete(node: str) -> bool:
            _, task_id = node.split("/", 1)
            task = store.get_task(task_id)
            return task is not None and task["status"] == "succeeded"

        # completed_node is complete by definition -- the caller is telling us so --
        # regardless of whether its own task.yaml happens to be part of the disk scan
        # above (e.g. a root task with no depends_on of its own is still a real node
        # other tasks depend on; nothing about this function's contract should hinge
        # on that file still being discoverable at analysis time).
        completed_set = {node for node in graph if is_complete(node)} | {completed_node}
        newly_unblocked: list[str] = []
        still_blocked: dict[str, list[str]] = {}
        for node, depends_on in sorted(graph.items()):
            if node == completed_node or node in completed_set or completed_node not in depends_on:
                continue
            remaining = sorted(set(depends_on) - completed_set)
            if remaining:
                still_blocked[node] = remaining
            else:
                newly_unblocked.append(node)
        document["newly_unblocked"] = newly_unblocked
        document["still_blocked"] = still_blocked

    if output:
        write_json(output, document)
    return document
