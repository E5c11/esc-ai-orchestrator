from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from esc_exec.manifests import overall_exit_code
from esc_exec.procedures import PROCEDURES
from esc_exec.registry import (
    SUBSCRIPTION_CAPABLE_PROVIDERS,
    active_provider,
    add_route,
    default_policy_id,
    set_default_policy,
)
from esc_exec.roadmap import load_project_roadmap, save_project_roadmap
from esc_exec.yaml_io import load_yaml
from esc_orchestrator.application.planning import apply_plan, draft_plan
from esc_orchestrator.application.providers import (
    connect_provider,
    resolve_default_policy,
)
from esc_orchestrator.application.repositories import (
    analyze,
    apply_answers,
    planning_process_metrics,
    repository_status,
    resolve_repository,
    validate_all,
)
from esc_orchestrator.application.runs import (
    _task_id_suggestions,
    active_work,
    checkpoint_candidate,
    execute_task,
    prior_consent,
    promote_checkpoint,
)
from esc_orchestrator.domain.intents import INTENT_WORK_TYPES, LEGACY_PLAN_SUBCOMMANDS
from esc_orchestrator.entrypoints.cli.render import (
    render_active_work,
    render_apply_result,
    render_checkpoint_candidate,
    render_execution_preview,
    render_execution_result,
    render_plan_draft,
    render_plan_result,
    render_policy_status,
    render_procedure,
    render_proposal,
    render_roadmap,
    render_status,
    render_validation,
)
from esc_orchestrator.initiative import analyze_task_impact, find_ready_tasks
from esc_orchestrator.runtime import doctor_check
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


def _initiative_id_for(verb: str, objective: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", objective.lower()).strip("-")[:40].strip("-")
    return f"{verb}-{slug}" if slug else verb


def _dispatch_intent(args: argparse.Namespace, store: Store, registry: Path) -> int:
    """BLA-42 front door: an intent verb drafts an initiative through the same
    draft_plan the `initiative` group and the interactive menu use. It only selects
    the procedure; every gate lives in PROCEDURES and the execution machinery."""
    verb = args.command
    work_type = INTENT_WORK_TYPES[verb]
    if work_type is None:
        print(f"UNAVAILABLE `{verb}` has no pipeline yet (planned in BLA-44). Its procedure will be:")
        print(render_procedure(verb))
        return 2
    if not args.repositories:
        print(f"INVALID    `{verb}` needs at least one repository: -r <repository-id-or-path>")
        return 1
    initiative_id = args.initiative_id or _initiative_id_for(verb, args.objective)
    if store.get_plan_draft(initiative_id) is not None:
        print(f"INVALID    initiative `{initiative_id}` already exists; pass --initiative-id to use another name.")
        return 1
    try:
        draft = draft_plan(store, registry, initiative_id, work_type, args.objective, args.repositories)
    except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
        print(f"INVALID    {exc}")
        return 1
    if args.json:
        print(json.dumps({**draft, "intent": verb, "procedure": [stage.name for stage in PROCEDURES[verb]]}, indent=2))
        return 0
    print(render_plan_draft(draft))
    print()
    print(render_procedure(verb))
    print()
    print(f"Next: `escape-ai initiative answer {initiative_id} <answers.json>`, then `escape-ai initiative apply {initiative_id}`.")
    return 0


def _rewrite_legacy_plan_argv(argv: list[str]) -> list[str]:
    """`plan draft|answer|apply|status|ready ...` -> `initiative ...`, with a notice,
    so existing scripts survive `plan` becoming an intent verb."""
    index = 0
    while index < len(argv) and argv[index].startswith("-"):
        index += 2 if argv[index] in ("--registry", "--db") else 1
    if index + 1 < len(argv) and argv[index] == "plan" and argv[index + 1] in LEGACY_PLAN_SUBCOMMANDS:
        print(
            f"note: `escape-ai plan {argv[index + 1]}` is deprecated; use `escape-ai initiative {argv[index + 1]}`.",
            file=sys.stderr,
        )
        return [*argv[:index], "initiative", *argv[index + 1:]]
    return argv


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
        print(json.dumps(draft, indent=2) if args.json else render_plan_draft(draft))
        return 0

    if args.plan_command == "answer":
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        store.save_plan_pending_answers(args.initiative_id, answers)
        print(f"STORED     answers for `{args.initiative_id}`; run `escape-ai initiative apply {args.initiative_id}` to write them.")
        return 0

    if args.plan_command == "apply":
        pending = store.get_plan_pending_answers(args.initiative_id)
        if pending is None:
            print(f"INCOMPLETE no pending answers for `{args.initiative_id}`; run `escape-ai initiative answer` first.")
            return 2
        try:
            result, dependency_chain = apply_plan(store, registry, args.initiative_id, pending["answers"])
        except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_plan_result(result, dependency_chain))
        return 0

    if args.plan_command == "status":
        draft = store.get_plan_draft(args.initiative_id)
        pending = store.get_plan_pending_answers(args.initiative_id)
        result = store.get_plan_result(args.initiative_id)
        info = {
            "initiative_id": args.initiative_id,
            "has_draft": draft is not None,
            "has_pending_answers": pending is not None,
            "has_result": result is not None,
            "process_metrics": planning_process_metrics(store, args.initiative_id),
        }
        # --json also surfaces the real pending-question array (see
        # plan/done/cli-discoverability.md finding #2) -- omitted from the plain
        # render, which stays a compact summary.
        print(json.dumps({**info, "questions": draft["questions"] if draft else []}, indent=2) if args.json else render_status(info))
        return 0

    if args.plan_command == "ready":
        ready = find_ready_tasks(store, registry, args.initiative_id)
        print(json.dumps(ready, indent=2))
        return 0

    return 1


