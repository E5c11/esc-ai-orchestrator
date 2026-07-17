from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from esc_exec.opencode_adapter import OpenCodeAdapter, OpenCodeClient
from esc_exec.yaml_io import write_yaml


class OpenCodeRuntime:
    def __init__(self, server: str, registry: Path):
        self.adapter = OpenCodeAdapter(OpenCodeClient(server), registry)

    def execute(self, contracts: dict[str, Any], output_root: Path) -> Path:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for kind in ("task", "workspace", "adapter", "policy"):
                paths[kind] = root / f"{kind}.yaml"
                write_yaml(paths[kind], contracts[kind])
            return self.adapter.execute(paths["task"], paths["workspace"], paths["adapter"], paths["policy"], output_root)
