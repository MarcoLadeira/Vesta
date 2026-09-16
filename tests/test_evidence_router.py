import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from vestahub.evidence import collect_evidence
from vestahub.loader import registry_items
from vestahub.router import (
    MAX_CHANGED_FILES,
    MAX_COMPACT_ROUTE_CHARS,
    MAX_DIFF_STAT_LINES,
    _cap_lines,
    _compact_command,
    compact_decision,
    route_task,
)


class EvidenceRouterTests(unittest.TestCase):
    def test_collect_evidence_detects_project_markers_without_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname='demo'\n", encoding="utf-8"
            )
            (root / "tests").mkdir()

            evidence = collect_evidence(root, "fix tests")

        self.assertFalse(evidence["ai_used"])
        self.assertIn("pyproject.toml", evidence["markers"])
        self.assertIn("python -m unittest discover -s tests", evidence["test_commands"])
        self.assertIn("cache_key", evidence)

    def test_collect_evidence_skips_git_diff_when_project_is_not_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            evidence = collect_evidence(root, "inspect project")

        self.assertFalse(evidence["git"]["is_repo"])
        self.assertFalse(evidence["git"]["changed_files"]["executed"])
        self.assertEqual(
            evidence["git"]["changed_files"]["output_tail"],
            "skipped: not a git repository",
        )

    def test_route_failing_tests_uses_test_debug_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()

            decision = route_task(root, "fix failing tests in auth")

        self.assertEqual(decision["workflow"], "test_failure_debug")
        self.assertEqual(decision["model_tier"], "L0")
        self.assertFalse(decision["requires_confirmation"])
        self.assertIn("run targeted tests first", decision["next_actions"])

    def test_route_deploy_requires_confirmation_before_strong_ai_or_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = route_task(Path(tmp), "deploy production release")

        self.assertEqual(decision["workflow"], "release_prepare")
        self.assertEqual(decision["model_tier"], "L0")
        self.assertTrue(decision["requires_confirmation"])
        self.assertIn("ask before deploy/cloud action", decision["safety_gates"])
        self.assertIn("run release preflight locally", decision["next_actions"])

    def test_route_returns_compact_evidence_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# demo\n", encoding="utf-8")

            decision = route_task(root, "show git status")

        self.assertIn("evidence_summary", decision)
        self.assertNotIn("evidence", decision)
        self.assertLessEqual(
            len(decision["evidence_summary"]["git"]["status"]["output_tail"]), 360
        )

    def test_compact_decision_omits_verbose_evidence_for_ai_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n")

            compact = compact_decision(route_task(root, "fix failing tests"))

        self.assertNotIn("evidence", compact)
        self.assertIn("cache_key", compact)
        self.assertLess(len(str(compact)), 700)
        self.assertEqual(compact["output"], "compact")
        self.assertIn("full evidence", compact["hint"])

    def test_route_compact_evidence_redacts_secret_like_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret_name = "sk-testsecret1234567890.txt"
            (root / secret_name).write_text("redacted\n", encoding="utf-8")
            import subprocess

            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)

            decision = route_task(root, "show git status")

        output = decision["evidence_summary"]["git"]["status"]["output_tail"]
        self.assertNotIn(secret_name, output)
        self.assertIn("[REDACTED_SECRET]", output)

    def test_router_returns_registry_backed_workflows(self):
        workflow_ids = {
            workflow["id"] for workflow in registry_items("workflows", Path.cwd())
        }
        tasks = [
            "show git status",
            "fix failing tests",
            "security review",
            "deploy production release",
            "add a feature",
        ]

        for task in tasks:
            with self.subTest(task=task):
                decision = route_task(Path.cwd(), task)
                self.assertIn(decision["workflow"], workflow_ids)


