from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import result_cache
from opaihub.evidence_cache import (
    DEFAULT_FINGERPRINT_LIMITS,
    FingerprintLimits,
    assess_repo_fingerprint,
)


def make_git_repo(root: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=cache-tests@example.invalid",
            "-c",
            "user.name=OPai Cache Tests",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


class ContentAwareFingerprintTests(unittest.TestCase):
    def test_same_dirty_path_with_different_bytes_has_different_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            app = root / "app.py"
            app.write_text("value = 2\n", encoding="utf-8")
            first = assess_repo_fingerprint(root)
            app.write_text("value = 3\n", encoding="utf-8")
            second = assess_repo_fingerprint(root)

        self.assertTrue(first.cacheable)
        self.assertTrue(second.cacheable)
        self.assertNotEqual(first.digest, second.digest)

    def test_safe_untracked_bytes_participate_in_the_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            scratch = root / "scratch.py"
            scratch.write_text("value = 'first'\n", encoding="utf-8")
            first = assess_repo_fingerprint(root)
            scratch.write_text("value = 'second'\n", encoding="utf-8")
            second = assess_repo_fingerprint(root)

        self.assertTrue(first.cacheable)
        self.assertTrue(second.cacheable)
        self.assertNotEqual(first.digest, second.digest)

    def test_clean_git_fast_path_never_opens_repository_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            with mock.patch.object(
                Path,
                "open",
                side_effect=AssertionError("clean fingerprint must not read files"),
            ):
                assessment = assess_repo_fingerprint(root)

        self.assertTrue(assessment.cacheable)
        self.assertEqual(assessment.dirty_file_count, 0)
        self.assertEqual(assessment.dirty_bytes, 0)

    def test_dirty_file_count_limit_bypasses_without_a_digest_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            (root / "app.py").write_text("value = 2\n", encoding="utf-8")
            assessment = assess_repo_fingerprint(
                root,
                limits=FingerprintLimits(
                    max_dirty_files=0,
                    max_file_bytes=DEFAULT_FINGERPRINT_LIMITS.max_file_bytes,
                    max_total_bytes=DEFAULT_FINGERPRINT_LIMITS.max_total_bytes,
                ),
            )

        self.assertFalse(assessment.cacheable)
        self.assertEqual(assessment.bypass_reason, "too_many_dirty_files")

    def test_ignored_input_bypasses_reuse_instead_of_silently_excluding_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(
                Path(tmp),
                {".gitignore": "secret.local\n", "app.py": "value = 1\n"},
            )
            (root / "secret.local").write_text("value = secret\n", encoding="utf-8")
            assessment = assess_repo_fingerprint(root)

        self.assertFalse(assessment.cacheable)
        self.assertEqual(assessment.bypass_reason, "ignored_input")


class ResultCacheContentCorrectnessTests(unittest.TestCase):
    def test_same_dirty_path_with_new_bytes_cannot_reuse_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            app = root / "app.py"
            app.write_text("value = 2\n", encoding="utf-8")
            result_cache.store(root, "summarize app", "local-test", "first answer")
            app.write_text("value = 3\n", encoding="utf-8")
            reused = result_cache.lookup(root, "summarize app", "local-test")

        self.assertIsNone(reused)

    def test_untracked_content_change_cannot_reuse_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            scratch = root / "scratch.py"
            scratch.write_text("value = 'first'\n", encoding="utf-8")
            result_cache.store(root, "summarize app", "local-test", "first answer")
            scratch.write_text("value = 'second'\n", encoding="utf-8")
            reused = result_cache.lookup(root, "summarize app", "local-test")

        self.assertIsNone(reused)


if __name__ == "__main__":
    unittest.main()
