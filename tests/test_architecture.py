"""Architecture fitness checks (PYTEST-ARCH-01): the layer contracts run as part of the suite."""
import inspect
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from esc_orchestrator.application.ports import StateStore
from esc_orchestrator.scheduler import _write_checkpoint_candidate
from esc_orchestrator.store import Store

ROOT = Path(__file__).resolve().parent.parent


class ArchitectureTests(unittest.TestCase):
    def test_import_contracts_hold(self):
        lint_imports = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
        if not Path(lint_imports).exists():
            self.skipTest("import-linter is not installed")
        result = subprocess.run([lint_imports], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_ruff_architecture_rules_hold(self):
        ruff = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff"))
        if not Path(ruff).exists():
            self.skipTest("ruff is not installed")
        result = subprocess.run([ruff, "check", "esc_orchestrator"], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
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


class SwallowedErrorTests(unittest.TestCase):
    """PYERR-SWALLOW-01: a best-effort step may not raise, but it must not vanish silently."""

    def test_failed_checkpoint_candidate_write_is_logged_not_raised(self):
        class BrokenStore:
            def contracts(self, task_id):
                raise RuntimeError("store exploded")

        with TemporaryDirectory() as temp, self.assertLogs("esc_orchestrator.scheduler", level="WARNING") as logs:
            _write_checkpoint_candidate(BrokenStore(), "t1", "run-1", Path(temp), ["blocked"])
        self.assertIn("run-1", logs.output[0])
        self.assertIn("store exploded", logs.output[0])


if __name__ == "__main__":
    unittest.main()
