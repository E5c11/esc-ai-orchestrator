"""Known errors (ARCH-PY-ERROR: PYERR-BASE-01).

Every failure the program *expects* -- something the caller can act on -- derives from `AppError`, grouped
by cause so a delivery surface can translate it without knowing individual subclasses. A failure that is a
normal result of an operation (a gate failing, findings being reported) is returned as a value, not raised,
and a bug is left to propagate as an ordinary exception.

`hint` is an optional second line of guidance (for example "did you mean: ..."); surfaces render it after the
message.
"""
from __future__ import annotations


class AppError(Exception):
    """Base class for known errors."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class NotFoundError(AppError):
    """A named thing does not exist: a repository, task, plan draft, checkpoint, proposal."""


class InvalidInputError(AppError):
    """The request is malformed, unsupported, or refers to something that cannot be used."""


class UnsupportedRepositoryError(InvalidInputError):
    """No supported build system was detected for a repository."""


class ConflictError(AppError):
    """The request would duplicate or overwrite something that already exists."""


class IncompleteError(AppError):
    """More input is needed before this can proceed (answers not yet given, no provider connected)."""


class UnavailableError(AppError):
    """The capability is not available (yet)."""
