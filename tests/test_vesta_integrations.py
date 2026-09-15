import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from vesta.cli import discover_project_root
from vesta.integrations import (
    activate_project,
    ensure_superpowers_bridge,
    instruction_text,
    install_global_integrations,
    load_global_status,
    project_instruction_text,
    project_status,
    render_statusline,
)


class VestaIntegrationTests(unittest.TestCase):
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
                targets=["codex", "claude", "copilot", "gemini", "shell"],
                install_shell_aliases=False,
            )

            self.assertEqual(result["status"], "installed")
            self.assertTrue((home / ".vesta" / "status.txt").exists())
            self.assertTrue(
                (home / ".agents" / "skills" / "vesta" / "SKILL.md").exists()
            )
            self.assertTrue((home / ".claude" / "CLAUDE.md").exists())
            self.assertTrue(
                (home / ".vesta" / "integrations" / "copilot-instructions.md").exists()
            )
            self.assertTrue((home / ".vesta" / "bin" / "vesta-codex.ps1").exists())
            self.assertTrue((home / ".vesta" / "bin" / "vesta-gemini.ps1").exists())
            self.assertTrue(
                (home / ".vesta" / "integrations" / "gemini-instructions.md").exists()
            )

            status = load_global_status(home)
            self.assertEqual(status["brand"], "Vesta")
            self.assertEqual(status["status_text"], "Using Vesta")

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
            self.assertEqual(text.count("Vesta managed block"), 2)
            self.assertEqual(text.count("Using Vesta"), 1)

    def test_managed_instructions_are_token_tiny(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project = Path(project_tmp)

            # The autonomy contract adds one compact paragraph while remaining
            # tiny enough for every client instruction context.
            self.assertLess(len(project_instruction_text(project)), 850)
            self.assertLess(len(instruction_text(project)), 950)
            self.assertIn("No generated dirs", project_instruction_text(project))
            self.assertNotIn(str(project), project_instruction_text(project))

    def test_statusline_is_right_aligned_when_width_allows(self):
        status = render_statusline(width=24, color=False)
        self.assertEqual(status, "             Using Vesta")

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
            self.assertTrue((project / ".vestahub" / "project.json").exists())
            self.assertTrue((project / ".vestahub" / "activation.json").exists())
            self.assertTrue((project / "AGENTS.md").exists())
            self.assertTrue((project / "CLAUDE.md").exists())
            self.assertTrue((project / "GEMINI.md").exists())
            self.assertTrue((project / ".claudeignore").exists())
            self.assertTrue((project / ".vestaignore").exists())
            self.assertTrue((project / ".github" / "copilot-instructions.md").exists())
            self.assertTrue(
                (home / ".agents" / "skills" / "vesta" / "SKILL.md").exists()
            )
            self.assertTrue(result["superpowers"]["enabled"])
            self.assertIn(
                "Superpowers", (project / "AGENTS.md").read_text(encoding="utf-8")
            )
            self.assertIn(
                ".opcoding-tools/",
                (project / ".claudeignore").read_text(encoding="utf-8"),
            )

    def test_project_activation_prepends_vesta_block_to_existing_instructions(self):
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
            self.assertTrue(text.startswith("<!-- Vesta managed block: start -->"))
            self.assertIn(existing, text)
            self.assertLess(
                text.index("Vesta Active"),
                text.index("# Existing Project Instructions"),
            )

    def test_global_vesta_skill_is_project_neutral(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            install_global_integrations(project, home=home, targets=["codex"])

            text = (home / ".agents" / "skills" / "vesta" / "SKILL.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(str(project), text)
            self.assertIn("Root: cwd", text)

    def test_global_vesta_integration_installs_vesta_skill_library(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            result = install_global_integrations(project, home=home, targets=["codex"])

            vesta_skill_root = home / ".agents" / "skills" / "vesta"
            self.assertTrue((vesta_skill_root / "SKILL.md").exists())
            self.assertTrue((vesta_skill_root / "registry.yaml").exists())
            self.assertTrue((vesta_skill_root / "model-selection" / "SKILL.md").exists())
            self.assertTrue((vesta_skill_root / "security-audit" / "SKILL.md").exists())
            self.assertGreaterEqual(result["vesta_skills"]["count"], 25)
            self.assertEqual(result["vesta_skills"]["missing"], [])

    def test_project_status_reports_vesta_block_position(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            activate_project(project, home=home, install_global=False)

            status = project_status(project, home=home)
            agents = status["project"]["instructions"]["agents"]
            self.assertTrue(agents["vesta_block"])
            self.assertTrue(agents["vesta_block_at_top"])
            self.assertTrue(agents["superpowers_reference"])

    def test_shell_wrappers_activate_each_project_before_launching(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)

            install_global_integrations(project, home=home, targets=["shell"])

            wrapper = (home / ".vesta" / "bin" / "vesta-codex.ps1").read_text(
                encoding="utf-8"
            )
            self.assertIn("-m vesta activate --quiet --project .", wrapper)
            self.assertIn("degraded mode", wrapper)
            self.assertIn("-m vesta statusline", wrapper)
            self.assertIn("-m vesta agent-launch --project . codex", wrapper)
            self.assertIn("$PassthroughExit = 125", wrapper)
            self.assertIn("[Console]::Error.WriteLine", wrapper)
            self.assertIn("VESTA_WELCOME", wrapper)
            self.assertNotIn("welcome --compact --animate", wrapper)

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
            self.assertIn("-m vesta @args", text)

    def test_repair_preserves_shell_alias_manifest_state(self):
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
            install_global_integrations(
                project,
                home=home,
                targets=["shell"],
                install_shell_aliases=False,
            )

            self.assertTrue(load_global_status(home)["shell_aliases_installed"])

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
            self.assertIn('-m vesta "$@"; }', text)
            self.assertIn("vesta-codex", text)
            self.assertIn("vesta-gemini", text)

    def test_posix_wrappers_delegate_to_capture_adapter_then_exec_raw(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)
            install_global_integrations(project, home=home, targets=["shell"])

            for agent in ("claude", "codex", "copilot", "gemini"):
                wrapper = (home / ".vesta" / "bin" / f"vesta-{agent}").read_text(
                    encoding="utf-8"
                )
                self.assertIn(f"-m vesta agent-launch --project . {agent}", wrapper)
                self.assertIn("PASSTHROUGH_EXIT=125", wrapper)
                self.assertIn('exec "$COMMAND" "$@"', wrapper)
                self.assertIn("-m vesta statusline >&2", wrapper)

    def test_wrapper_status_distinguishes_selective_capture_from_legacy(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)
            install_global_integrations(project, home=home, targets=["shell"])

            status = project_status(project, home=home)
            self.assertEqual(
                status["global"]["wrappers"]["codex"]["capture_mode"],
                "selective_proxy",
            )

            wrapper = home / ".vesta" / "bin" / "vesta-codex.ps1"
            wrapper.write_text("& codex @Args\n", encoding="utf-8")
            legacy = project_status(project, home=home)
            self.assertEqual(
                legacy["global"]["wrappers"]["codex"]["capture_mode"],
                "legacy_passthrough",
            )

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
                    "vesta",
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
            self.assertFalse((project / ".vestahub").exists())


if __name__ == "__main__":
    unittest.main()
