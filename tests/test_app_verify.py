"""Post-edit structural verification for OPai Build (#276): honest, offline
checks that catch the real failure modes of full-file regeneration —
truncated output, broken asset wiring, invalid JSON — plus strict rollback."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opai import gui_recents, gui_web
from opaihub.app_scaffold import scaffold_app
from opaihub.app_verify import scan_balance, verify_app
from opaihub.build_loop import (
    apply_edits,
    load_app_manifest,
    rollback_edits,
    run_build_request,
)
from opaihub.checkpoints import load_run_checkpoint


class ScanBalanceTests(unittest.TestCase):
    def test_balanced_js_passes(self):
        code = "function f(a) { return [a, {b: 1}]; } // note: {\n"
        self.assertTrue(scan_balance(code)["ok"])

    def test_truncated_js_fails_with_a_clear_reason(self):
        truncated = "function f() {\n  if (x) {\n    doThing("
        result = scan_balance(truncated)
        self.assertFalse(result["ok"])
        self.assertIn("unclosed", result["detail"])

    def test_braces_inside_strings_and_comments_are_ignored(self):
        code = (
            "const a = \"{[(\";\nconst b = '}}';\nconst c = `${x} {`;\n"
            "// } comment }\n/* { block { */\nlet d = {};\n"
        )
        self.assertTrue(scan_balance(code)["ok"], scan_balance(code))

    def test_escaped_quotes_do_not_end_strings(self):
        self.assertTrue(scan_balance('const s = "a\\"b{";')["ok"])

    def test_unexpected_closer_fails(self):
        self.assertFalse(scan_balance("}")["ok"])

    def test_unterminated_string_fails(self):
        self.assertFalse(scan_balance('const s = "open')["ok"])

    def test_regex_literals_with_quotes_and_braces_are_ignored(self):
        # The scaffold's own escapeHtml uses this pattern — it must pass.
        code = "String(s).replace(/[&<>\"']/g, function (c) { return c; });"
        self.assertTrue(scan_balance(code)["ok"], scan_balance(code))

    def test_division_is_not_mistaken_for_a_regex(self):
        code = "const x = a / b;\nconst y = total / 2; let z = {};"
        self.assertTrue(scan_balance(code)["ok"], scan_balance(code))

    def test_unterminated_regex_fails(self):
        self.assertFalse(scan_balance("const r = /abc")["ok"])

    def test_css_balance(self):
        self.assertTrue(scan_balance("body { color: red; }", language="css")["ok"])
        self.assertFalse(scan_balance("body { color: red;", language="css")["ok"])
        # CSS content strings with braces are fine.
        self.assertTrue(
            scan_balance('a::before { content: "{"; }', language="css")["ok"]
        )


class VerifyAppTests(unittest.TestCase):
    def _app(self, tmp):
        result = scaffold_app(Path(tmp), "a demo app")
        return Path(result.root)

    def test_a_fresh_scaffold_verifies_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            report = verify_app(
                root, entrypoint="index.html", files=["app.js", "styles.css"]
            )
            self.assertTrue(report["ok"], report)
            self.assertGreater(report["passed"], 2)
            self.assertEqual(report["failed"], 0)

    def test_a_missing_asset_reference_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            html = (root / "index.html").read_text(encoding="utf-8")
            (root / "index.html").write_text(
                html.replace('<script src="app.js">', '<script src="missing.js">'),
                encoding="utf-8",
            )
            report = verify_app(root, entrypoint="index.html")
            self.assertFalse(report["ok"])
            self.assertTrue(
                any("missing.js" in c["check"] for c in report["checks"] if not c["ok"])
            )

    def test_truncated_js_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            (root / "app.js").write_text("function f() {", encoding="utf-8")
            report = verify_app(root, files=["app.js"])
            self.assertFalse(report["ok"])

    def test_invalid_json_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            (root / "data.json").write_text("{oops", encoding="utf-8")
            report = verify_app(root, files=["data.json"])
            self.assertFalse(report["ok"])

    def test_missing_entrypoint_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = verify_app(Path(tmp), entrypoint="index.html")
            self.assertFalse(report["ok"])

    def test_external_and_data_urls_are_not_checked_as_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text(
                "<!doctype html><html><head>"
                '<link href="https://cdn.example/x.css" rel="stylesheet">'
                '<img src="data:image/png;base64,xx">'
                "</head><body></body></html>",
                encoding="utf-8",
            )
            report = verify_app(root, entrypoint="index.html")
            self.assertTrue(report["ok"], report)


class RollbackTests(unittest.TestCase):
    def _app(self, tmp):
        result = scaffold_app(Path(tmp), "a rollback demo")
        make_repo(Path(result.root))
        return Path(result.root)

    def test_rollback_restores_updates_and_removes_created_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            manifest = load_app_manifest(root)
            original = (root / "app.js").read_text(encoding="utf-8")
            outcome = apply_edits(
                root,
                {"app.js": "broken {", "extra.js": "new file"},
                manifest,
            )
            self.assertEqual(len(outcome["applied"]), 2)
            restored = rollback_edits(root, outcome, manifest)
            self.assertEqual(sorted(restored), ["app.js", "extra.js"])
            self.assertEqual((root / "app.js").read_text(encoding="utf-8"), original)
            self.assertFalse((root / "extra.js").exists())
            self.assertNotIn("extra.js", load_app_manifest(root)["files"])


class _CannedRunner:
    """Minimal provider fake: both blocking and streaming entry points."""

    model = "opus"
    account_id = "claude"

    def __init__(self, answer: str):
        self._answer = answer

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, **kwargs):
        return {"text": self._answer, "cost": 0.01}

    def stream(self, prompt: str, **kwargs):
        return {"text": self._answer, "cost": 0.01}


class StrictBuildTests(unittest.TestCase):
    def _app(self, tmp):
        result = scaffold_app(Path(tmp), "a strict demo")
        make_repo(Path(result.root))
        return Path(result.root)

    def test_good_edit_reports_verification_passed(self):
        answer = "```file:app.js\nconsole.log('fine');\n```"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            report = run_build_request(
                root,
                "log a line",
                model="claude:opus",
                account_runner=_CannedRunner(answer),
                strict=True,
            )
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["status"], "applied")
            self.assertTrue(report["verify"]["ok"])

    def test_truncated_edit_is_rolled_back_under_strict(self):
        answer = "```file:app.js\nfunction f() { if (x) { doThing(\n```"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            original = (root / "app.js").read_text(encoding="utf-8")
            report = run_build_request(
                root,
                "break it",
                model="claude:opus",
                account_runner=_CannedRunner(answer),
                strict=True,
            )
            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "rolled_back")
            self.assertIn("app.js", report["rolled_back"])
            # The app is exactly as it was.
            self.assertEqual((root / "app.js").read_text(encoding="utf-8"), original)

    def test_incomplete_strict_rollback_keeps_changed_file_evidence(self):
        answer = "```file:app.js\nfunction f() { if (x) { doThing(\n```"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            with mock.patch("opaihub.build_loop.rollback_edits", return_value=[]):
                report = run_build_request(
                    root,
                    "break it",
                    model="claude:opus",
                    account_runner=_CannedRunner(answer),
                    strict=True,
                )
            checkpoint = load_run_checkpoint(root, report["checkpoint_id"])

            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "rollback_failed")
            self.assertFalse(report["rollback_complete"])
            self.assertEqual(report["remaining_changed_files"], ["app.js"])
            self.assertEqual([item["path"] for item in report["applied"]], ["app.js"])
            self.assertEqual(checkpoint.completion_state, "failed")
            self.assertEqual(checkpoint.result_changed_files, ("app.js",))

    def test_truncated_edit_is_kept_but_flagged_without_strict(self):
        answer = "```file:app.js\nfunction f() { if (x) { doThing(\n```"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._app(tmp)
            gui_web._persist_turn_start(root, "verify-failed", "break it", "build")
            report = run_build_request(
                root,
                "break it",
                model="claude:opus",
                account_runner=_CannedRunner(answer),
                strict=False,
            )
            gui_web._persist_turn_result(
                root,
                "verify-failed",
                report,
                mode="build",
                build=True,
            )
            checkpoint = load_run_checkpoint(root, report["checkpoint_id"])
            thread = gui_recents.load_thread(root)

            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "verification_failed")
            self.assertFalse(report["verify"]["ok"])
            self.assertIsNotNone(report["backup_dir"])  # recovery stays possible
            self.assertTrue(
                (root / "app.js").read_text(encoding="utf-8").startswith("function f()")
            )
            self.assertEqual(checkpoint.completion_state, "failed")
            self.assertEqual(checkpoint.result_changed_files, ("app.js",))
            self.assertEqual(thread["messages"][-1]["status"], "failed")
            self.assertIn("changes were preserved", thread["messages"][-1]["text"])
            self.assertNotIn("Applied and verified", thread["messages"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
