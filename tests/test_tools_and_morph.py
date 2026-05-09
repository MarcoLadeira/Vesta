import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opcoding.free_tools import run_tool, tools_doctor, tools_root
from opcoding.morph import build_morph_payload, morph_apply_snippet


class ToolsAndMorphTests(unittest.TestCase):
    def test_tools_doctor_reports_install_root(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as cache,
        ):
            root = Path(tmp)
            with patch.dict("os.environ", {"LOCALAPPDATA": cache}):
                doctor = tools_doctor(root)
            self.assertIn("tool-cache", doctor["root"])
            self.assertFalse(Path(doctor["root"]).is_relative_to(root.resolve()))
            self.assertIn("ruff", doctor["installed"])
            self.assertFalse(doctor["legacy_project_root_exists"])

    def test_tools_root_defaults_outside_project_context(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as cache,
        ):
            root = Path(tmp)
            with patch.dict("os.environ", {"LOCALAPPDATA": cache}):
                path = tools_root(root)

            self.assertTrue(path.exists())
            self.assertFalse(path.is_relative_to(root.resolve()))
            self.assertTrue(path.is_relative_to(Path(cache).resolve()))

    def test_pip_audit_is_a_registered_runnable_tool(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as cache,
        ):
            root = Path(tmp)
            with patch.dict("os.environ", {"LOCALAPPDATA": cache}):
                result = run_tool(root, "pip-audit", timeout=1)

        self.assertEqual(result["tool"], "pip-audit")
        self.assertIn("command", result)
        self.assertIn(".", result["command"])
        self.assertIn("--skip-editable", result["command"])
        self.assertNotIn("unknown tool", result.get("error", ""))

    def test_actionlint_uses_concrete_workflow_files(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as cache,
        ):
            root = Path(tmp)
            workflow = root / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: ci\non: [push]\njobs: {}\n", encoding="utf-8")

            with patch.dict("os.environ", {"LOCALAPPDATA": cache}):
                result = run_tool(root, "actionlint", timeout=1)

        command = " ".join(result["command"])
        self.assertIn(".github", command)
        self.assertIn("ci.yml", command)
        self.assertNotIn("*.yml", command)
        self.assertNotIn("*.yaml", command)

    def test_morph_payload_uses_fast_model(self):
        payload = build_morph_payload("Add error handling", "code", "updated")
        self.assertEqual(payload["model"], "morph-v3-fast")
        self.assertIn(
            "<instruction>Add error handling</instruction>",
            payload["messages"][0]["content"],
        )

    def test_morph_blocks_secret_like_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = morph_apply_snippet(
                root,
                "Update code",
                "const token = 'sk-123456789012345678901234';",
                execute=True,
                confirm_spend=True,
            )
            self.assertIn("blocked", result)
            self.assertFalse(result["executed"])


if __name__ == "__main__":
    unittest.main()
