"""Known errors, their translation to CLI output, and the unexpected-error handler (ARCH-PY-ERROR)."""
import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from esc_orchestrator import escape_ai_cli as cli
from esc_orchestrator.application.errors import translates_engine_errors
from esc_orchestrator.domain.errors import (
    AppError,
    ConflictError,
    IncompleteError,
    InvalidInputError,
    NotFoundError,
    UnavailableError,
    UnsupportedRepositoryError,
)
from esc_orchestrator.entrypoints.cli import main as cli_main
from esc_orchestrator.entrypoints.cli.errors import (
    EXIT_FAILED,
    EXIT_INCOMPLETE,
    EXIT_INTERNAL,
    guarded,
    render_error,
)


class ErrorHierarchyTests(unittest.TestCase):
    def test_every_known_error_derives_from_app_error(self):
        for error_type in (
            NotFoundError, InvalidInputError, UnsupportedRepositoryError, ConflictError, IncompleteError, UnavailableError,
        ):
            with self.subTest(error=error_type.__name__):
                self.assertTrue(issubclass(error_type, AppError))

    def test_unsupported_repository_is_a_kind_of_invalid_input(self):
        self.assertTrue(issubclass(UnsupportedRepositoryError, InvalidInputError))


class TranslationTests(unittest.TestCase):
    def test_each_error_maps_to_one_label_and_status(self):
        cases = [
            (NotFoundError("no such thing"), "INVALID", EXIT_FAILED),
            (InvalidInputError("bad"), "INVALID", EXIT_FAILED),
            (ConflictError("exists"), "INVALID", EXIT_FAILED),
            (IncompleteError("more input needed"), "INCOMPLETE", EXIT_INCOMPLETE),
            (UnavailableError("not yet"), "UNAVAILABLE", EXIT_INCOMPLETE),
        ]
        for error, label, status in cases:
            with self.subTest(error=type(error).__name__):
                text, got_status = render_error(error)
                self.assertTrue(text.startswith(label), text)
                self.assertIn(str(error), text)
                self.assertEqual(status, got_status)

    def test_the_status_column_is_aligned_for_every_label(self):
        for error in (InvalidInputError("m"), IncompleteError("m")):
            text, _ = render_error(error)
            self.assertEqual("m", text.split(" ", 1)[1].lstrip())

    def test_a_hint_is_shown_on_its_own_indented_line(self):
        text, _ = render_error(NotFoundError("no task.yaml", hint="did you mean: a-b?"))
        first, second = text.splitlines()
        self.assertTrue(first.startswith("INVALID"))
        self.assertEqual("did you mean: a-b?", second.strip())
        self.assertTrue(second.startswith(" " * 11))

    def test_guarded_turns_a_known_error_into_output_and_a_status(self):
        @guarded
        def handler(args):
            raise IncompleteError("answers missing")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            status = handler(argparse.Namespace())
        self.assertEqual(EXIT_INCOMPLETE, status)
        self.assertIn("INCOMPLETE answers missing", buffer.getvalue())

    def test_guarded_leaves_unexpected_errors_alone(self):
        @guarded
        def handler(args):
            raise KeyError("a bug")

        with self.assertRaises(KeyError):
            handler(argparse.Namespace())


class EngineErrorTranslationTests(unittest.TestCase):
    def _raising(self, exception):
        @translates_engine_errors
        def operation():
            raise exception

        return operation

    def test_missing_things_become_not_found_and_keep_the_cause(self):
        for exception in (KeyError("repo"), FileNotFoundError("gone")):
            with self.subTest(exception=type(exception).__name__), self.assertRaises(NotFoundError) as caught:
                self._raising(exception)()
            self.assertIs(exception, caught.exception.__cause__)

    def test_a_key_error_message_is_not_wrapped_in_its_repr_quotes(self):
        with self.assertRaises(NotFoundError) as caught:
            self._raising(KeyError("Repository route `ghost` is not registered."))()
        self.assertEqual("Repository route `ghost` is not registered.", str(caught.exception))

    def test_bad_values_and_os_errors_become_invalid_input(self):
        for exception in (ValueError("bad"), PermissionError("denied")):
            with self.subTest(exception=type(exception).__name__), self.assertRaises(InvalidInputError):
                self._raising(exception)()

    def test_known_errors_pass_through_unchanged(self):
        error = ConflictError("exists")
        with self.assertRaises(ConflictError) as caught:
            self._raising(error)()
        self.assertIs(error, caught.exception)

    def test_a_bug_is_not_swallowed_into_a_known_error(self):
        with self.assertRaises(RuntimeError):
            self._raising(RuntimeError("bug"))()


class UnexpectedErrorTests(unittest.TestCase):
    def test_a_bug_in_a_handler_exits_with_the_internal_status_and_does_not_look_like_bad_input(self):
        def boom(args, app):
            raise RuntimeError("kaboom")

        buffer = io.StringIO()
        with (
            patch.dict(cli_main._HANDLERS, {"resume": boom}),
            redirect_stdout(buffer),
            self.assertLogs("esc_orchestrator.entrypoints.cli.errors", level="ERROR") as logs,
        ):
            status = cli.main(["--db", ":memory:", "--registry", "/nonexistent/registry.yaml", "resume"])
        self.assertEqual(EXIT_INTERNAL, status)
        self.assertIn("ERROR", buffer.getvalue())
        self.assertNotIn("INVALID", buffer.getvalue())
        self.assertIn("kaboom", logs.output[0])


if __name__ == "__main__":
    unittest.main()
