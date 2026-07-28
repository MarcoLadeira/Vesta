"""Tests for OPai self-update (check_for_update / apply_update).

Every git and pip interaction is injected so these tests never touch a real
repository, the network, or the developer's actual ``~/.opai`` cache.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from opai import updater


class _Root:
    """A throwaway project root + cache path per test."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        return root, root / "cache" / "update_check.json"

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fake_git(
    responses: dict[tuple, "subprocess.CompletedProcess[str]"], default_ok: bool = True
):
    """Builds a GitRunner that answers from ``responses`` keyed by the args tuple."""

    def runner(root: Path, args) -> "subprocess.CompletedProcess[str]":
        key = tuple(args)
        if key in responses:
            return responses[key]
        # rev-parse/remote checks default to "yes, a normal git checkout" unless overridden.
        if key == ("rev-parse", "--is-inside-work-tree"):
            return _completed(0, "true\n") if default_ok else _completed(1)
        if key == ("remote", "get-url", "origin"):
            return (
                _completed(0, "https://github.com/MarcoLadeira/OPai.git\n")
                if default_ok
                else _completed(1)
            )
        if key == ("status", "--porcelain"):
            return _completed(0, "")
        return _completed(0, "")

    return runner


class CheckForUpdateTests(unittest.TestCase):
    def test_up_to_date_reports_zero_behind(self):
        with _Root() as (root, cache_path):
            git = _fake_git(
                {
                    ("fetch", "--quiet", "origin", "main"): _completed(0),
                    ("rev-list", "--count", "HEAD..origin/main"): _completed(0, "0\n"),
                    ("show", "origin/main:opai/__init__.py"): _completed(
                        0, '__version__ = "0.2.1a1"\n'
                    ),
                }
            )
            result = updater.check_for_update(root, git=git, cache_path=cache_path)
            self.assertTrue(result["checked"])
            self.assertTrue(result["up_to_date"])
            self.assertEqual(result["commits_behind"], 0)
            self.assertEqual(result["latest_version"], "0.2.1a1")

    def test_behind_reports_commit_count_and_latest_version(self):
        with _Root() as (root, cache_path):
            git = _fake_git(
                {
                    ("fetch", "--quiet", "origin", "main"): _completed(0),
                    ("rev-list", "--count", "HEAD..origin/main"): _completed(0, "4\n"),
                    ("show", "origin/main:opai/__init__.py"): _completed(
                        0, '__version__ = "0.3.0"\n'
                    ),
                }
            )
            result = updater.check_for_update(root, git=git, cache_path=cache_path)
            self.assertFalse(result["up_to_date"])
            self.assertEqual(result["commits_behind"], 4)
            self.assertEqual(result["latest_version"], "0.3.0")

    def test_not_a_git_checkout_is_reported_honestly_not_raised(self):
        with _Root() as (root, cache_path):
            git = _fake_git({}, default_ok=False)
            result = updater.check_for_update(root, git=git, cache_path=cache_path)
            self.assertFalse(result["checked"])
            self.assertTrue(result["up_to_date"])  # never nags when we can't tell
            self.assertIn("git checkout", result["reason"])

    def test_offline_fetch_failure_is_soft(self):
        with _Root() as (root, cache_path):
            git = _fake_git(
                {
                    ("fetch", "--quiet", "origin", "main"): _completed(
                        1, "", "network unreachable"
                    )
                }
            )
            result = updater.check_for_update(root, git=git, cache_path=cache_path)
            self.assertFalse(result["checked"])
            self.assertIn("offline", result["reason"].lower())

    def test_git_timeout_is_soft_not_raised(self):
        with _Root() as (root, cache_path):

            def timing_out(root_arg, args):
                if tuple(args) == ("fetch", "--quiet", "origin", "main"):
                    raise subprocess.TimeoutExpired(cmd="git", timeout=8.0)
                return _fake_git({})(root_arg, args)

            result = updater.check_for_update(
                root, git=timing_out, cache_path=cache_path
            )
            self.assertFalse(result["checked"])
            self.assertIn("timed out", result["reason"].lower())

    def test_result_is_cached_and_reused_within_ttl(self):
        with _Root() as (root, cache_path):
            calls = {"n": 0}

            def counting_git(root_arg, args):
                calls["n"] += 1
                return _fake_git(
                    {
                        ("fetch", "--quiet", "origin", "main"): _completed(0),
                        ("rev-list", "--count", "HEAD..origin/main"): _completed(
                            0, "0\n"
                        ),
                    }
                )(root_arg, args)

            first = updater.check_for_update(
                root, git=counting_git, cache_path=cache_path
            )
            calls_after_first = calls["n"]
            second = updater.check_for_update(
                root, git=counting_git, cache_path=cache_path
            )
            self.assertEqual(
                calls["n"], calls_after_first
            )  # no new git calls — served from cache
            self.assertEqual(first["checked_at"], second["checked_at"])

    def test_force_bypasses_the_cache(self):
        with _Root() as (root, cache_path):
            git = _fake_git(
                {
                    ("fetch", "--quiet", "origin", "main"): _completed(0),
                    ("rev-list", "--count", "HEAD..origin/main"): _completed(0, "0\n"),
                }
            )
            first = updater.check_for_update(root, git=git, cache_path=cache_path)
            time.sleep(0.01)
            second = updater.check_for_update(
                root, git=git, force=True, cache_path=cache_path
            )
            self.assertGreater(second["checked_at"], first["checked_at"])


