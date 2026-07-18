from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from esc_exec.adapters import detect_build_system
from esc_exec.dependencies import validate_dependency_graph
from esc_exec.indexing import validate_indexes
from esc_exec.manifests import overall_exit_code, validate_repository
from esc_exec.model import ValidationResult
from esc_exec.onboarding import analyze_repository, apply_onboarding_answers
from esc_exec.planning import (
    WORK_TYPES, generate_multi_repository_workflow, generate_single_repository_workflow,
    planning_questions, route_objective,
)
from esc_exec.registry import add_route, default_registry_path, resolve_route

from esc_orchestrator.store import Store


MENU = [
    "Onboard a repository",
    "Plan new work",
    "Resume active work",
    "Observe a run",
    "Configure system",
    "Validate the system",
]

NOT_YET_IMPLEMENTED = (
    "Not yet implemented -- this is a later phase of the Escape AI plan "
    "(see plan/cohesive-system-integration-and-onboarding.md). Only repository "
    "onboarding and planning new work are wired up so far."
)


# ---------------------------------------------------------------------------
# Pure rendering (no I/O) -- tested independently of the business logic below.
# ---------------------------------------------------------------------------

def render_menu() -> str:
    lines = ["Escape AI", "=========", "", "What would you like to do?"]
    lines += [f"  {i}. {item}" for i, item in enumerate(MENU, 1)]
    return "\n".join(lines)


def render_proposal(proposal: dict[str, Any]) -> str:
    lines = [
        f"Repository: {proposal['repository']['id']} ({proposal['repository']['type']})",
        "",
        "Files:",
    ]
    for entry in proposal["files"]:
        lines.append(f"  [{entry['action']:<9}] {entry['path']} -- {entry['evidence']}")

    suggestions = proposal.get("profile_id_suggestions") or {}
    if suggestions:
        lines += ["", "Suggested architecture.profile_ids:"]
        lines += [f"  {component_id}: {', '.join(ids)}" for component_id, ids in suggestions.items()]

    existing = proposal.get("existing_adoption") or {}
    present = [key for key, value in existing.items() if value]
    if present:
        lines += ["", "Existing adoption detected:"] + [f"  - {key}" for key in present]

    questions = proposal.get("semantic_questions") or []
    lines.append("")
    lines.append(
        f"{len(questions)} question(s) require your input before this can be applied."
        if questions else "No outstanding questions -- ready to apply."
    )
    return "\n".join(lines)


def render_apply_result(result: dict[str, Any]) -> str:
    lines = ["Applied. Files written or updated:"]
    lines += [f"  {path}" for path in result.get("written", [])]

    inheritance = result.get("workflow_inheritance") or {}
    if inheritance.get("created"):
        lines += ["Workflow inheritance files created:"] + [f"  {path}" for path in inheritance["created"]]
    if inheritance.get("existing"):
        lines += [
            "Existing workflow files left untouched (review these yourself):",
        ] + [f"  {path}" for path in inheritance["existing"]]

    if result.get("stub_documents"):
        lines += ["", "WARNING -- stub architecture documents referenced (not yet complete):"]
        lines += [f"  {component_id}: {', '.join(ids)}" for component_id, ids in result["stub_documents"].items()]

    if result.get("missing_documents"):
        lines += ["", "WARNING -- referenced architecture documents could not be resolved:"]
        lines += [f"  {component_id}: {', '.join(ids)}" for component_id, ids in result["missing_documents"].items()]

    if result.get("empty_profile_id_suggestions"):
        lines += ["", "No architecture.profile_ids could be suggested for: " + ", ".join(result["empty_profile_id_suggestions"])]

    lines += ["", "Nothing has been committed. Review the files above, then commit them yourself."]
    return "\n".join(lines)


def render_status(info: dict[str, Any]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in info.items())


