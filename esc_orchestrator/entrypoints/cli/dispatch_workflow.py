from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from esc_exec.procedures import PROCEDURES
from esc_orchestrator.application.planning import (
    apply_pending_plan,
    draft_intent,
    draft_plan,
    plan_status,
    store_plan_answers,
)
from esc_orchestrator.application.ports import StateStore
from esc_orchestrator.application.repositories import resolve_repository
from esc_orchestrator.application.runs import (
    checkpoint_candidate,
    doctor_task,
    execute_task,
    prepare_task_run,
    promote_checkpoint,
    require_provider,
    task_impact,
    worktree_diff,
)
from esc_orchestrator.domain.errors import UnavailableError
from esc_orchestrator.domain.intents import LEGACY_PLAN_SUBCOMMANDS
from esc_orchestrator.entrypoints.cli.errors import EXIT_FAILED, EXIT_OK, guarded, report
from esc_orchestrator.entrypoints.cli.render import (
    render_checkpoint_candidate,
    render_execution_preview,
    render_execution_result,
    render_plan_draft,
    render_plan_result,
    render_procedure,
    render_status,
)
from esc_orchestrator.initiative import find_ready_tasks


@guarded
def _dispatch_intent(args: argparse.Namespace, store: StateStore, registry: Path) -> int:
    """BLA-42 front door: an intent verb drafts an initiative through the same draft_plan the
    `initiative` group and the interactive menu use. It only selects the procedure; every gate lives in
    PROCEDURES and the execution machinery."""
    verb = args.command
    try:
        initiative_id, draft = draft_intent(store, registry, verb, args.objective, args.repositories, args.initiative_id)
    except UnavailableError as exc:
        status = report(exc)
        print(render_procedure(verb))
        return status
    if args.json:
        print(json.dumps({**draft, "intent": verb, "procedure": [stage.name for stage in PROCEDURES[verb]]}, indent=2))
        return EXIT_OK
    print(render_plan_draft(draft))
    print()
    print(render_procedure(verb))
    print()
    print(f"Next: `escape-ai initiative answer {initiative_id} <answers.json>`, then `escape-ai initiative apply {initiative_id}`.")
    return EXIT_OK


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


@guarded
def _dispatch_plan(args: argparse.Namespace, store: StateStore, registry: Path) -> int:
    if args.plan_command == "draft":
        request = json.loads(args.request_file.read_text(encoding="utf-8"))
        draft = draft_plan(
            store, registry, args.initiative_id, request["work_type"], request["objective"], request["repositories"],
        )
        print(json.dumps(draft, indent=2) if args.json else render_plan_draft(draft))
        return EXIT_OK

    if args.plan_command == "answer":
        store_plan_answers(store, args.initiative_id, json.loads(args.answers_file.read_text(encoding="utf-8")))
        print(f"STORED     answers for `{args.initiative_id}`; run `escape-ai initiative apply {args.initiative_id}` to write them.")
        return EXIT_OK

    if args.plan_command == "apply":
        result, dependency_chain = apply_pending_plan(store, registry, args.initiative_id)
        print(render_plan_result(result, dependency_chain))
        return EXIT_OK

    if args.plan_command == "status":
        info = plan_status(store, args.initiative_id)
        # --json also surfaces the real pending-question array (see plan/done/cli-discoverability.md
        # finding #2) -- omitted from the plain render, which stays a compact summary.
        print(json.dumps(info, indent=2) if args.json else render_status({k: v for k, v in info.items() if k != "questions"}))
        return EXIT_OK

    if args.plan_command == "ready":
        print(json.dumps(find_ready_tasks(store, registry, args.initiative_id), indent=2))
        return EXIT_OK

    return EXIT_FAILED


@guarded
def _dispatch_task(args: argparse.Namespace, store: StateStore, registry: Path) -> int:
    if args.task_command == "run":
        preview = prepare_task_run(store, registry, args.repository, args.task_id)
        print(render_execution_preview(
            preview.repository_id, args.task_id, preview.task_document, preview.provider, preview.policy, preview.consent,
        ))
        if not args.yes:
            print("Preview only -- re-run with --yes to actually execute.")
            return EXIT_OK
        provider = require_provider(preview.provider)
        result = execute_task(
            store, registry, preview.repository_id, preview.repository_path, args.task_id, provider,
            opencode_server=args.opencode,
        )
        print(render_execution_result(result))
        return EXIT_OK if result["status"] == "succeeded" else EXIT_FAILED

    if args.task_command == "promote-checkpoint":
        _, repository_path = resolve_repository(args.repository, registry)
        candidate = checkpoint_candidate(store, repository_path, args.task_id)
        print(render_checkpoint_candidate(candidate, worktree_diff(repository_path, args.task_id)))
        if not args.yes:
            print("Preview only -- re-run with --yes to actually promote.")
            return EXIT_OK
        path = promote_checkpoint(repository_path, args.task_id, candidate)
        print("Merged worktree back; no checkpoint to record." if path is None else f"Promoted checkpoint to {path}")
        return EXIT_OK

    if args.task_command == "impact":
        print(json.dumps(task_impact(store, registry, args.task_id), indent=2))
        return EXIT_OK

    if args.task_command == "doctor":
        _, blockers = doctor_task(registry, args.repository, args.task_id)
        if not blockers:
            print("CLEAN      architecture coverage and verification-gate prerequisites are both satisfied.")
            return EXIT_OK
        print(f"BLOCKED    {len(blockers)} issue(s) found; a real `task run` would fail before this task even starts:")
        for blocker in blockers:
            print(f"  - {blocker}")
        return EXIT_FAILED

    return EXIT_FAILED
