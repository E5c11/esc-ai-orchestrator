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
from esc_orchestrator.application.ports import StateStore
from esc_orchestrator.application.providers import connect_provider
from esc_orchestrator.application.repositories import (
    analyze,
    apply_pending_answers,
    repository_status,
    resolve_repository,
    store_answers,
    validate_all,
)
from esc_orchestrator.application.roadmap import show_roadmap, update_roadmap
from esc_orchestrator.application.runs import active_work
from esc_orchestrator.domain.errors import NotFoundError, UnsupportedRepositoryError
from esc_orchestrator.entrypoints.cli.errors import EXIT_FAILED, EXIT_OK, guarded
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

_SCAFFOLD_NEXT_STEP = "Then run: escape-ai repository add <id> <path> && escape-ai repository analyze <id>"


@guarded
def _dispatch_repository(args: argparse.Namespace, store: StateStore, registry: Path) -> int:
    if args.repository_command == "add":
        add_route(registry, "repositories", args.id, args.path)
        print(f"REGISTERED repository `{args.id}` -> {args.path.expanduser().resolve()}")
        return EXIT_OK

    if args.repository_command == "analyze":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
        except UnsupportedRepositoryError as exc:
            print(render_wizard_suggestion(
                f"No supported build system detected under `{args.repository}` ({exc}).", _SCAFFOLD_NEXT_STEP,
            ))
            return EXIT_FAILED
        except NotFoundError:
            print(render_wizard_suggestion(
                f"`{args.repository}` isn't a directory and isn't a registered repository.", _SCAFFOLD_NEXT_STEP,
            ))
            return EXIT_FAILED
        proposal = analyze(store, registry, repository_id, repository_path)
        print(json.dumps(proposal, indent=2) if args.json else render_proposal(proposal))
        return EXIT_OK

    if args.repository_command == "answer":
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        repository_id = store_answers(store, registry, args.repository, answers)
        print(f"STORED     answers for `{repository_id}`; run `escape-ai repository apply {repository_id}` to write them.")
        return EXIT_OK

    if args.repository_command == "apply":
        print(render_apply_result(apply_pending_answers(store, registry, args.repository)))
        return EXIT_OK

    if args.repository_command == "validate":
        _, repository_path = resolve_repository(args.repository, registry)
        results = validate_all(repository_path, registry)
        print(render_validation(results))
        return overall_exit_code(results)

    if args.repository_command == "status":
        repository_id, _ = resolve_repository(args.repository, registry)
        print(render_status(repository_status(store, registry, repository_id)))
        return EXIT_OK

    return EXIT_FAILED


@guarded
def _dispatch_resume(args: argparse.Namespace, store: StateStore, registry: Path) -> int:
    items = active_work(store, registry)
    print(json.dumps(items, indent=2) if args.json else render_active_work(items))
    return EXIT_OK


@guarded
def _dispatch_provider(args: argparse.Namespace, store: StateStore, registry: Path) -> int:
    if args.provider_command == "auth":
        route = args.route or ("subscription" if args.name in SUBSCRIPTION_CAPABLE_PROVIDERS else "api-key")
        connect_provider(registry, args.name, route)
        note = "" if route == "subscription" else (
            " -- routes through OpenCode; make sure it's configured for this provider (`opencode auth login`)."
        )
        print(f"CONNECTED  {args.name} ({route}){note}")
        return EXIT_OK
    return EXIT_FAILED


@guarded
def _dispatch_policy(args: argparse.Namespace, store: StateStore, registry: Path) -> int:
    if args.policy_command == "show":
        print(render_policy_status(default_policy_id(registry)))
        return EXIT_OK
    if args.policy_command == "set":
        set_default_policy(registry, args.profile_id)
        print(f"SET        default policy to `{args.profile_id}`.")
        return EXIT_OK
    return EXIT_FAILED


@guarded
def _dispatch_roadmap(args: argparse.Namespace, store: StateStore, registry: Path) -> int:
    if args.roadmap_command == "show":
        print(render_roadmap(show_roadmap(registry, args.repository)))
        return EXIT_OK
    if args.roadmap_command == "set":
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        repository_id = update_roadmap(registry, args.repository, answers)
        print(f"SET        roadmap for `{repository_id}`.")
        return EXIT_OK
    return EXIT_FAILED
