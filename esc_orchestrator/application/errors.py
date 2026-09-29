"""Translate the engine's exceptions into `AppError` at the application boundary (PYERR-BOUNDARY-01).

`esc_exec` reports expected failures as `KeyError` / `FileNotFoundError` / `ValueError` / `OSError`. Left alone,
those would reach an entrypoint indistinguishable from a bug. Decorating an application operation with
`translates_engine_errors` turns them into the matching `AppError` (chaining the original with `from`), so
everything an entrypoint sees is either a known `AppError` or a genuine unexpected error.
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from esc_orchestrator.domain.errors import AppError, InvalidInputError, NotFoundError

F = TypeVar("F", bound=Callable[..., Any])


def translates_engine_errors(function: F) -> F:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except AppError:
            raise
        except (KeyError, FileNotFoundError) as exc:
            raise NotFoundError(str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise InvalidInputError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]
