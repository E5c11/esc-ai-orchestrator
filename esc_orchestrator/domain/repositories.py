from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositoryLocation:
    """Where a registered repository resolves to, or why it does not (input to the pure
    `render_repository_list`)."""
    id: str
    path: Path | None
    error: str | None
