"""The composition root (ARCH-PY-COMPOSITION).

The one place that knows which concrete classes exist: it opens the store, chooses the provider runtime, and
hands the assembled `App` to a delivery surface. Only this module and the `escape_ai_cli` facade (the
console-script target, i.e. this program's `__main__`) import it; entrypoints receive an `App` instead
(PYCOMP-ROOT-02), and the import contracts in `.importlinter` enforce that.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.registry import default_registry_path
from esc_orchestrator.application.app import App
from esc_orchestrator.runtime import ClaudeCodeRuntime, CodexRuntime, OpenCodeRuntime
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.store import Store


def resolve_runtime(provider: dict[str, Any], registry: Path, opencode_server: str) -> Any:
    """Choose the runtime for a connected provider: Claude Code and Codex use their subscription CLIs;
    everything else routes through OpenCode."""
    if provider["route"] == "subscription" and provider["id"] == "claude":
        return ClaudeCodeRuntime(registry)
    if provider["route"] == "subscription" and provider["id"] == "openai":
        return CodexRuntime(registry)
    return OpenCodeRuntime(opencode_server, registry)


def build_app(db: Path, registry: Path | None = None) -> App:
    """Assemble an `App`: the SQLite state store at `db`, the registry (default location when omitted), and
    the real scheduler and runtime factories."""
    return App(
        store=Store(db),
        registry=registry or default_registry_path(),
        scheduler_factory=Scheduler,
        runtime_factory=resolve_runtime,
    )
