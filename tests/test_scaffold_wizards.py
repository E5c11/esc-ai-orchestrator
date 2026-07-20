import unittest

from esc_orchestrator.scaffold_wizards import WIZARD_COMMANDS, render_wizard_suggestion


class RenderWizardSuggestionTests(unittest.TestCase):
    def test_includes_reason_and_next_step(self):
        text = render_wizard_suggestion("No supported build system detected under `/tmp/x`.", "Then come back.")
        self.assertIn("No supported build system detected under `/tmp/x`.", text)
        self.assertIn("Then come back.", text)

    def test_lists_every_known_wizard_command(self):
        text = render_wizard_suggestion("reason", "next step")
        for stack, command in WIZARD_COMMANDS.items():
            self.assertIn(stack, text)
            self.assertIn(command, text)

    def test_does_not_claim_escape_ai_scaffolds_anything(self):
        text = render_wizard_suggestion("reason", "next step")
        self.assertIn("doesn't scaffold new projects itself", text)


if __name__ == "__main__":
    unittest.main()
