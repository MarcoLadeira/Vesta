"""Blocks written before the rebrand to Vesta upgrade in place.

Vesta writes managed blocks into files it does not own -- a user's global
CLAUDE.md, a project's AGENTS.md, shell profiles, AI ignore files -- and finds
them again by their markers. Those markers used to say "OPai". An install made
before the rebrand must be recognised, replaced and uninstalled exactly as a
current one is: never left behind with a second, Vesta-marked block appended
after it, and never reported as missing.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vesta.clients import client_integrations_status
from vesta.context_slim import write_ai_ignore_files
from vesta.integrations import (
    activate_project,
    install_global_integrations,
    project_status,
    uninstall_vesta,
)
from vestahub.context_engine import generate_client_ignores

LEGACY_BLOCK = (
    "<!-- OPai managed block: start -->\n"
    "OPai is installed. Old instructions.\n"
    "<!-- OPai managed block: end -->"
)


class LegacyManagedBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project = tempfile.TemporaryDirectory()
        self._home = tempfile.TemporaryDirectory()
        self.project = Path(self._project.name)
        self.home = Path(self._home.name)

    def tearDown(self) -> None:
        self._project.cleanup()
        self._home.cleanup()

    def test_global_claude_memory_replaces_the_old_block_instead_of_adding_one(self):
        claude = self.home / ".claude" / "CLAUDE.md"
        claude.parent.mkdir(parents=True)
        claude.write_text(LEGACY_BLOCK + "\n\n# My own notes\n", encoding="utf-8")

        install_global_integrations(self.project, home=self.home, targets=["claude"])

        text = claude.read_text(encoding="utf-8")
        self.assertEqual(text.count("<!-- Vesta managed block: start -->"), 1)
        self.assertEqual(text.count("<!-- Vesta managed block: end -->"), 1)
        self.assertNotIn("OPai managed block", text)
        self.assertNotIn("Old instructions", text)
        self.assertIn("# My own notes", text)
        self.assertTrue(text.startswith("<!-- Vesta managed block: start -->"))

    def test_project_instruction_files_replace_the_old_block_in_place(self):
        agents = self.project / "AGENTS.md"
        agents.write_text(
            "# Team rules\n\n" + LEGACY_BLOCK + "\n\n# More\n", encoding="utf-8"
        )

        activate_project(self.project, home=self.home, install_global=False)

        text = agents.read_text(encoding="utf-8")
        self.assertEqual(text.count("Vesta managed block: start"), 1)
        self.assertNotIn("OPai managed block", text)
        self.assertIn("# Team rules", text)
        self.assertIn("# More", text)

    def test_an_old_block_still_counts_as_installed_before_it_is_upgraded(self):
        (self.project / "CLAUDE.md").write_text(LEGACY_BLOCK + "\n", encoding="utf-8")
        (self.project / "AGENTS.md").write_text(LEGACY_BLOCK + "\n", encoding="utf-8")

        clients = {
            client["id"]: client
            for client in client_integrations_status(self.project, self.home)["clients"]
        }
        for client_id in ("claude", "codex"):
            # Recognised as managed; the only thing left to repair is the global
            # discovery file this bare test home does not have.
            self.assertEqual(
                clients[client_id]["reason"],
                "Project file is managed but global discovery file is missing.",
            )

        status = project_status(self.project, home=self.home)
        for name in ("agents", "claude"):
            instructions = status["project"]["instructions"][name]
            self.assertTrue(instructions["vesta_block"], name)
            self.assertTrue(instructions["vesta_block_at_top"], name)

    def test_uninstall_removes_an_old_block_and_keeps_user_content(self):
        claude = self.home / ".claude" / "CLAUDE.md"
        claude.parent.mkdir(parents=True)
        claude.write_text(LEGACY_BLOCK + "\n\n# My own notes\n", encoding="utf-8")

        planned = uninstall_vesta(self.project, home=self.home, dry_run=True)
        self.assertIn(str(claude), planned["planned_block_strips"])

        uninstall_vesta(self.project, home=self.home, dry_run=False)
        text = claude.read_text(encoding="utf-8")
        self.assertNotIn("managed block", text)
        self.assertIn("# My own notes", text)

    def test_shell_profiles_replace_the_old_block_and_add_the_vesta_command(self):
        profile = self.home / ".bashrc"
        profile.write_text(
            "export EDITOR=vim\n\n# OPai managed block: start\nopai() { old; }\n# OPai managed block: end\n",
            encoding="utf-8",
        )

        install_global_integrations(
            self.project, home=self.home, targets=["shell"], install_shell_aliases=True
        )

        text = profile.read_text(encoding="utf-8")
        self.assertEqual(text.count("# Vesta managed block: start"), 1)
        self.assertNotIn("OPai managed block", text)
        self.assertNotIn("old;", text)
        self.assertIn("vesta() {", text)
        self.assertIn("export EDITOR=vim", text)


class LegacyIgnoreRulesTests(unittest.TestCase):
    def test_client_ignore_blocks_are_renamed_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".cursorignore").write_text(
                "# mine\nsecrets/\n\n# OPai context-slimming rules (managed)\n.git/\n# end OPai rules\n",
                encoding="utf-8",
            )

            result = generate_client_ignores(root, ["cursor"])
            text = (root / ".cursorignore").read_text(encoding="utf-8")

        self.assertEqual(result["results"][0]["status"], "already_managed")
        self.assertEqual(text.count("# Vesta context-slimming rules (managed)"), 1)
        self.assertEqual(text.count("# end Vesta rules"), 1)
        self.assertNotIn("Vesta", text)
        self.assertIn("secrets/", text)

    def test_ai_ignore_files_rename_the_old_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claudeignore").write_text(
                "custom-cache/\n\n# OPai context-slimming rules\n.git/\n",
                encoding="utf-8",
            )

            write_ai_ignore_files(root)
            text = (root / ".claudeignore").read_text(encoding="utf-8")

        self.assertEqual(text.count("# Vesta context-slimming rules"), 1)
        self.assertNotIn("Vesta", text)
        self.assertIn("custom-cache/", text)
        self.assertEqual(text.count(".git/"), 1)


if __name__ == "__main__":
    unittest.main()