class RouteRedactionHardeningTests(unittest.TestCase):
    """Issue #30: redaction and compacting hardening for route output."""

    # --- _cap_lines helper ---

    def test_cap_lines_keeps_first_n_lines(self):
        text = "\n".join(f"line{i}" for i in range(10))
        result = _cap_lines(text, 5)
        actual = [ln for ln in result.splitlines() if not ln.startswith("[+")]
        self.assertEqual(actual, [f"line{i}" for i in range(5)])
        self.assertIn("[+5 more lines omitted]", result)

    def test_cap_lines_no_notice_when_below_max(self):
        text = "a\nb\nc"
        result = _cap_lines(text, 10)
        self.assertNotIn("omitted", result)
        self.assertEqual(len([ln for ln in result.splitlines() if ln.strip()]), 3)

    def test_cap_lines_skips_blank_lines(self):
        text = "a\n\nb\n\nc"
        result = _cap_lines(text, 10)
        self.assertEqual(len([ln for ln in result.splitlines() if ln.strip()]), 3)

    def test_cap_lines_exact_max_no_notice(self):
        text = "\n".join(f"f{i}" for i in range(5))
        result = _cap_lines(text, 5)
        self.assertNotIn("omitted", result)

    # --- _compact_command redaction (defense-in-depth) ---

    def test_compact_command_redacts_sk_secret_in_tail(self):
        """_compact_command redacts secrets even when upstream redaction was bypassed."""
        raw = "output includes sk-verylongsecretkey123456789 here"
        result = {
            "output_tail": raw,
            "returncode": 0,
            "executed": True,
            "policy": "allow",
        }
        compact = _compact_command(result)
        self.assertNotIn("sk-verylongsecretkey123456789", compact["output_tail"])
        self.assertIn("[REDACTED", compact["output_tail"])

    def test_compact_command_redacts_api_key_in_tail(self):
        raw = "api_key: 'abcdefghijklmnopqrstuvwxyz1234' extra"
        result = {
            "output_tail": raw,
            "returncode": 0,
            "executed": True,
            "policy": "allow",
        }
        compact = _compact_command(result)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234", compact["output_tail"])

    def test_compact_command_redacts_bearer_token(self):
        raw = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9abcdefgh"
        result = {
            "output_tail": raw,
            "returncode": 0,
            "executed": True,
            "policy": "allow",
        }
        compact = _compact_command(result)
        self.assertNotIn(
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9abcdefgh", compact["output_tail"]
        )
        self.assertIn("[REDACTED]", compact["output_tail"])

    def test_compact_command_safe_output_unchanged(self):
        raw = "git status: clean working tree"
        result = {
            "output_tail": raw,
            "returncode": 0,
            "executed": True,
            "policy": "allow",
        }
        compact = _compact_command(result)
        self.assertIn("clean working tree", compact["output_tail"])

    # --- line count caps ---

    def test_compact_changed_files_capped_at_max(self):
        many_files = "\n".join(
            f"src/module_{i:04d}.py" for i in range(MAX_CHANGED_FILES + 30)
        )
        result = {
            "output_tail": many_files,
            "returncode": 0,
            "executed": True,
            "policy": "allow",
        }
        compact = _compact_command(result, max_lines=MAX_CHANGED_FILES)
        file_lines = [
            ln
            for ln in compact["output_tail"].splitlines()
            if ln.strip() and not ln.startswith("[+")
        ]
        self.assertLessEqual(len(file_lines), MAX_CHANGED_FILES)
        self.assertIn("more lines omitted", compact["output_tail"])

    def test_compact_diff_stat_capped_at_max(self):
        stat_lines = "\n".join(
            f" file_{i:04d}.py | {i + 1} {'+' * (i % 5 + 1)}"
            for i in range(MAX_DIFF_STAT_LINES + 10)
        )
        result = {
            "output_tail": stat_lines,
            "returncode": 0,
            "executed": True,
            "policy": "allow",
        }
        compact = _compact_command(result, max_lines=MAX_DIFF_STAT_LINES)
        stat_entries = [
            ln
            for ln in compact["output_tail"].splitlines()
            if ln.strip() and not ln.startswith("[+")
        ]
        self.assertLessEqual(len(stat_entries), MAX_DIFF_STAT_LINES)
        self.assertIn("more lines omitted", compact["output_tail"])

    def test_compact_file_list_not_truncated_when_short(self):
        few_files = "\n".join(f"file_{i}.py" for i in range(5))
        result = {
            "output_tail": few_files,
            "returncode": 0,
            "executed": True,
            "policy": "allow",
        }
        compact = _compact_command(result, max_lines=MAX_CHANGED_FILES)
        self.assertNotIn("omitted", compact["output_tail"])
        lines = [ln for ln in compact["output_tail"].splitlines() if ln.strip()]
        self.assertEqual(len(lines), 5)

    def test_changed_files_cap_applied_in_compact_evidence(self):
        """_compact_evidence passes max_lines=MAX_CHANGED_FILES for changed_files."""
        from vestahub.router import _compact_evidence

        many = "\n".join(f"file_{i}.py" for i in range(MAX_CHANGED_FILES + 50))
        evidence = {
            "cache_key": "abc",
            "ai_used": False,
            "markers": [],
            "languages": [],
            "test_commands": [],
            "registry_counts": {},
            "git": {
                "is_repo": True,
                "status": {
                    "output_tail": "## main",
                    "returncode": 0,
                    "executed": True,
                    "policy": "allow",
                },
                "changed_files": {
                    "output_tail": many,
                    "returncode": 0,
                    "executed": True,
                    "policy": "allow",
                },
                "diff_stat": {
                    "output_tail": "",
                    "returncode": 0,
                    "executed": True,
                    "policy": "allow",
                },
            },
        }
        compact = _compact_evidence(evidence)
        tail = compact["git"]["changed_files"]["output_tail"]
        file_lines = [
            ln for ln in tail.splitlines() if ln.strip() and not ln.startswith("[+")
        ]
        self.assertLessEqual(len(file_lines), MAX_CHANGED_FILES)

    def test_diff_stat_cap_applied_in_compact_evidence(self):
        """_compact_evidence passes max_lines=MAX_DIFF_STAT_LINES for diff_stat."""
        from vestahub.router import _compact_evidence

        many_stat = "\n".join(
            f" f_{i}.py | {i} +" for i in range(MAX_DIFF_STAT_LINES + 20)
        )
        evidence = {
            "cache_key": "abc",
            "ai_used": False,
            "markers": [],
            "languages": [],
            "test_commands": [],
            "registry_counts": {},
            "git": {
                "is_repo": True,
                "status": {
                    "output_tail": "",
                    "returncode": 0,
                    "executed": True,
                    "policy": "allow",
                },
                "changed_files": {
                    "output_tail": "",
                    "returncode": 0,
                    "executed": True,
                    "policy": "allow",
                },
                "diff_stat": {
                    "output_tail": many_stat,
                    "returncode": 0,
                    "executed": True,
                    "policy": "allow",
                },
            },
        }
        compact = _compact_evidence(evidence)
        tail = compact["git"]["diff_stat"]["output_tail"]
        stat_entries = [
            ln for ln in tail.splitlines() if ln.strip() and not ln.startswith("[+")
        ]
        self.assertLessEqual(len(stat_entries), MAX_DIFF_STAT_LINES)

    # --- route-size regression tests ---

    def _make_git_repo(self, root: Path, n_extra_files: int = 0) -> None:
        subprocess.run(["git", "init"], cwd=root, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@test.com"], cwd=root, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=root, capture_output=True
        )
        (root / "pyproject.toml").write_text(
            "[project]\nname='demo'\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "."], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True)
        for i in range(n_extra_files):
            (root / f"mod_{i:04d}.py").write_text(f"# {i}\n" * 3, encoding="utf-8")

    def test_default_route_below_char_budget_on_noisy_repo(self):
        """route_task() output stays below MAX_COMPACT_ROUTE_CHARS on a 200-file fixture."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_git_repo(root, n_extra_files=200)
            decision = route_task(root, "show git status", use_cache=False)

        chars = len(json.dumps(decision, default=str))
        self.assertLess(
            chars,
            MAX_COMPACT_ROUTE_CHARS,
            f"Route output exceeded budget: {chars} chars (limit: {MAX_COMPACT_ROUTE_CHARS})",
        )

    def test_default_route_below_budget_on_clean_repo(self):
        """route_task() stays well below budget even on an empty repo."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_git_repo(root)
            decision = route_task(root, "fix failing tests", use_cache=False)

        chars = len(json.dumps(decision, default=str))
        self.assertLess(chars, MAX_COMPACT_ROUTE_CHARS)

    def test_compact_decision_always_tiny(self):
        """compact_decision() output is always far below 2000 chars."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_git_repo(root, n_extra_files=200)
            full = route_task(
                root, "show git status", include_evidence=True, use_cache=False
            )
            compact = compact_decision(full)

        chars = len(json.dumps(compact, default=str))
        self.assertLess(chars, 2_000)

    # --- full-evidence redaction tests ---

    def test_full_evidence_redacts_sk_secret_in_filename(self):
        """include_evidence=True never leaks sk-… patterns from filenames in git output."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, capture_output=True)
            secret_name = "sk-secretkey123456789abcdef.py"
            (root / secret_name).write_text("pass\n", encoding="utf-8")

            decision = route_task(
                root, "show git status", include_evidence=True, use_cache=False
            )

        serialized = json.dumps(decision, default=str)
        self.assertNotIn("sk-secretkey123456789abcdef", serialized)

    def test_full_evidence_redacts_token_in_status_output(self):
        """Full evidence redacts token=… patterns appearing in git output."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, capture_output=True)
            # Use a filename that triggers the token pattern
            fname = "token=abcdefghij1234567890xyz.env"
            (root / fname).write_text("secret\n", encoding="utf-8")

            decision = route_task(
                root, "show git status", include_evidence=True, use_cache=False
            )

        serialized = json.dumps(decision, default=str)
        self.assertNotIn("abcdefghij1234567890xyz", serialized)

    def test_full_evidence_size_with_many_files_is_bounded(self):
        """include_evidence=True still applies compact evidence caps in the summary."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_git_repo(root, n_extra_files=200)
            decision = route_task(
                root, "show git status", include_evidence=True, use_cache=False
            )

        summary_chars = len(json.dumps(decision["evidence_summary"], default=str))
        self.assertLess(summary_chars, MAX_COMPACT_ROUTE_CHARS)


if __name__ == "__main__":
    unittest.main()