def render_validation(results: list[ValidationResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"{result.state.value:<10} {result.path}")
        lines += [f"  - {message}" for message in result.messages]
    return "\n".join(lines)


def render_work_types() -> str:
    lines = ["Work type:"] + [f"  {i}. {work_type}" for i, work_type in enumerate(WORK_TYPES, 1)]
    return "\n".join(lines)


def render_plan_draft(draft: dict[str, Any]) -> str:
    lines = [
        f"Initiative: {draft['initiative_id']} ({draft['work_type']})",
        f"Objective: {draft['objective']}",
        "",
        "Repositories and routed components:",
    ]
    for repository_id in draft["repositories"]:
        matches = draft["routing"].get(repository_id, [])
        suggested = ", ".join(match["component_id"] for match in matches) or "no matches"
        lines.append(f"  {repository_id}: suggested [{suggested}]")
    lines += ["", f"{len(draft['questions'])} question(s) require your input before this can be applied."]
    return "\n".join(lines)


def render_plan_result(result: dict[str, Any]) -> str:
    lines = ["Planned. Files written:"]
    for repository_id, paths in result.items():
        lines.append(f"  {repository_id}:")
        lines += [f"    {path}" for path in paths]
    lines += ["", "Nothing has been committed. Review the files above, then commit them yourself."]
    return "\n".join(lines)


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


def analyze(store: Store, registry: Path, repository_id: str, repository_path: Path) -> dict[str, Any]:
    proposal = analyze_repository(repository_path, registry)
    store.save_onboarding_proposal(repository_id, proposal)
    return proposal


def apply_answers(store: Store, registry: Path, repository_id: str, repository_path: Path, answers: dict[str, Any]) -> dict[str, Any]:
    record = store.get_onboarding_proposal(repository_id)
    if record is None:
        raise ValueError(f"no onboarding proposal for `{repository_id}`; analyze first")
    result = apply_onboarding_answers(repository_path, record["proposal"], answers, registry)
    store.save_onboarding_answers(repository_id, answers, result)
    return result


def repository_status(store: Store, registry: Path, repository_id: str) -> dict[str, Any]:
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
        "instructions_file_present": bool(path and (path / "INSTRUCTIONS.md").is_file()),
        "workflows_directory_present": bool(path and (path / ".esc-ai" / "workflows").is_dir()),
    }


def validate_all(repository_path: Path, registry: Path) -> list[ValidationResult]:
    results = list(validate_repository(repository_path, registry))
    results += validate_indexes(repository_path)
    results.append(validate_dependency_graph(repository_path))
    return results


def draft_plan(store: Store, registry: Path, initiative_id: str, work_type: str, objective: str, repository_values: list[str]) -> dict[str, Any]:
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


def apply_plan(store: Store, registry: Path, initiative_id: str, answers: dict[str, Any]) -> dict[str, Any]:
    """
    A single-repository plan writes one task directly; a multi-repository plan
    chains each repository's task to the previous one in the declared order (the
    plan's own worked example -- contracts -> backend -> mobile -- is exactly this
    shape) rather than asking for a full dependency graph through the CLI. Both
    paths validate every reference before writing anything (see
    generate_single_repository_workflow/generate_multi_repository_workflow).
    """
    draft = store.get_plan_draft(initiative_id)
    if draft is None:
        raise ValueError(f"no plan draft for `{initiative_id}`; draft first")
    repositories = draft["repositories"]
    components_by_repo = answers.get("components", {})
    completion_conditions = answers.get("completion_conditions", [])
    scope_boundary = answers.get("scope_boundary", "")
    rollout_needs = answers.get("rollout_needs", "")

    if len(repositories) == 1:
        repository_id = repositories[0]
        _, repository_path = resolve_repository(repository_id, registry)
        written = generate_single_repository_workflow(
            repository_path, repository_id, initiative_id, draft["objective"], draft["work_type"],
            components_by_repo.get(repository_id, []), scope_boundary, completion_conditions, rollout_needs,
        )
        result = {repository_id: [str(path.relative_to(repository_path)) for path in written]}
    else:
        tasks: dict[str, Any] = {}
        previous_task_ref: str | None = None
        for repository_id in repositories:
            task_id = f"{initiative_id}-{repository_id}"
            task: dict[str, Any] = {
                "task_id": task_id,
                "components": components_by_repo.get(repository_id, []),
                "scope_boundary": scope_boundary,
                "completion_conditions": completion_conditions,
                "rollout_needs": rollout_needs,
            }
            if previous_task_ref:
                task["depends_on"] = [previous_task_ref]
            tasks[repository_id] = task
            previous_task_ref = f"{repository_id}/{task_id}"
        written_paths = generate_multi_repository_workflow(registry, initiative_id, draft["objective"], draft["work_type"], tasks)
        result = {}
        for repository_id, paths in written_paths.items():
            _, repository_path = resolve_repository(repository_id, registry)
            result[repository_id] = [str(path.relative_to(repository_path)) for path in paths]

    store.save_plan_result(initiative_id, answers, result)
    return result


# ---------------------------------------------------------------------------
# Interactive wizard -- thin glue between prompts and the operations above.
# ---------------------------------------------------------------------------

def run_interactive(store: Store, registry: Path) -> int:
    print(render_menu())
    try:
        choice = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 0
    if choice == "1":
        return run_onboarding_interactive(store, registry)
    if choice == "2":
        return run_planning_interactive(store, registry)
    if choice in {"3", "4", "5", "6"}:
        print(NOT_YET_IMPLEMENTED)
        return 0
    print("Unrecognized choice.")
    return 1


