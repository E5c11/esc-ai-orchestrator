"""The application object entrypoints receive (ARCH-PY-COMPOSITION: PYCOMP-ROOT-02).

`App` bundles what a delivery surface needs from below -- the state store, the registry location, and the
factories for the two pieces of infrastructure an operation has to build (a scheduler and a provider
runtime) -- so entrypoints never construct or import infrastructure. It is built in one place,
`esc_orchestrator.composition.build_app`, and passed down as an ordinary argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esc_orchestrator.application.ports import RuntimeFactory, SchedulerFactory, StateStore
from esc_orchestrator.application.providers import DEFAULT_OPENCODE_SERVER
from esc_orchestrator.application.runs import execute_task


@dataclass(frozen=True)
class App:
    store: StateStore
    registry: Path
    scheduler_factory: SchedulerFactory
    runtime_factory: RuntimeFactory

    def execute_task(
        self, repository_id: str, repository_path: Path, task_id: str, provider: dict[str, Any],
        runtime: Any = None, opencode_server: str = DEFAULT_OPENCODE_SERVER,
    ) -> dict[str, Any]:
        """Run an approved task through the scheduler this app was composed with."""
        return execute_task(
            self.store, self.registry, repository_id, repository_path, task_id, provider, runtime, opencode_server,
            scheduler_factory=self.scheduler_factory, runtime_factory=self.runtime_factory,
        )
