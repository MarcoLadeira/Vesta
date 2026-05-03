import tempfile
import unittest
from pathlib import Path

from opai.integrations import (
    activate_project,
    ensure_superpowers_bridge,
    install_global_integrations,
    load_global_status,
    render_statusline,
)


class OPaiIntegrationTests(unittest.TestCase):
    def test_global_integration_writes_discovery_files(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            result = install_global_integrations(
                project,
                home=home,
                targets=["codex", "claude", "copilot", "shell"],
                install_shell_aliases=False,
            )

            self.assertEqual(result["status"], "installed")
            self.assertTrue((home / ".opai" / "status.txt").exists())
            self.assertTrue(
                (home / ".agents" / "skills" / "opai" / "SKILL.md").exists()
            )
            self.assertTrue((home / ".claude" / "CLAUDE.md").exists())
            self.assertTrue(
                (home / ".opai" / "integrations" / "copilot-instructions.md").exists()
            )
            self.assertTrue((home / ".opai" / "bin" / "opai-codex.ps1").exists())

            status = load_global_status(home)
            self.assertEqual(status["brand"], "OPai")
            self.assertEqual(status["status_text"], "Using OPai")

    def test_claude_block_is_idempotent(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            install_global_integrations(project, home=home, targets=["claude"])
            install_global_integrations(project, home=home, targets=["claude"])

            text = (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertEqual(text.count("OPai managed block"), 2)
            self.assertEqual(text.count("Using OPai"), 1)

    def test_statusline_is_right_aligned_when_width_allows(self):
        status = render_statusline(width=24, color=False)
        self.assertEqual(status, "              Using OPai")

    def test_superpowers_bridge_is_enabled_from_codex_install(self):
        with tempfile.TemporaryDirectory() as home_tmp:
            home = Path(home_tmp)
            source = home / ".codex" / "superpowers" / "skills"
            (source / "brainstorming").mkdir(parents=True)
            (source / "brainstorming" / "SKILL.md").write_text(
                "---\nname: brainstorming\n---\n", encoding="utf-8"
            )

            result = ensure_superpowers_bridge(home)

            self.assertTrue(result["enabled"])
            self.assertTrue((home / ".agents" / "skills" / "superpowers").exists())
            self.assertTrue(
                (
                    home
                    / ".agents"
                    / "skills"
                    / "superpowers"
                    / "brainstorming"
                    / "SKILL.md"
                ).exists()
            )

    def test_project_activation_writes_state_and_ai_client_instructions(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)
            source = home / ".codex" / "superpowers" / "skills"
            (source / "using-superpowers").mkdir(parents=True)
            (source / "using-superpowers" / "SKILL.md").write_text(
                "---\nname: using-superpowers\n---\n", encoding="utf-8"
            )

            result = activate_project(project, home=home, install_global=True)

            self.assertEqual(result["status"], "active")
            self.assertTrue((project / ".opaihub" / "project.json").exists())
            self.assertTrue((project / ".opaihub" / "activation.json").exists())
            self.assertTrue((project / "AGENTS.md").exists())
            self.assertTrue((project / "CLAUDE.md").exists())
            self.assertTrue((project / ".github" / "copilot-instructions.md").exists())
            self.assertTrue(
                (home / ".agents" / "skills" / "opai" / "SKILL.md").exists()
            )
            self.assertTrue(result["superpowers"]["enabled"])
            self.assertIn(
                "Superpowers", (project / "AGENTS.md").read_text(encoding="utf-8")
            )

    def test_shell_wrappers_activate_each_project_before_launching(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            install_global_integrations(project, home=home, targets=["shell"])

            wrapper = (home / ".opai" / "bin" / "opai-codex.ps1").read_text(
                encoding="utf-8"
            )
            self.assertIn("python -m opai activate --quiet --project .", wrapper)

    def test_shell_aliases_include_op_brand_command(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            install_global_integrations(
                project,
                home=home,
                targets=["shell"],
                install_shell_aliases=True,
            )

            profile = (
                home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
            )
            text = profile.read_text(encoding="utf-8")
            self.assertIn("function op { python -m opai @args }", text)
            self.assertIn("function opai { python -m opai @args }", text)

    def test_shell_aliases_include_posix_ai_client_wrappers(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            install_global_integrations(
                project,
                home=home,
                targets=["shell"],
                install_shell_aliases=True,
            )

            profile = home / ".profile"
            text = profile.read_text(encoding="utf-8")
            self.assertIn('op() { python -m opai "$@"; }', text)
            self.assertIn('opai() { python -m opai "$@"; }', text)
            self.assertIn("opai-codex", text)


if __name__ == "__main__":
    unittest.main()
