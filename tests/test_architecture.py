"""Architecture fitness checks (PYTEST-ARCH-01): the layer contracts run as part of the suite."""
import inspect
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from esc_orchestrator.application.ports import StateStore
from esc_orchestrator.store import Store

ROOT = Path(__file__).resolve().parent.parent


class ArchitectureTests(unittest.TestCase):
    def test_import_contracts_hold(self):
        lint_imports = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
        if not Path(lint_imports).exists():
            self.skipTest("import-linter is not installed")
        result = subprocess.run([lint_imports], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_concrete_store_satisfies_the_state_store_port(self):
        for name, port_method in inspect.getmembers(StateStore, inspect.isfunction):
            if name.startswith("_"):
                continue  # Protocol machinery, not part of the port
            with self.subTest(method=name):
                self.assertTrue(hasattr(Store, name), f"Store lacks {name}")
                self.assertEqual(
                    list(inspect.signature(port_method).parameters),
                    list(inspect.signature(getattr(Store, name)).parameters),
                )


if __name__ == "__main__":
    unittest.main()
