from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from esc_orchestrator.application.app import App
from esc_orchestrator.domain.intents import INTENT_WORK_TYPES
from esc_orchestrator.entrypoints.cli.dispatch_system import (
    _dispatch_policy,
    _dispatch_provider,
    _dispatch_repository,
    _dispatch_resume,
    _dispatch_roadmap,
)
from esc_orchestrator.entrypoints.cli.dispatch_workflow import (
    _dispatch_intent,
    _dispatch_plan,
    _dispatch_task,
    _rewrite_legacy_plan_argv,
)
from esc_orchestrator.entrypoints.cli.errors import EXIT_FAILED, report_unexpected
from esc_orchestrator.entrypoints.cli.interactive.menu import run_interactive
from esc_orchestrator.entrypoints.cli.parser import build_parser

# Command name -> handler. One entry per command (PYEP-DISPATCH-01): adding a command adds a row,
# not another branch. Every intent verb shares one handler; the verb selects the procedure.
_HANDLERS = {
    "repository": _dispatch_repository,
    "initiative": _dispatch_plan,
    "task": _dispatch_task,
    "resume": _dispatch_resume,
    "provider": _dispatch_provider,
    "policy": _dispatch_policy,
    "roadmap": _dispatch_roadmap,
    **dict.fromkeys(INTENT_WORK_TYPES, _dispatch_intent),
}


def run(argv: list[str] | None, app_factory: Callable[[Path, Path | None], App]) -> int:
    """Parse `argv`, build the app with `app_factory(db, registry)` and dispatch. The factory is supplied by
    the caller (the composition root's console-script wrapper), so this module never constructs
    infrastructure."""
    args = build_parser().parse_args(_rewrite_legacy_plan_argv(list(sys.argv[1:] if argv is None else argv)))
    app = app_factory(args.db, args.registry)
    try:
        if args.command is None:
            return run_interactive(app)
        handler = _HANDLERS.get(args.command)
        return handler(args, app) if handler else EXIT_FAILED
    except Exception as exc:  # noqa: BLE001 -- top-level handler (PYERR-TRANSLATE-01): known errors were translated by the handler, so this is a bug
        return report_unexpected(exc)
