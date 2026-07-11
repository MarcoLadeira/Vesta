"""The OPai Build customization loop (#276): scaffold → cheap targeted edits.

Hermetic end to end: the model is a fake runner returning canned ``file:``
blocks, the pipeline is the real ``handle_gui_message``, and every write is
checked for safety (root confinement, backups, honest statuses)."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import FakeStreamingRunner, make_repo

from opaihub.app_scaffold import scaffold_app
from opaihub.build_loop import (
    MANIFEST_NAME,
    apply_edits,
    build_edit_prompt,
    load_app_manifest,
    parse_file_blocks,
    run_build_request,
    select_context,
)


def _scaffold(tmp: str):
    """A scaffolded app inside a git repo (the pipeline expects repo context)."""
    result = scaffold_app(Path(tmp), "a todo app with dark mode")
    make_repo(Path(result.root))
    return result


class ManifestTests(unittest.TestCase):
    def test_scaffold_writes_a_manifest_the_loop_can_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            manifest = load_app_manifest(Path(result.root))
            self.assertIsNotNone(manifest)
            self.assertEqual(manifest["name"], result.name)
            self.assertEqual(sorted(manifest["files"]), result.files)
            self.assertEqual(manifest["entrypoint"], "index.html")

    def test_non_app_dirs_have_no_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_app_manifest(Path(tmp)))

    def test_corrupt_manifest_is_treated_as_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / MANIFEST_NAME).write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_app_manifest(Path(tmp)))


class SelectContextTests(unittest.TestCase):
    def test_a_file_named_in_the_request_always_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            manifest = load_app_manifest(Path(result.root))
            sel = select_context(
                Path(result.root), "change the footer in index.html", manifest
            )
            self.assertEqual(sel["files"][0]["path"], "index.html")

    def test_style_requests_rank_the_stylesheet_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            manifest = load_app_manifest(Path(result.root))
            sel = select_context(
                Path(result.root), "make the theme colors darker", manifest
            )
            self.assertEqual(sel["files"][0]["path"], "styles.css")

    def test_budget_trims_but_always_selects_at_least_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            manifest = load_app_manifest(Path(result.root))
            sel = select_context(
                Path(result.root), "implement the list logic", manifest, budget_chars=10
            )
            self.assertEqual(len(sel["files"]), 1)  # top file only, over budget
            self.assertGreater(sel["saved_pct"], 0)

    def test_manifest_and_hidden_files_are_never_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            root = Path(result.root)
            manifest = load_app_manifest(root)
            manifest["files"].append(MANIFEST_NAME)
            sel = select_context(root, "anything at all", manifest)
            self.assertNotIn(MANIFEST_NAME, [item["path"] for item in sel["files"]])

    def test_accounting_is_conserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            manifest = load_app_manifest(Path(result.root))
            sel = select_context(Path(result.root), "add a button", manifest)
            self.assertLessEqual(sel["chars_selected"], sel["chars_total"])
            self.assertGreaterEqual(sel["saved_pct"], 0)


class PromptAndParseTests(unittest.TestCase):
    def test_prompt_contains_request_files_and_strict_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            manifest = load_app_manifest(Path(result.root))
            sel = select_context(Path(result.root), "add a clear-all button", manifest)
            prompt = build_edit_prompt("add a clear-all button", manifest, sel)
            self.assertIn("add a clear-all button", prompt)
            self.assertIn("```file:", prompt)
            self.assertIn("complete updated content", prompt)

    def test_parse_extracts_multiple_blocks(self):
        answer = (
            "Here you go.\n\n```file:app.js\nconsole.log(1);\n```\n"
            "and the styles:\n```file: styles.css\nbody { color: red; }\n```\ndone"
        )
        edits = parse_file_blocks(answer)
        self.assertEqual(sorted(edits), ["app.js", "styles.css"])
        self.assertEqual(edits["app.js"], "console.log(1);\n")
        self.assertEqual(edits["styles.css"], "body { color: red; }\n")

    def test_parse_ignores_plain_code_fences_and_empty_answers(self):
        self.assertEqual(parse_file_blocks("```js\ncode\n```"), {})
        self.assertEqual(parse_file_blocks(""), {})
        self.assertEqual(parse_file_blocks(None), {})

    def test_parse_tolerates_crlf(self):
        edits = parse_file_blocks("```file:a.js\r\nlet x = 1;\r\n```")
        self.assertIn("a.js", edits)


class ApplyEditsSafetyTests(unittest.TestCase):
    def _app(self, tmp):
        result = _scaffold(tmp)
        return Path(result.root), load_app_manifest(Path(result.root))

    def test_updates_are_backed_up_and_diffstatted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._app(tmp)
            original = (root / "app.js").read_text(encoding="utf-8")
            outcome = apply_edits(root, {"app.js": "console.log('new');\n"}, manifest)
            self.assertEqual(outcome["applied"][0]["action"], "updated")
            self.assertGreater(outcome["applied"][0]["removed"], 0)
            backup = Path(outcome["backup_dir"]) / "app.js"
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            self.assertEqual(
                (root / "app.js").read_text(encoding="utf-8"), "console.log('new');\n"
            )

    def test_new_files_join_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._app(tmp)
            outcome = apply_edits(root, {"utils.js": "export {};\n"}, manifest)
            self.assertEqual(outcome["applied"][0]["action"], "created")
            self.assertIn("utils.js", load_app_manifest(root)["files"])

    def test_escapes_and_protected_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._app(tmp)
            bad = {
                "../outside.js": "x",
                "C:/temp/abs.js": "x",
                MANIFEST_NAME: "{}",
                ".git/config": "x",
                "evil.exe": "x",
            }
            outcome = apply_edits(root, bad, manifest)
            self.assertEqual(outcome["applied"], [])
            self.assertEqual(len(outcome["rejected"]), len(bad))
            self.assertFalse((Path(tmp) / "outside.js").exists())

    def test_oversized_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._app(tmp)
            outcome = apply_edits(root, {"big.js": "x" * 600_000}, manifest)
            self.assertEqual(outcome["applied"], [])
            self.assertEqual(outcome["rejected"][0]["reason"], "file too large")


class _EditingRunner(FakeStreamingRunner):
    """A fake provider that answers with a canned file: block.

    Implements both paths: ``stream`` (callbacks supplied) and ``complete``
    (blocking, what ``opai build`` uses without callbacks).
    """

    def __init__(self, answer: str):
        super().__init__(chunks=[answer])
        self._answer = answer

    def stream(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        on_text = kwargs.get("on_text")
        if on_text:
            on_text(self._answer)
        return {"text": self._answer, "cost": 0.01}

    def complete(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return {"text": self._answer, "cost": 0.01}


class RunBuildRequestTests(unittest.TestCase):
    def test_end_to_end_edit_lands_on_disk_through_the_real_pipeline(self):
        answer = "```file:app.js\nconsole.log('built by opai');\n```"
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            runner = _EditingRunner(answer)
            report = run_build_request(
                Path(result.root),
                "replace app.js with a log line",
                model="claude:opus",
                account_runner=runner,
            )
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["status"], "applied")
            self.assertEqual(report["applied"][0]["path"], "app.js")
            self.assertIn(
                "built by opai",
                (Path(result.root) / "app.js").read_text(encoding="utf-8"),
            )
            # The prompt the model saw was the targeted slice with file blocks.
            self.assertIn("file:app.js", runner.calls[0]["prompt"])
            # Receipt flows through from the normal pipeline (paid call: spend).
            self.assertIsNotNone(report.get("receipt"))
            self.assertIn("preview_cmd", report)

    def test_answer_without_blocks_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            before = (Path(result.root) / "app.js").read_text(encoding="utf-8")
            report = run_build_request(
                Path(result.root),
                "do something",
                model="claude:opus",
                account_runner=_EditingRunner("I would suggest refactoring."),
            )
            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "no_edits")
            self.assertEqual(
                (Path(result.root) / "app.js").read_text(encoding="utf-8"), before
            )

    def test_not_an_app_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_build_request(Path(tmp), "x")
            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "not_an_app")

    def test_dry_run_sends_nothing_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            report = run_build_request(
                Path(result.root), "style the header", dry_run=True
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["status"], "dry_run")
            self.assertIn("styles.css", report["context"]["files"])
            self.assertIn("```file:", report["prompt"])


class CliBuildCommandTests(unittest.TestCase):
    def test_opai_build_wires_through_and_reports_json(self):
        from opai.cli import main

        canned = {
            "ok": True,
            "status": "applied",
            "applied": [
                {"path": "app.js", "action": "updated", "added": 1, "removed": 1}
            ],
            "rejected": [],
            "context": {
                "files": ["app.js"],
                "chars_selected": 10,
                "chars_total": 20,
                "saved_pct": 50,
            },
            "preview_cmd": "python -m http.server 8000",
        }
        with mock.patch(
            "opaihub.build_loop.run_build_request", return_value=canned
        ) as run:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["build", "tweak it", "--app", ".", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(buf.getvalue())["ok"])
        self.assertEqual(run.call_args.args[1], "tweak it")

    def test_opai_build_dry_run_end_to_end(self):
        from opai.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "build",
                        "style the page",
                        "--app",
                        result.root,
                        "--dry-run",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "dry_run")
            self.assertGreater(len(payload["context"]["files"]), 0)


if __name__ == "__main__":
    unittest.main()
