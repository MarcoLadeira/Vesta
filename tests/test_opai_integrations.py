import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from opai.cli import discover_project_root
from opai.integrations import (
    activate_project,
    ensure_superpowers_bridge,
    install_global_integrations,
    load_global_status,
    project_status,
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

    def test_project_activation_prepends_opai_block_to_existing_instructions(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)
            existing = (
                "# Existing Project Instructions\n\nKeep this project-specific note."
            )
            (project / "AGENTS.md").write_text(existing, encoding="utf-8")

            activate_project(project, home=home, install_global=False)

            text = (project / "AGENTS.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("<!-- OPai managed block: start -->"))
            self.assertIn(existing, text)
            self.assertLess(
                text.index("OPai Project Active"),
                text.index("# Existing Project Instructions"),
            )

    def test_global_opai_skill_is_project_neutral(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            install_global_integrations(project, home=home, targets=["codex"])

            text = (home / ".agents" / "skills" / "opai" / "SKILL.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(str(project), text)
            self.assertIn("Use the current working directory", text)

    def test_project_status_reports_opai_block_position(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            activate_project(project, home=home, install_global=False)

            status = project_status(project, home=home)
            agents = status["project"]["instructions"]["agents"]
            self.assertTrue(agents["opai_block"])
            self.assertTrue(agents["opai_block_at_top"])
            self.assertTrue(agents["superpowers_reference"])

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
            self.assertIn("-m opai activate --quiet --project .", wrapper)
            self.assertIn("degraded mode", wrapper)

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
            self.assertIn("function op { & ", text)
            self.assertIn("-m opai @args", text)
            self.assertIn("function opai { & ", text)

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
            self.assertIn("op() { ", text)
            self.assertIn('-m opai "$@"; }', text)
            self.assertIn("opai() { ", text)
            self.assertIn("opai-codex", text)

    def test_project_root_detection_walks_up_from_nested_directory(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project = Path(project_tmp)
            nested = project / "src" / "app"
            nested.mkdir(parents=True)
            (project / "package.json").write_text("{}", encoding="utf-8")

            self.assertEqual(discover_project_root(nested), project.resolve())

    def test_route_command_is_read_only_by_default(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project = Path(project_tmp)
            (project / "package.json").write_text("{}", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "opai",
                    "--project",
                    str(project),
                    "route",
                    "fix a bug",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse((project / ".opaihub").exists())


if __name__ == "__main__":
    unittest.main()