def _collect_answer(question: dict[str, Any], answers: dict[str, dict[str, Any]]) -> None:
    component_id, field = question["component_id"], question["field"]
    bucket = answers.setdefault(component_id, {})
    value = input(f"{question['prompt']} ").strip()
    if field == "purpose":
        bucket["purpose"] = value
        return
    if field == "frameworks":
        frameworks: dict[str, str] = {}
        for pair in value.split(","):
            pair = pair.strip()
            if ":" in pair:
                key, _, val = pair.partition(":")
                frameworks[key.strip()] = val.strip()
        bucket["frameworks"] = frameworks
        targets_raw = input("Targets (comma-separated, blank for none): ").strip()
        bucket["targets"] = [target.strip() for target in targets_raw.split(",") if target.strip()]
        return
    bucket[field] = value


def run_onboarding_interactive(store: Store, registry: Path) -> int:
    try:
        raw = input("Repository path: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written.")
        return 0

    try:
        repository_id, repository_path = resolve_repository(raw, registry)
    except ValueError as exc:
        print(f"Could not detect a supported build system: {exc}")
        return 1

    existing_proposal = store.get_onboarding_proposal(repository_id)
    try:
        proposal = analyze(store, registry, repository_id, repository_path)
    except (OSError, ValueError) as exc:
        print(f"Analysis failed: {exc}")
        return 1

    if existing_proposal and existing_proposal["input_digest"] == proposal["input_digest"]:
        print(f"Found an existing onboarding proposal for `{repository_id}` with unchanged inputs -- resuming.")
        existing_answers = store.get_onboarding_answers(repository_id)
        if existing_answers is not None:
            print("This repository was already onboarded with these inputs.")
            print(render_apply_result(existing_answers["result"]))
            try:
                again = input("Re-run anyway? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled.")
                return 0
            if again != "y":
                return 0
    elif existing_proposal:
        print(f"Repository inputs changed since the last analysis for `{repository_id}`; re-analyzed.")

    print(render_proposal(proposal))

    answers: dict[str, dict[str, Any]] = {}
    try:
        for question in proposal["semantic_questions"]:
            _collect_answer(question, answers)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written. The proposal is saved; resume anytime by running escape-ai again.")
        return 0

    try:
        confirm = input("Apply these answers and write manifests? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written. The proposal is saved; resume anytime by running escape-ai again.")
        return 0
    if confirm != "y":
        print("Cancelled -- nothing was written. The proposal is saved; resume anytime by running escape-ai again.")
        return 0

    try:
        result = apply_answers(store, registry, repository_id, repository_path, answers)
    except (OSError, ValueError) as exc:
        print(f"Apply failed: {exc}")
        return 1
    print(render_apply_result(result))
    return 0


def run_planning_interactive(store: Store, registry: Path) -> int:
    print(render_work_types())
    try:
        choice = input("> ").strip()
        work_type = WORK_TYPES[int(choice) - 1]
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 0
    except (ValueError, IndexError):
        print("Unrecognized work type.")
        return 1

    try:
        objective = input("Objective: ").strip()
        initiative_id = input("Initiative/task ID (a short slug, e.g. feature-user-export): ").strip()
        repos_raw = input("Repositories (comma-separated IDs or paths): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written.")
        return 0
    repository_values = [value.strip() for value in repos_raw.split(",") if value.strip()]

    try:
        draft = draft_plan(store, registry, initiative_id, work_type, objective, repository_values)
    except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
        print(f"Could not draft this plan: {exc}")
        return 1
    print(render_plan_draft(draft))

    answers: dict[str, Any] = {}
    try:
        for question in draft["questions"]:
            if question["field"] == "components":
                repository_id = question["repository"]
                value = input(f"{question['prompt']} ").strip()
                answers.setdefault("components", {})[repository_id] = [item.strip() for item in value.split(",") if item.strip()]
            elif question["field"] == "completion_conditions":
                value = input(f"{question['prompt']} ").strip()
                answers["completion_conditions"] = [item.strip() for item in value.split(",") if item.strip()]
            else:
                answers[question["field"]] = input(f"{question['prompt']} ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written. The draft is saved; resume anytime by running escape-ai again.")
        return 0

    store.save_plan_pending_answers(initiative_id, answers)

    try:
        confirm = input("Generate workflow files from these answers? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written. The draft and answers are saved; resume anytime by running escape-ai again.")
        return 0
    if confirm != "y":
        print("Cancelled -- nothing was written. The draft and answers are saved; resume anytime by running escape-ai again.")
        return 0

    try:
        result = apply_plan(store, registry, initiative_id, answers)
    except (OSError, ValueError) as exc:
        print(f"Apply failed: {exc}")
        return 1
    print(render_plan_result(result))
    return 0


