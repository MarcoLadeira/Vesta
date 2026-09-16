import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vesta.clients import client_integrations_status, detect_stale_paths
from vesta.integrations import (
    activate_project,
    project_status,
    uninstall_vesta,
    update_vesta_source,
)


class ClientDetectionTests(unittest.TestCase):
    def test_global_manifest_write_is_atomic(self):
        from vesta import integrations

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".vesta" / "global.json"
            with mock.patch("vesta.integrations.atomic_write_text") as atomic_write:
                integrations._write(target, "{}\n")

        atomic_write.assert_called_once_with(target, "{}\n")

    def test_global_install_preserves_previously_registered_targets(self):
        from vesta.integrations import install_global_integrations, load_global_status

        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            project, home = Path(ptmp), Path(htmp)
            install_global_integrations(
                project, home=home, targets=["codex"], ensure_superpowers=False
            )
            install_global_integrations(
                project, home=home, targets=["gemini"], ensure_superpowers=False
            )
            status = load_global_status(home)

        self.assertEqual(set(status["targets"]), {"codex", "gemini"})

    def test_activation_makes_all_six_clients_active(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            activate_project(proj, home=home, install_global=True)
            status = client_integrations_status(proj, home)

        self.assertEqual(
            set(status["summary"]["active"]),
            {"claude", "codex", "copilot", "gemini", "cursor", "cline"},
        )
        self.assertEqual(status["summary"]["broken"], [])
        self.assertEqual(status["summary"]["missing"], [])

    def test_cursor_and_cline_rule_files_are_written(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            activate_project(proj, home=home, install_global=False)
            self.assertTrue((proj / ".cursor" / "rules" / "vesta.mdc").exists())
            self.assertTrue((proj / ".clinerules" / "vesta.md").exists())

    def test_gemini_memory_is_written_and_preserves_user_content(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            gemini = proj / "GEMINI.md"
            gemini.write_text("# User Gemini notes\nKeep this.\n", encoding="utf-8")

            activate_project(proj, home=home, install_global=False)
            activate_project(proj, home=home, install_global=False, repair=True)
            text = gemini.read_text(encoding="utf-8")

        self.assertEqual(text.count("Vesta managed block: start"), 1)
        self.assertIn("latest explicit request controls", text.lower())
        self.assertIn("# User Gemini notes", text)
        self.assertIn("Keep this.", text)

    def test_missing_client_reports_missing_with_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            status = client_integrations_status(proj, proj / "home")
        for client in status["clients"]:
            self.assertEqual(client["status"], "missing")
            self.assertEqual(client["repair"], "vesta activate --repair")

    def test_tampered_client_file_reports_broken(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            activate_project(proj, home=home, install_global=True)
            cop = proj / ".github" / "copilot-instructions.md"
            cop.write_text("user content only, no Vesta block", encoding="utf-8")
            status = client_integrations_status(proj, home)
        copilot = next(c for c in status["clients"] if c["id"] == "copilot")
        self.assertEqual(copilot["status"], "broken")
        self.assertIn("copilot", status["summary"]["broken"])

    def test_project_status_includes_client_integrations(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            activate_project(proj, home=home, install_global=False)
            status = project_status(proj, home=home)
        self.assertIn("client_integrations", status)
        self.assertIn("stale_paths", status)


class StalePathTests(unittest.TestCase):
    def test_moved_project_is_detected(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            activate_project(proj, home=home, install_global=False)
            act = proj / ".vestahub" / "activation.json"
            data = json.loads(act.read_text(encoding="utf-8"))
            data["project_root"] = "C:/old/path/that/moved"
            act.write_text(json.dumps(data), encoding="utf-8")
            stale = detect_stale_paths(proj, home)
        self.assertFalse(stale["ok"])
        self.assertIn("moved_project", [i["kind"] for i in stale["issues"]])

    def test_clean_activation_is_not_stale(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            activate_project(proj, home=home, install_global=False)
            stale = detect_stale_paths(proj, home)
        self.assertTrue(stale["ok"])


class UninstallTests(unittest.TestCase):
    def test_dry_run_plans_but_does_not_remove(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            activate_project(proj, home=home, install_global=True)
            result = uninstall_vesta(proj, home=home, dry_run=True)
            self.assertEqual(result["status"], "planned")
            self.assertTrue(result["planned_path_removals"])
            # Nothing actually removed.
            self.assertTrue((home / ".vesta" / "global.json").exists())

    def test_confirm_removes_and_is_idempotent(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            activate_project(proj, home=home, install_global=True)
            first = uninstall_vesta(proj, home=home, dry_run=False)
            self.assertEqual(first["status"], "removed")
            self.assertTrue(first["removed_paths"])
            self.assertFalse((home / ".vesta" / "global.json").exists())
            second = uninstall_vesta(proj, home=home, dry_run=False)
            self.assertEqual(second["removed_paths"], [])

    def test_uninstall_preserves_user_content_outside_blocks(self):
        with (
            tempfile.TemporaryDirectory() as ptmp,
            tempfile.TemporaryDirectory() as htmp,
        ):
            proj, home = Path(ptmp), Path(htmp)
            claude = home / ".claude" / "CLAUDE.md"
            claude.parent.mkdir(parents=True, exist_ok=True)
            claude.write_text("# My personal notes\nkeep me\n", encoding="utf-8")
            activate_project(proj, home=home, install_global=True)
            uninstall_vesta(proj, home=home, dry_run=False)
            remaining = claude.read_text(encoding="utf-8")
        self.assertIn("My personal notes", remaining)
        self.assertNotIn("Vesta managed block", remaining)


class UpdateTests(unittest.TestCase):
    def test_update_reports_missing_source_cleanly(self):
        with tempfile.TemporaryDirectory() as htmp:
            result = update_vesta_source(home=Path(htmp))
        self.assertEqual(result["status"], "missing_source")


if __name__ == "__main__":
    unittest.main()
