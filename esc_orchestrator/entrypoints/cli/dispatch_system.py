from __future__ import annotations

import argparse
import json
from pathlib import Path

from esc_exec.manifests import overall_exit_code
from esc_exec.registry import (
    SUBSCRIPTION_CAPABLE_PROVIDERS,
    add_route,
    default_policy_id,
    set_default_policy,
)
from esc_exec.roadmap import load_project_roadmap, save_project_roadmap
from esc_orchestrator.application.providers import connect_provider
from esc_orchestrator.application.repositories import (
    analyze,
    apply_answers,
    repository_status,
    resolve_repository,
    validate_all,
)
from esc_orchestrator.application.runs import active_work
from esc_orchestrator.entrypoints.cli.render import (
    render_active_work,
    render_apply_result,
    render_policy_status,
    render_proposal,
    render_roadmap,
    render_status,
    render_validation,
)
from esc_orchestrator.scaffold_wizards import render_wizard_suggestion
from esc_orchestrator.store import Store


def _dispatch_repository(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.repository_command == "add":
        add_route(registry, "repositories", args.id, args.path)
        print(f"REGISTERED repository `{args.id}` -> {args.path.expanduser().resolve()}")
        return 0

    if args.repository_command == "analyze":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
        except ValueError as exc:
            print(render_wizard_suggestion(
                f"No supported build system detected under `{args.repository}` ({exc}).",
                "Then run: escape-ai repository add <id> <path> && escape-ai repository analyze <id>",
            ))
            return 1
        except (KeyError, FileNotFoundError):
            print(render_wizard_suggestion(
                f"`{args.repository}` isn't a directory and isn't a registered repository.",
                "Then run: escape-ai repository add <id> <path> && escape-ai repository analyze <id>",
            ))
            return 1
        try:
            proposal = analyze(store, registry, repository_id, repository_path)
        except (OSError, ValueError) as exc:
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


def _dispatch_resume(args: argparse.Namespace, store: Store, registry: Path) -> int:
    items = active_work(store, registry)
    print(json.dumps(items, indent=2) if args.json else render_active_work(items))
    return 0


def _dispatch_provider(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.provider_command == "auth":
        route = args.route or ("subscription" if args.name in SUBSCRIPTION_CAPABLE_PROVIDERS else "api-key")
        try:
            connect_provider(registry, args.name, route)
        except ValueError as exc:
            print(f"INVALID    {exc}")
            return 1
        note = "" if route == "subscription" else (
            " -- routes through OpenCode; make sure it's configured for this provider (`opencode auth login`)."
        )
        print(f"CONNECTED  {args.name} ({route}){note}")
        return 0
    return 1


def _dispatch_policy(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.policy_command == "show":
        print(render_policy_status(default_policy_id(registry)))
        return 0
    if args.policy_command == "set":
        set_default_policy(registry, args.profile_id)
        print(f"SET        default policy to `{args.profile_id}`.")
        return 0
    return 1


def _dispatch_roadmap(args: argparse.Namespace, store: Store, registry: Path) -> int:
    try:
        repository_id, repository_path = resolve_repository(args.repository, registry)
    except (KeyError, FileNotFoundError) as exc:
        print(f"INVALID    {exc}")
        return 1
    if args.roadmap_command == "show":
        print(render_roadmap(load_project_roadmap(repository_path)))
        return 0
    if args.roadmap_command == "set":
        # A field omitted from answers_file keeps its current saved value rather
        # than being blanked -- a roadmap update is normally a small delta, not a
        # full restatement (see plan/done/project-vision-and-direction.md open
        # question 1).
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        current = (load_project_roadmap(repository_path) or {}).get("project_roadmap", {})
        save_project_roadmap(
            repository_path, repository_id,
            answers.get("purpose", current.get("purpose", "")),
            answers.get("current_stage", current.get("current_stage", "")),
            answers.get("direction", current.get("direction", "")),
            answers.get("durable_decisions", current.get("durable_decisions", [])),
        )
        print(f"SET        roadmap for `{repository_id}`.")
        return 0
    return 1

