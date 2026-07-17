from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from esc_exec.opencode_adapter import OpenCodeAdapter, OpenCodeClient
from esc_exec.registry import resolve_route
from esc_exec.task_context import build_verification_plan
from esc_exec.json_io import write_json
from esc_exec.yaml_io import write_yaml


class OpenCodeRuntime:
    def __init__(self, server: str, registry: Path):
        self.adapter = OpenCodeAdapter(OpenCodeClient(server), registry)
        self.registry = registry

    def execute(self, contracts: dict[str, Any], output_root: Path) -> Path:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for kind in ("task", "workspace", "adapter", "policy"):
                paths[kind] = root / f"{kind}.yaml"
                write_yaml(paths[kind], contracts[kind])
            repository = resolve_route(
                self.registry, "repositories", contracts["task"]["task"]["repository"]
            )
            plan = build_verification_plan(repository, paths["task"], root / "verification-plan.json")
            run_dir = self.adapter.execute(
                paths["task"], paths["workspace"], paths["adapter"], paths["policy"], output_root
            )
            write_json(run_dir / "verification-plan.json", plan)
            return run_dir
