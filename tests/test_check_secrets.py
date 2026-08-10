from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "check_secrets.py"
    spec = importlib.util.spec_from_file_location("opai_check_secrets", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load check_secrets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scan(*, secret_hash: str = "a" * 40, filename: str = "fixture.py") -> dict:
    return {
        "version": "1.5.0",
        "plugins_used": [],
        "filters_used": [],
        "results": {
            filename: [
                {
                    "type": "Secret Keyword",
                    "filename": filename,
                    "hashed_secret": secret_hash,
                    "is_verified": False,
                    "line_number": 1,
                }
            ]
        },
        "generated_at": "2026-08-09T00:00:00Z",
    }


class EnforcingSecretScanTests(unittest.TestCase):
    def test_known_baseline_fingerprint_is_accepted(self) -> None:
        module = _load_module()
        self.assertEqual(module.unapproved_findings(_scan(), _scan()), [])

    def test_new_or_changed_fingerprint_fails(self) -> None:
        module = _load_module()
        findings = module.unapproved_findings(
            _scan(secret_hash="b" * 40), _scan(secret_hash="a" * 40)
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["filename"], "fixture.py")
        self.assertNotIn("b" * 40, json.dumps(findings))

    def test_scanner_nonzero_is_infrastructure_blocked_not_clean(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            baseline = Path(temporary_directory) / ".secrets.baseline"
            baseline.write_text(json.dumps(_scan()), encoding="utf-8")
            completed = mock.Mock(returncode=2, stdout="", stderr="scanner unavailable")
            with mock.patch.object(module.subprocess, "run", return_value=completed):
                result = module.main(["--baseline", str(baseline)])
        self.assertEqual(result, module.INFRASTRUCTURE_EXIT)

    def test_new_finding_returns_security_exit_without_secret_value(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            baseline = Path(temporary_directory) / ".secrets.baseline"
            baseline.write_text(
                json.dumps(_scan(secret_hash="a" * 40)), encoding="utf-8"
            )
            completed = mock.Mock(
                returncode=0,
                stdout=json.dumps(_scan(secret_hash="b" * 40)),
                stderr="",
            )
            with mock.patch.object(module.subprocess, "run", return_value=completed):
                result = module.main(["--baseline", str(baseline)])
        self.assertEqual(result, module.SECURITY_EXIT)

    def test_scanner_does_not_exclude_secret_bearing_lines_by_keyword(self) -> None:
        module = _load_module()
        empty_scan = {**_scan(), "results": {}}
        with tempfile.TemporaryDirectory() as temporary_directory:
            baseline = Path(temporary_directory) / ".secrets.baseline"
            baseline.write_text(json.dumps(empty_scan), encoding="utf-8")
            completed = mock.Mock(
                returncode=0, stdout=json.dumps(empty_scan), stderr=""
            )
            with mock.patch.object(
                module.subprocess, "run", return_value=completed
            ) as run:
                self.assertEqual(module.main(["--baseline", str(baseline)]), 0)

        command = run.call_args.args[0]
        self.assertNotIn("--exclude-lines", command)
        self.assertNotIn("MORPH_API_KEY", command)

    def test_generated_tool_trees_are_excluded_but_ignored_secrets_are_scanned(
        self,
    ) -> None:
        module = _load_module()
        generated_paths = (
            ".venv/Lib/site-packages/example.py",
            "node_modules/example/index.js",
            ".pytest_cache/CACHEDIR.TAG",
            ".hypothesis/examples/fixture",
            ".mypy_cache/3.13/cache.json",
            "test-results/output.txt",
            "playwright-report/index.html",
            ".playwright/chromium/browser.exe",
            "build/lib/example.py",
            "dist/opai.whl",
            "opai.egg-info/PKG-INFO",
            "opai/__pycache__/module.pyc",
        )
        for path in generated_paths:
            with self.subTest(path=path):
                self.assertIsNotNone(re.search(module.EXCLUDED_FILES, path))

        for sensitive_path in (".env", ".env.local", "ignored/config.local"):
            with self.subTest(sensitive_path=sensitive_path):
                self.assertIsNone(
                    re.search(module.EXCLUDED_FILES, sensitive_path),
                    "ordinary ignored files must remain inside --all-files coverage",
                )


if __name__ == "__main__":
    unittest.main()
