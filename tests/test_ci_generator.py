from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opcoding.ci import write_github_workflow
from opcoding.cli import main


class CiGeneratorTests(unittest.TestCase):
    def test_missing_test_command_blocks_without_writing_false_green_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch("opcoding.ci.load_profile", return_value=None),
                mock.patch("opcoding.ci.scan_project", return_value={"commands": {}}),
            ):
                with self.assertRaisesRegex(ValueError, "test command"):
                    write_github_workflow(root)

            self.assertFalse((root / ".github" / "workflows" / "opcoding.yml").exists())

    def test_generated_workflow_is_fail_closed_and_pins_trust_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = {
                "commands": {
                    "test": "python -m unittest discover -s tests",
                    "build": "python -m build",
                }
            }
            with mock.patch("opcoding.ci.load_profile", return_value=profile):
                result = write_github_workflow(root)

            source = Path(result["written"]).read_text(encoding="utf-8")
            self.assertIn("permissions:\n  contents: read", source)
            self.assertIn("concurrency:", source)
            self.assertIn("timeout-minutes: 30", source)
            self.assertIn(
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
                source,
            )
            self.assertIn(
                "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
                source,
            )
            self.assertIn("persist-credentials: false", source)
            self.assertIn("ref: ${{ github.sha }}", source)
            self.assertIn("python-version: '3.13'", source)
            self.assertNotIn("python-version: '3.x'", source)
            self.assertNotIn("No test command detected", source)
            self.assertNotIn("continue-on-error", source)

    def test_cli_returns_nonzero_when_generation_cannot_qualify(self):
        with mock.patch(
            "opcoding.cli.write_github_workflow",
            side_effect=ValueError("No test command detected"),
        ):
            self.assertEqual(main(["ci", ".", "github"]), 2)


if __name__ == "__main__":
    unittest.main()