# ---------------------------------------------------------------------------
# Non-interactive subcommands.
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="escape-ai")
    parser.add_argument("--registry", type=Path, default=None, help="Override the machine-local system catalog path")
    parser.add_argument("--db", type=Path, default=Path(".orchestrator/orchestrator.db"), help="Orchestrator state database path")
    subcommands = parser.add_subparsers(dest="command")

    repository = subcommands.add_parser("repository", help="Onboard and manage repositories")
    repository_commands = repository.add_subparsers(dest="repository_command", required=True)

    add = repository_commands.add_parser("add")
    add.add_argument("id")
    add.add_argument("path", type=Path)

    analyze_cmd = repository_commands.add_parser("analyze")
    analyze_cmd.add_argument("repository")
    analyze_cmd.add_argument("--json", action="store_true")

    answer_cmd = repository_commands.add_parser("answer")
    answer_cmd.add_argument("repository")
    answer_cmd.add_argument("answers_file", type=Path)

    repository_commands.add_parser("apply").add_argument("repository")
    repository_commands.add_parser("validate").add_argument("repository")
    repository_commands.add_parser("status").add_argument("repository")

    plan = subcommands.add_parser("plan", help="Plan a feature/fix and generate workflow files")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)

    plan_draft = plan_commands.add_parser("draft")
    plan_draft.add_argument("initiative_id")
    plan_draft.add_argument("request_file", type=Path)

    plan_answer = plan_commands.add_parser("answer")
    plan_answer.add_argument("initiative_id")
    plan_answer.add_argument("answers_file", type=Path)

    plan_commands.add_parser("apply").add_argument("initiative_id")
    plan_commands.add_parser("status").add_argument("initiative_id")

    return parser


def _dispatch_repository(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.repository_command == "add":
        add_route(registry, "repositories", args.id, args.path)
        print(f"REGISTERED repository `{args.id}` -> {args.path.expanduser().resolve()}")
        return 0

    if args.repository_command == "analyze":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            proposal = analyze(store, registry, repository_id, repository_path)
        except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(json.dumps(proposal, indent=2) if args.json else render_proposal(proposal))
        return 0

    if args.repository_command == "answer":
        try:
            repository_id, _ = resolve_repository(args.repository, registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        store.save_pending_answers(repository_id, answers)
        print(f"STORED     answers for `{repository_id}`; run `escape-ai repository apply {repository_id}` to write them.")
        return 0

    if args.repository_command == "apply":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        pending = store.get_pending_answers(repository_id)
        if pending is None:
            print(f"INCOMPLETE no pending answers for `{repository_id}`; run `escape-ai repository answer` first.")
            return 2
        try:
            result = apply_answers(store, registry, repository_id, repository_path, pending["answers"])
        except (OSError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_apply_result(result))
        return 0

    if args.repository_command == "validate":
        try:
            _, repository_path = resolve_repository(args.repository, registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        results = validate_all(repository_path, registry)
        print(render_validation(results))
        return overall_exit_code(results)

    if args.repository_command == "status":
        try:
            repository_id, _ = resolve_repository(args.repository, registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_status(repository_status(store, registry, repository_id)))
        return 0

    return 1


def _dispatch_plan(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.plan_command == "draft":
        request = json.loads(args.request_file.read_text(encoding="utf-8"))
        try:
            draft = draft_plan(
                store, registry, args.initiative_id,
                request["work_type"], request["objective"], request["repositories"],
            )
        except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_plan_draft(draft))
        return 0

    if args.plan_command == "answer":
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        store.save_plan_pending_answers(args.initiative_id, answers)
        print(f"STORED     answers for `{args.initiative_id}`; run `escape-ai plan apply {args.initiative_id}` to write them.")
        return 0

    if args.plan_command == "apply":
        pending = store.get_plan_pending_answers(args.initiative_id)
        if pending is None:
            print(f"INCOMPLETE no pending answers for `{args.initiative_id}`; run `escape-ai plan answer` first.")
            return 2
        try:
            result = apply_plan(store, registry, args.initiative_id, pending["answers"])
        except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_plan_result(result))
        return 0

    if args.plan_command == "status":
        draft = store.get_plan_draft(args.initiative_id)
        pending = store.get_plan_pending_answers(args.initiative_id)
        result = store.get_plan_result(args.initiative_id)
        print(render_status({
            "initiative_id": args.initiative_id,
            "has_draft": draft is not None,
            "has_pending_answers": pending is not None,
            "has_result": result is not None,
        }))
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = args.registry or default_registry_path()
    store = Store(args.db)
    if args.command is None:
        return run_interactive(store, registry)
    if args.command == "repository":
        return _dispatch_repository(args, store, registry)
    if args.command == "plan":
        return _dispatch_plan(args, store, registry)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
