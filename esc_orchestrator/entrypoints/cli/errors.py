"""The CLI's one place that turns errors into output and an exit status (ARCH-PY-ERROR: PYERR-TRANSLATE-01).

Exit status contract -- scripts depend on it, so it is documented here once and nowhere else is a status
invented (PYERR-EXITCODE-01):

    0   success (including "nothing to do", and a preview when `--yes` was not given)
    1   the operation could not be done -- any known error -- or its outcome is a failure
        (a task run that did not succeed, a `task doctor` that found blockers, validation findings)
    2   incomplete: more input is needed first (answers not yet given, no provider connected, a verb whose
        pipeline is not available yet); also argparse's status for invalid usage
    70  an unexpected internal error (a bug); the traceback goes to the log, not the terminal
"""
from __future__ import annotations

import argparse
import functools
import logging
from collections.abc import Callable
from typing import Any

from esc_orchestrator.domain.errors import AppError, IncompleteError, UnavailableError

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INCOMPLETE = 2
EXIT_INTERNAL = 70

# Most specific first: the first entry an error is an instance of wins.
_TRANSLATION: tuple[tuple[type[AppError], str, int], ...] = (
    (IncompleteError, "INCOMPLETE", EXIT_INCOMPLETE),
    (UnavailableError, "UNAVAILABLE", EXIT_INCOMPLETE),
    (AppError, "INVALID", EXIT_FAILED),
)


def render_error(error: AppError) -> tuple[str, int]:
    """The text to show for a known error, and its exit status."""
    for error_type, label, status in _TRANSLATION:
        if isinstance(error, error_type):
            text = f"{label:<10} {error}"
            if error.hint:
                text += f"\n{' ' * 11}{error.hint}"
            return text, status
    raise AssertionError("AppError is the last entry of _TRANSLATION, so every AppError matches")  # pragma: no cover


def report(error: AppError) -> int:
    text, status = render_error(error)
    print(text)
    return status


Handler = Callable[..., int]


def guarded(handler: Handler) -> Handler:
    """Run a command handler, turning any known error it raises into output plus an exit status."""
    @functools.wraps(handler)
    def wrapper(args: argparse.Namespace, *rest: Any) -> int:
        try:
            return handler(args, *rest)
        except AppError as error:
            return report(error)

    return wrapper


def report_unexpected(error: BaseException) -> int:
    """Top-level handler for a bug: log the traceback, show a short message, return the internal status."""
    logger.error("unexpected error", exc_info=error)
    print(f"ERROR      unexpected internal error ({type(error).__name__}: {error}); see the traceback on stderr.")
    return EXIT_INTERNAL
