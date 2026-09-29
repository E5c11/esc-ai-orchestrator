from __future__ import annotations

import sys
from esc_exec.registry import default_registry_path
from esc_orchestrator.store import Store
from esc_orchestrator.domain.intents import INTENT_WORK_TYPES
from esc_orchestrator.domain.intents import INTENT_WORK_TYPES
from esc_orchestrator.domain.intents import INTENT_WORK_TYPES
from esc_orchestrator.entrypoints.cli.interactive.menu import run_interactive

from esc_orchestrator.domain.intents import INTENT_WORK_TYPES
from esc_orchestrator.entrypoints.cli.dispatch import (
    _dispatch_intent,
    _dispatch_plan,
    _dispatch_policy,
    _dispatch_provider,
    _dispatch_repository,
    _dispatch_resume,
    _dispatch_roadmap,
    _dispatch_task,
    _rewrite_legacy_plan_argv,
)
from esc_orchestrator.entrypoints.cli.interactive.menu import run_interactive
from esc_orchestrator.entrypoints.cli.parser import build_parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(_rewrite_legacy_plan_argv(list(sys.argv[1:] if argv is None else argv)))
    registry = args.registry or default_registry_path()
    store = Store(args.db)
    if args.command is None:
        return run_interactive(store, registry)
    if args.command == "repository":
        return _dispatch_repository(args, store, registry)
    if args.command in INTENT_WORK_TYPES:
        return _dispatch_intent(args, store, registry)
    if args.command == "initiative":
        return _dispatch_plan(args, store, registry)
    if args.command == "task":
        return _dispatch_task(args, store, registry)
    if args.command == "resume":
        return _dispatch_resume(args, store, registry)
    if args.command == "provider":
        return _dispatch_provider(args, store, registry)
    if args.command == "policy":
        return _dispatch_policy(args, store, registry)
    if args.command == "roadmap":
        return _dispatch_roadmap(args, store, registry)
    return 1

