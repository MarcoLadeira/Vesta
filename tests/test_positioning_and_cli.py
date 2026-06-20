"""Issue #40: positioning is consistent and every documented command exists."""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from opai.cli import build_parser

REPO = Path(__file__).resolve().parents[1]
POSITIONING = "AI coding cost firewall"


class PositioningConsistencyTests(unittest.TestCase):
    def test_core_surfaces_use_the_same_positioning(self):
        for relative in [
            "README.md",
            "docs/QUICKSTART.md",
            "docs/PROOF.md",
            "docs/LAUNCH_CHECKLIST.md",
        ]:
            text = (REPO / relative).read_text(encoding="utf-8")
            self.assertIn(POSITIONING, text, relative)

    def test_launch_checklist_covers_required_channels(self):
        text = (REPO / "docs" / "LAUNCH_CHECKLIST.md").read_text(encoding="utf-8")
        for channel in ["GitHub", "Product Hunt", "Hacker News", "Reddit", "video"]:
            self.assertIn(channel, text, channel)

    def test_proof_numbers_are_reproducible_claims(self):
        text = (REPO / "docs" / "PROOF.md").read_text(encoding="utf-8")
        self.assertIn("opai savings", text)
        self.assertIn("Reproduce it yourself", text)


class DocumentedCommandsExistTests(unittest.TestCase):
    def _commands(self):
        parser = build_parser()
        for action in parser._subparsers._group_actions:
            if hasattr(action, "choices") and action.choices:
                return set(action.choices.keys())
        return set()

    def test_headline_commands_are_registered(self):
        commands = self._commands()
        for name in [
            "doctor",
            "route",
            "savings",
            "policy",
            "edition",
            "guard",
            "update",
            "uninstall",
            "status",
            "models",
        ]:
            self.assertIn(name, commands, name)

    def test_route_supports_record_flag(self):
        parser = build_parser()
        args = parser.parse_args(["route", "fix bug", "--record"])
        self.assertTrue(args.record)

    def test_savings_runs_and_uses_positioning_help(self):
        # The command must execute without error on an empty project.
        from opai.cli import main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--project", str(REPO), "savings"])
        self.assertEqual(code, 0)
        self.assertIn("opai-savings", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