class ApplyUpdateTests(unittest.TestCase):
    def test_refuses_on_dirty_working_tree(self):
        with _Root() as (root, cache_path):
            git = _fake_git(
                {("status", "--porcelain"): _completed(0, "M some/file.py\n")}
            )
            result = updater.apply_update(root, git=git, cache_path=cache_path)
            self.assertFalse(result["ok"])
            self.assertIn("uncommitted", result["error"].lower())

    def test_refuses_when_not_a_git_checkout(self):
        with _Root() as (root, cache_path):
            git = _fake_git({}, default_ok=False)
            result = updater.apply_update(root, git=git, cache_path=cache_path)
            self.assertFalse(result["ok"])
            self.assertIn("git checkout", result["error"])

    def test_successful_update_fetches_checks_out_and_reinstalls(self):
        with _Root() as (root, cache_path):
            (root / "opai").mkdir()
            (root / "opai" / "__init__.py").write_text(
                '__version__ = "0.3.0"\n', encoding="utf-8"
            )
            calls: list[tuple] = []

            def recording_git(root_arg, args):
                calls.append(tuple(args))
                return _fake_git(
                    {
                        ("fetch", "--quiet", "origin", "main"): _completed(0),
                        ("checkout", "main"): _completed(0),
                        ("merge", "--ff-only", "origin/main"): _completed(0),
                    }
                )(root_arg, args)

            def fake_pip_install(root_arg):
                return _completed(0)

            result = updater.apply_update(
                root,
                git=recording_git,
                pip_install=fake_pip_install,
                cache_path=cache_path,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["restart_required"])
            self.assertEqual(result["installed_version"], "0.3.0")
            self.assertIn(("fetch", "--quiet", "origin", "main"), calls)
            self.assertIn(("checkout", "main"), calls)
            self.assertIn(("merge", "--ff-only", "origin/main"), calls)

    def test_merge_conflict_is_reported_not_raised(self):
        with _Root() as (root, cache_path):
            git = _fake_git(
                {
                    ("fetch", "--quiet", "origin", "main"): _completed(0),
                    ("checkout", "main"): _completed(0),
                    ("merge", "--ff-only", "origin/main"): _completed(
                        1, "", "diverged"
                    ),
                }
            )
            result = updater.apply_update(root, git=git, cache_path=cache_path)
            self.assertFalse(result["ok"])
            self.assertIn("diverged", result["error"].lower())

    def test_pip_install_failure_reports_code_updated_but_not_ok(self):
        with _Root() as (root, cache_path):
            (root / "opai").mkdir()
            (root / "opai" / "__init__.py").write_text(
                '__version__ = "0.3.0"\n', encoding="utf-8"
            )
            git = _fake_git(
                {
                    ("fetch", "--quiet", "origin", "main"): _completed(0),
                    ("checkout", "main"): _completed(0),
                    ("merge", "--ff-only", "origin/main"): _completed(0),
                }
            )
            result = updater.apply_update(
                root,
                git=git,
                pip_install=lambda r: _completed(1, "", "boom"),
                cache_path=cache_path,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result["code_updated"])


if __name__ == "__main__":
    unittest.main()