def _dispatch_task(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.task_command == "run":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            task_path = repository_path / ".esc-ai" / "workflows" / "active" / args.task_id / "task.yaml"
            if not task_path.is_file():
                print(f"INVALID    no task.yaml found for `{args.task_id}` in `{repository_id}`")
                suggestions = _task_id_suggestions(repository_path, args.task_id)
                if suggestions:
                    print(f"           did you mean: {', '.join(suggestions)}?")
                return 1
            task_document = load_yaml(task_path)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        provider = active_provider(registry)
        print(render_execution_preview(
            repository_id, args.task_id, task_document, provider,
            resolve_default_policy(registry), prior_consent(store, args.task_id),
        ))
        if not args.yes:
            print("Preview only -- re-run with --yes to actually execute.")
            return 0
        if provider is None:
            print("INCOMPLETE no AI provider connected; run `escape-ai provider auth <name>` first.")
            return 2
        result = execute_task(store, registry, repository_id, repository_path, args.task_id, provider, opencode_server=args.opencode)
        print(render_execution_result(result))
        return 0 if result["status"] == "succeeded" else 1

    if args.task_command == "promote-checkpoint":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            candidate = checkpoint_candidate(store, repository_path, args.task_id)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_checkpoint_candidate(candidate, repository_path))
        if not args.yes:
            print("Preview only -- re-run with --yes to actually promote.")
            return 0
        path = promote_checkpoint(repository_path, args.task_id, candidate)
        print("Merged worktree back; no checkpoint to record." if path is None else f"Promoted checkpoint to {path}")
        return 0

    if args.task_command == "impact":
        try:
            document = analyze_task_impact(store, registry, args.task_id)
        except ValueError as exc:
            print(f"INVALID    {exc}")
            return 1
        print(json.dumps(document, indent=2))
        return 0

    if args.task_command == "doctor":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            task_path = repository_path / ".esc-ai" / "workflows" / "active" / args.task_id / "task.yaml"
            if not task_path.is_file():
                print(f"INVALID    no task.yaml found for `{args.task_id}` in `{repository_id}`")
                suggestions = _task_id_suggestions(repository_path, args.task_id)
                if suggestions:
                    print(f"           did you mean: {', '.join(suggestions)}?")
                return 1
            blockers = doctor_check(repository_path, task_path, registry)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1
        if not blockers:
            print("CLEAN      architecture coverage and verification-gate prerequisites are both satisfied.")
            return 0
        print(f"BLOCKED    {len(blockers)} issue(s) found; a real `task run` would fail before this task even starts:")
        for blocker in blockers:
            print(f"  - {blocker}")
        return 1

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

