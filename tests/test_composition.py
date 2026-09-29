"""The composition root and the App it builds (ARCH-PY-COMPOSITION)."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from esc_orchestrator.application.app import App
from esc_orchestrator.composition import build_app, resolve_runtime
from esc_orchestrator.runtime import ClaudeCodeRuntime, CodexRuntime, OpenCodeRuntime
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.store import Store


class BuildAppTests(unittest.TestCase):
    def test_builds_the_real_store_and_factories(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.yaml"
            app = build_app(Path(temp) / "db.sqlite", registry)
        self.assertIsInstance(app, App)
        self.assertIsInstance(app.store, Store)
        self.assertEqual(registry, app.registry)
        self.assertIs(Scheduler, app.scheduler_factory)
        self.assertIs(resolve_runtime, app.runtime_factory)

    def test_the_registry_defaults_when_omitted(self):
        with TemporaryDirectory() as temp:
            with patch("esc_orchestrator.composition.default_registry_path", return_value=Path("/x/registry.yaml")):
                app = build_app(Path(temp) / "db.sqlite")
        self.assertEqual(Path("/x/registry.yaml"), app.registry)


class ResolveRuntimeTests(unittest.TestCase):
    def test_chooses_the_runtime_for_each_provider_route(self):
        registry = Path("registry.yaml")
        cases = [
            ({"id": "claude", "route": "subscription"}, ClaudeCodeRuntime),
            ({"id": "openai", "route": "subscription"}, CodexRuntime),
            ({"id": "claude", "route": "api-key"}, OpenCodeRuntime),
            ({"id": "anything", "route": "api-key"}, OpenCodeRuntime),
        ]
        for provider, expected in cases:
            with self.subTest(provider=provider):
                self.assertIsInstance(resolve_runtime(provider, registry, "http://127.0.0.1:1"), expected)


class AppSeamTests(unittest.TestCase):
    def test_execute_task_hands_the_composed_factories_to_the_operation(self):
        """The operation builds no infrastructure of its own: whatever the App was composed with is what it uses."""
        def scheduler_factory(store, runtime, registry):
            raise AssertionError("not called: execute_task is patched")

        def runtime_factory(provider, registry, opencode_server):
            raise AssertionError("not called: execute_task is patched")

        app = App(store=object(), registry=Path("r.yaml"), scheduler_factory=scheduler_factory, runtime_factory=runtime_factory)
        with patch("esc_orchestrator.application.app.execute_task", return_value={"status": "succeeded"}) as operation:
            result = app.execute_task("repo", Path("/repo"), "task-1", {"id": "claude", "route": "subscription"})
        self.assertEqual({"status": "succeeded"}, result)
        kwargs = operation.call_args.kwargs
        self.assertIs(scheduler_factory, kwargs["scheduler_factory"])
        self.assertIs(runtime_factory, kwargs["runtime_factory"])
        self.assertIs(app.store, operation.call_args.args[0])

    def test_app_is_immutable(self):
        app = App(store=object(), registry=Path("r"), scheduler_factory=lambda *a: None, runtime_factory=lambda *a: None)
        with self.assertRaises(AttributeError):
            app.registry = Path("other")


if __name__ == "__main__":
    unittest.main()
