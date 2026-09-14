"""The Vesta Build customization loop (#276): scaffold → cheap targeted edits.

Hermetic end to end: the model is a fake runner returning canned ``file:``
blocks, the pipeline is the real ``handle_gui_message``, and every write is
checked for safety (root confinement, backups, honest statuses)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import FakeStreamingRunner, make_repo

from opaihub.app_scaffold import scaffold_app
from opaihub.build_loop import (
    BACKUP_DIR,
    BUILD_LOG_NAME,
    MANIFEST_NAME,
    MAX_BUILD_PROMPT_CHARS,
    apply_edits,
    build_edit_prompt,
    load_app_manifest,
    parse_file_blocks,
    record_build_entry,
    rollback_edits,
    run_build_request,
    save_app_manifest,
    select_context,
)
from opaihub.checkpoints import load_run_checkpoint


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

    def test_linked_manifest_cannot_read_or_overwrite_an_outside_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            root = Path(result.root)
            manifest_path = root / MANIFEST_NAME
            manifest_path.unlink()
            outside = root.parent / "outside-manifest.json"
            original = (
                json.dumps({"name": "OUTSIDE_PRIVATE_MARKER", "files": ["private.py"]})
                + "\n"
            )
            outside.write_text(original, encoding="utf-8")
            try:
                manifest_path.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"file symlinks unavailable: {exc}")

            self.assertIsNone(load_app_manifest(root))
            with self.assertRaises(OSError):
                save_app_manifest(root, {"name": "forged", "files": ["app.js"]})
            self.assertEqual(outside.read_text(encoding="utf-8"), original)


class SelectContextTests(unittest.TestCase):
    def test_long_line_indexed_slice_prompt_has_exact_column_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line = (
                ("prefix_value = 1; " * 20)
                + "RareTargetSymbol"
                + ("; suffix_value = 2" * 20)
            )
            source = line + "\n"
            target = root / "src" / "large.py"
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding="utf-8")
            make_repo(root)
            manifest = {"name": "large", "files": ["src/large.py"]}

            selection = select_context(
                root, "inspect RareTargetSymbol", manifest, budget_chars=80
            )
            item = selection["files"][0]
            prompt = build_edit_prompt("inspect RareTargetSymbol", manifest, selection)

            self.assertTrue(item["truncated"])
            self.assertEqual(item["start_line"], 1)
            self.assertEqual(item["end_line"], 1)
            self.assertGreater(item["start_column"], 1)
            self.assertEqual(
                line[item["start_column"] - 1 : item["end_column"] - 1],
                item["content"],
            )
            self.assertIn(
                f"```context:src/large.py#L1C{item['start_column']}-"
                f"L1C{item['end_column']}",
                prompt,
            )

    def test_generic_add_word_cannot_displace_target_symbol_definition_and_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {
                "packages/api/processor.go": (
                    "package api\n\n"
                    "type PaymentGateway struct{}\n\n"
                    "func (PaymentGateway) Charge(amount int) bool {\n"
                    "    return amount > 0\n"
                    "}\n"
                ),
                "packages/web/client.go": (
                    "package web\n\n"
                    "func Checkout(gateway api.PaymentGateway, total int) bool {\n"
                    "    return gateway.Charge(total)\n"
                    "}\n"
                ),
                "frontend/noise.js": "function addFeature() { return true; }\n",
            }
            for relative, content in files.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            make_repo(root)
            relevant_budget = sum(
                len(files[path])
                for path in ("packages/api/processor.go", "packages/web/client.go")
            )

            selection = select_context(
                root,
                "Add retry visibility to PaymentGateway callers",
                {"name": "polyglot", "files": list(files)},
                budget_chars=relevant_budget,
            )

            self.assertEqual(
                [item["path"] for item in selection["files"]],
                ["packages/api/processor.go", "packages/web/client.go"],
            )

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

    def test_manifest_paths_cannot_escape_or_expose_nested_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            root = Path(result.root)
            outside = Path(tmp) / "outside-private.py"
            outside.write_text("ABSOLUTE_PRIVATE_MARKER\n", encoding="utf-8")
            private = root / "config" / ".env.local"
            private.parent.mkdir(parents=True, exist_ok=True)
            private.write_text("NESTED_PRIVATE_MARKER\n", encoding="utf-8")
            carrier = root / "credentials.json"
            carrier.write_text("{}\n", encoding="utf-8")
            ads_path = root / "credentials.json:payload.py"
            try:
                ads_path.write_text("ADS_PRIVATE_MARKER\n", encoding="utf-8")
            except OSError:
                ads_path = None
            normalized_dir = root / "node_modules"
            normalized_dir.mkdir()
            (normalized_dir / "secret.js").write_text(
                "TRAILING_DOT_PRIVATE_MARKER\n", encoding="utf-8"
            )
            manifest = load_app_manifest(root)
            manifest["files"].extend(
                [
                    str(outside),
                    "config/.env.local",
                    "credentials.json:payload.py",
                    "node_modules./secret.js",
                    {"not": "a path"},
                    None,
                ]
            )

            selection = select_context(root, "inspect everything", manifest)
            prompt = build_edit_prompt("inspect everything", manifest, selection)

            self.assertNotIn("ABSOLUTE_PRIVATE_MARKER", prompt)
            self.assertNotIn("NESTED_PRIVATE_MARKER", prompt)
            self.assertNotIn("ADS_PRIVATE_MARKER", prompt)
            self.assertNotIn("TRAILING_DOT_PRIVATE_MARKER", prompt)
            self.assertNotIn(
                str(outside), [item["path"] for item in selection["files"]]
            )
            self.assertNotIn(
                "config/.env.local", [item["path"] for item in selection["files"]]
            )
            if ads_path is not None:
                self.assertNotIn(
                    "credentials.json:payload.py",
                    [item["path"] for item in selection["files"]],
                )

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
                "bad?.py": "x",
                "bad*.py": "x",
                "bad|.py": "x",
                "bad>.py": "x",
                "bad<.py": "x",
                'bad".py': "x",
            }
            outcome = apply_edits(root, bad, manifest)
            self.assertEqual(outcome["applied"], [])
            self.assertEqual(len(outcome["rejected"]), len(bad))
            self.assertFalse((Path(tmp) / "outside.js").exists())

    def test_symlinked_edit_parent_cannot_escape_to_prefix_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._app(tmp)
            outside = root.parent / f"{root.name}-escape"
            outside.mkdir()
            link = root / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            outcome = apply_edits(
                root, {"linked/escaped.js": "private = true;\n"}, manifest
            )

            self.assertEqual(outcome["applied"], [])
            self.assertEqual(
                outcome["rejected"][0]["reason"], "resolves outside the app"
            )
            self.assertFalse((outside / "escaped.js").exists())

    def test_hardlinked_context_and_edit_target_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._app(tmp)
            outside = root.parent / "outside-hardlink.py"
            outside.write_text("HARDLINK_PRIVATE_MARKER\n", encoding="utf-8")
            linked = root / "linked.py"
            try:
                os.link(outside, linked)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            manifest["files"].append("linked.py")

            selection = select_context(root, "inspect linked.py", manifest)
            outcome = apply_edits(root, {"linked.py": "overwritten\n"}, manifest)

            self.assertNotIn("linked.py", [item["path"] for item in selection["files"]])
            self.assertEqual(outcome["applied"], [])
            self.assertEqual(
                outside.read_text(encoding="utf-8"), "HARDLINK_PRIVATE_MARKER\n"
            )

    def test_linked_backup_directory_cannot_escape_or_apply_without_a_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._app(tmp)
            target = root / "app.js"
            original = target.read_text(encoding="utf-8")
            outside = root.parent / "outside-backups"
            outside.mkdir()
            try:
                (root / BACKUP_DIR).symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            outcome = apply_edits(root, {"app.js": "unsafe change\n"}, manifest)

            self.assertEqual(outcome["applied"], [])
            self.assertTrue(outcome["rejected"])
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertEqual(list(outside.rglob("*")), [])

    def test_linked_build_log_cannot_append_outside_the_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _manifest = self._app(tmp)
            outside = root.parent / "outside-build-log.jsonl"
            outside.write_text("OUTSIDE_LOG_MARKER\n", encoding="utf-8")
            log_path = root / BUILD_LOG_NAME
            try:
                log_path.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"file symlinks unavailable: {exc}")

            with self.assertRaises(OSError):
                record_build_entry(root, {"status": "forged"})
            self.assertEqual(
                outside.read_text(encoding="utf-8"), "OUTSIDE_LOG_MARKER\n"
            )

    def test_hardlinked_build_log_cannot_append_outside_the_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _manifest = self._app(tmp)
            outside = root.parent / "outside-hardlink-log.jsonl"
            outside.write_text("OUTSIDE_HARDLINK_LOG\n", encoding="utf-8")
            try:
                os.link(outside, root / BUILD_LOG_NAME)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaises(OSError):
                record_build_entry(root, {"status": "forged"})
            self.assertEqual(
                outside.read_text(encoding="utf-8"), "OUTSIDE_HARDLINK_LOG\n"
            )

    def test_rollback_refuses_a_target_relinked_outside_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._app(tmp)
            outcome = apply_edits(root, {"app.js": "temporary change\n"}, manifest)
            target = root / "app.js"
            target.unlink()
            outside = root.parent / "outside-rollback.js"
            outside.write_text("OUTSIDE_ROLLBACK_MARKER\n", encoding="utf-8")
            try:
                target.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"file symlinks unavailable: {exc}")

            restored = rollback_edits(root, outcome, manifest)

            self.assertNotIn("app.js", restored)
            self.assertEqual(
                outside.read_text(encoding="utf-8"), "OUTSIDE_ROLLBACK_MARKER\n"
            )

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
    def test_cloud_confirmation_grant_is_forwarded_to_the_shared_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(_scaffold(tmp).root)
            blocked = {
                "status": "needs_auto_confirmation",
                "answer": "Confirm the named cloud model.",
                "fallbackModelId": "free:gemini:flash",
                "fallbackModelLabel": "Gemini Flash",
                "cloudStarted": False,
                "completion_verdict": {"verdict": "blocked"},
            }
            with mock.patch(
                "opaihub.gui_pipeline.handle_gui_message", return_value=blocked
            ) as handle:
                report = run_build_request(
                    root,
                    "add search",
                    model="free:gemini:flash",
                    allow_cloud=True,
                )

            self.assertEqual(report["status"], "needs_auto_confirmation")
            self.assertTrue(handle.call_args.kwargs["allow_cloud"])
            self.assertEqual(report["fallbackModelId"], "free:gemini:flash")
            self.assertEqual(report["fallbackModelLabel"], "Gemini Flash")
            self.assertFalse(report["cloudStarted"])
            self.assertEqual(report["completion_verdict"]["verdict"], "blocked")

    def test_linked_build_log_fails_before_provider_or_edit_side_effects(self):
        answer = "```file:app.js\nconsole.log('must not land');\n```"
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            root = Path(result.root)
            original = (root / "app.js").read_text(encoding="utf-8")
            outside = root.parent / "outside-run-log.jsonl"
            outside.write_text("OUTSIDE_RUN_LOG\n", encoding="utf-8")
            try:
                (root / BUILD_LOG_NAME).symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"file symlinks unavailable: {exc}")
            runner = _EditingRunner(answer)

            report = run_build_request(
                root,
                "replace app.js",
                model="claude:opus",
                account_runner=runner,
            )

            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "unsafe_app_state")
            self.assertEqual(runner.calls, [])
            self.assertEqual((root / "app.js").read_text(encoding="utf-8"), original)
            self.assertEqual(outside.read_text(encoding="utf-8"), "OUTSIDE_RUN_LOG\n")

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
            checkpoint = load_run_checkpoint(Path(result.root), report["checkpoint_id"])
            self.assertEqual(checkpoint.completion_state, "answered")
            self.assertEqual(checkpoint.outcome, "no_edits")
            self.assertEqual(checkpoint.result_changed_files, ())

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
            self.assertEqual(report["context"]["budget_scope"], "source_context")
            self.assertTrue(report["context"]["within_context_budget"])
            self.assertTrue(report["context"]["within_build_prompt_budget"])
            self.assertEqual(
                report["context"]["build_prompt_chars"], len(report["prompt"])
            )

    def test_pre_pipeline_build_prompt_has_a_fail_closed_absolute_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _scaffold(tmp)
            report = run_build_request(
                Path(result.root), "x" * MAX_BUILD_PROMPT_CHARS, dry_run=True
            )

            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "prompt_too_large")
            self.assertGreater(report["build_prompt_chars"], MAX_BUILD_PROMPT_CHARS)


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
