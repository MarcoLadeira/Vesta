"""Per-app build receipt (#276): the aggregate cost story of a Vesta Build
app — measured vs estimated spend kept apart, plus the tokens never spent
(free boilerplate + context slicing)."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from _helpers import make_repo

from vestahub.app_scaffold import scaffold_app
from vestahub.build_loop import (
    BUILD_LOG_NAME,
    app_receipt,
    apply_edits,
    load_app_manifest,
    read_build_log,
    record_build_entry,
    run_build_request,
)


def _scaffold(tmp: str):
    result = scaffold_app(Path(tmp), "a receipt demo")
    make_repo(Path(result.root))
    return Path(result.root)


class _CannedRunner:
    model = "opus"
    account_id = "claude"

    def __init__(self, answer: str, cost: float = 0.01):
        self._answer = answer
        self._cost = cost

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, **kwargs):
        return {"text": self._answer, "cost": self._cost}

    def stream(self, prompt: str, **kwargs):
        return {"text": self._answer, "cost": self._cost}


class ManifestPersistenceTests(unittest.TestCase):
    def test_scaffold_persists_the_free_boilerplate_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            manifest = load_app_manifest(root)
            self.assertGreater(manifest["boilerplate_tokens_avoided"], 100)
            self.assertGreater(manifest["created_at"], 0)


class BuildLogTests(unittest.TestCase):
    def test_entries_append_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            record_build_entry(root, {"schema": 1, "status": "applied"})
            record_build_entry(root, {"schema": 1, "status": "no_edits"})
            entries = read_build_log(root)
            self.assertEqual([e["status"] for e in entries], ["applied", "no_edits"])

    def test_corrupt_lines_are_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            (root / BUILD_LOG_NAME).write_text(
                '{"status": "applied"}\nnot json\n42\n', encoding="utf-8"
            )
            entries = read_build_log(root)
            self.assertEqual(len(entries), 1)

    def test_missing_log_reads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_build_log(Path(tmp)), [])


class RunBuildLogsTests(unittest.TestCase):
    def test_an_applied_build_is_logged_with_spend_and_context(self):
        answer = "```file:app.js\nconsole.log('x');\n```"
        secret = "sk-" + "proj-" + ("A1_" * 18)
        request = f"log a line with {secret}"
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            report = run_build_request(
                root,
                request,
                model="claude:opus",
                account_runner=_CannedRunner(answer),
            )
            self.assertEqual(report["status"], "applied")
            entries = read_build_log(root)
            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertEqual(entry["status"], "applied")
            self.assertEqual(entry["files_changed"], 1)
            self.assertGreater(entry["chars_total"], 0)
            self.assertNotIn("request", entry)
            self.assertEqual(len(entry["request_fingerprint"]), 16)
            self.assertEqual(entry["request_chars"], len(request))
            raw_log = (root / BUILD_LOG_NAME).read_text(encoding="utf-8")
            self.assertNotIn(secret, raw_log)
            self.assertNotIn("log a line", raw_log)
            # The report carries the running total for the CLI one-liner.
            self.assertTrue(report["receipt_so_far"]["ok"])
            self.assertEqual(report["receipt_so_far"]["builds"], 1)

    def test_clear_history_scrubs_legacy_raw_build_requests_without_losing_receipt(
        self,
    ):
        from vesta.gui_web import clear_history_payload

        secret = "sk-" + "ant-api03-" + ("B2_" * 18)
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            (root / BUILD_LOG_NAME).write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "status": "applied",
                        "request": f"legacy prompt {secret}",
                        "files_changed": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = clear_history_payload(root)
            raw_log = (root / BUILD_LOG_NAME).read_text(encoding="utf-8")
            entries = read_build_log(root)

            self.assertTrue(result["ok"])
            self.assertNotIn(secret, raw_log)
            self.assertNotIn("legacy prompt", raw_log)
            self.assertNotIn("request", entries[0])
            self.assertEqual(len(entries[0]["request_fingerprint"]), 16)
            self.assertEqual(app_receipt(root)["builds"], 1)

    def test_no_edits_and_dry_run_logging_are_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            run_build_request(
                root,
                "chat only",
                model="claude:opus",
                account_runner=_CannedRunner("no blocks here"),
            )
            self.assertEqual(read_build_log(root)[-1]["status"], "no_edits")
            before = len(read_build_log(root))
            run_build_request(root, "dry", dry_run=True)
            self.assertEqual(len(read_build_log(root)), before)  # dry runs never log

    def test_the_model_cannot_write_the_build_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            manifest = load_app_manifest(root)
            outcome = apply_edits(root, {BUILD_LOG_NAME: "forged"}, manifest)
            self.assertEqual(outcome["applied"], [])
            self.assertEqual(outcome["rejected"][0]["reason"], "protected file")


class AppReceiptTests(unittest.TestCase):
    def test_aggregates_builds_and_separates_measured_from_estimated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            record_build_entry(
                root,
                {
                    "status": "applied",
                    "files_changed": 1,
                    "added": 10,
                    "removed": 2,
                    "chars_selected": 1000,
                    "chars_total": 5000,
                    "spend_usd": 0.03,
                    "savings_usd": 0.0,
                    "confidence": "actual",
                },
            )
            record_build_entry(
                root,
                {
                    "status": "applied",
                    "files_changed": 1,
                    "added": 5,
                    "removed": 1,
                    "chars_selected": 2000,
                    "chars_total": 6000,
                    "spend_usd": 0.02,
                    "savings_usd": 0.05,
                    "confidence": "estimated",
                },
            )
            receipt = app_receipt(root)
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["builds"], 2)
            self.assertEqual(receipt["applied_builds"], 2)
            self.assertEqual(receipt["lines_added"], 15)
            self.assertAlmostEqual(receipt["spend_usd_actual"], 0.03)
            self.assertAlmostEqual(receipt["spend_usd_estimated"], 0.02)
            self.assertAlmostEqual(receipt["savings_usd_estimated"], 0.05)
            # context avoided: (5000-1000)+(6000-2000)=8000 chars -> 2000 tokens
            self.assertEqual(receipt["context_tokens_avoided"], 2000)
            self.assertEqual(
                receipt["tokens_never_sent"],
                receipt["boilerplate_tokens_avoided"] + 2000,
            )

    def test_fresh_app_has_zero_builds_but_free_boilerplate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            receipt = app_receipt(root)
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["builds"], 0)
            self.assertGreater(receipt["tokens_never_sent"], 100)

    def test_not_an_app_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = app_receipt(Path(tmp))
            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["status"], "not_an_app")


class CliAppReceiptTests(unittest.TestCase):
    def test_vesta_app_receipt_end_to_end(self):
        from vesta.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            record_build_entry(
                root,
                {
                    "status": "applied",
                    "files_changed": 1,
                    "added": 3,
                    "removed": 0,
                    "chars_selected": 100,
                    "chars_total": 400,
                    "spend_usd": 0.01,
                    "savings_usd": 0.0,
                    "confidence": "actual",
                },
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["app-receipt", "--app", str(root), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["builds"], 1)
            self.assertAlmostEqual(payload["spend_usd_actual"], 0.01)

    def test_vesta_app_receipt_human_output(self):
        from vesta.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = _scaffold(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["app-receipt", "--app", str(root)])
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("Vesta Build receipt", out)
            self.assertIn("tokens never spent", out)

    def test_vesta_app_receipt_on_a_non_app_exits_2(self):
        from vesta.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["app-receipt", "--app", tmp, "--json"])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
