from __future__ import annotations

import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest import mock

from opaihub import result_cache
from opaihub.evidence_cache import (
    DEFAULT_FINGERPRINT_LIMITS,
    FingerprintLimits,
    RepoFingerprint,
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


class ResultCacheEnvelopeTests(unittest.TestCase):
    def test_entry_expires_at_the_documented_lifetime(self):
        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            result_cache.store(
                root,
                "summarize app",
                "local-test",
                "fresh answer",
                now=now,
            )
            fresh = result_cache.lookup_with_meta(
                root,
                "summarize app",
                "local-test",
                now=now + timedelta(seconds=result_cache.DEFAULT_TTL_SECONDS - 1),
            )
            expired = result_cache.lookup_with_meta(
                root,
                "summarize app",
                "local-test",
                now=now + timedelta(seconds=result_cache.DEFAULT_TTL_SECONDS),
            )

        self.assertEqual(fresh.outcome, "hit")
        self.assertEqual(fresh.entry["answer"], "fresh answer")
        self.assertEqual(expired.outcome, "expired")
        self.assertIsNone(expired.entry)

    def test_schema_incompatible_and_corrupt_entries_are_never_reused(self):
        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            path = result_cache.store(
                root,
                "summarize app",
                "local-test",
                "fresh answer",
                now=now,
            )
            self.assertIsNotNone(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema_version"] = result_cache.RESULT_CACHE_VERSION - 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            incompatible = result_cache.lookup_with_meta(
                root, "summarize app", "local-test", now=now
            )
            path.write_text("{broken", encoding="utf-8")
            corrupt = result_cache.lookup_with_meta(
                root, "summarize app", "local-test", now=now
            )

        self.assertEqual(incompatible.outcome, "schema_mismatch")
        self.assertIsNone(incompatible.entry)
        self.assertEqual(corrupt.outcome, "corrupt")
        self.assertIsNone(corrupt.entry)

    def test_binary_input_bypasses_result_cache_without_creating_an_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            (root / "payload.bin").write_bytes(b"\x00binary")
            path = result_cache.store(root, "summarize app", "local-test", "answer")
            lookup = result_cache.lookup_with_meta(root, "summarize app", "local-test")
            answers = root / ".opaihub" / "answers"

        self.assertIsNone(path)
        self.assertEqual(lookup.outcome, "bypass")
        self.assertEqual(lookup.reason, "binary_file")
        self.assertFalse(answers.exists())

    def test_atomic_writes_never_expose_partial_entries_to_readers(self):
        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            result_cache.store(root, "summarize app", "local-test", "seed", now=now)

            def write_answer(index: int) -> None:
                for count in range(12):
                    result_cache.store(
                        root,
                        "summarize app",
                        "local-test",
                        f"writer-{index}-{count}",
                        now=now,
                    )

            def read_answers() -> list[str]:
                answers: list[str] = []
                for _ in range(24):
                    lookup = result_cache.lookup_with_meta(
                        root, "summarize app", "local-test", now=now
                    )
                    self.assertEqual(lookup.outcome, "hit")
                    self.assertIsNotNone(lookup.entry)
                    self.assertEqual(
                        lookup.entry["schema_version"],
                        result_cache.RESULT_CACHE_VERSION,
                    )
                    answers.append(lookup.entry["answer"])
                return answers

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(write_answer, 1),
                    executor.submit(write_answer, 2),
                    executor.submit(read_answers),
                    executor.submit(read_answers),
                ]
                results = [future.result() for future in futures]

        observed = [answer for result in results if result for answer in result]
        self.assertTrue(observed)
        self.assertTrue(
            all(answer == "seed" or answer.startswith("writer-") for answer in observed)
        )

    def test_untracked_content_change_cannot_reuse_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(Path(tmp), {"app.py": "value = 1\n"})
            scratch = root / "scratch.py"
            scratch.write_text("value = 'first'\n", encoding="utf-8")
            result_cache.store(root, "summarize app", "local-test", "first answer")
            scratch.write_text("value = 'second'\n", encoding="utf-8")
            reused = result_cache.lookup(root, "summarize app", "local-test")

        self.assertIsNone(reused)


class RepositoryWorkCacheTests(unittest.TestCase):
    def test_uncacheable_assessment_never_reuses_gui_repository_work(self):
        from opaihub.intent_router import _cached_repo_work, clear_repo_work_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = 0

            def compute() -> dict[str, int]:
                nonlocal calls
                calls += 1
                return {"calls": calls}

            unsafe = RepoFingerprint(
                digest="unsafe",
                cacheable=False,
                bypass_reason="binary_file",
                dirty_file_count=1,
                dirty_bytes=0,
            )
            clear_repo_work_cache()
            with mock.patch(
                "opaihub.evidence_cache.assess_repo_fingerprint",
                return_value=unsafe,
            ):
                first = _cached_repo_work(root, "context", compute)
                second = _cached_repo_work(root, "context", compute)
            clear_repo_work_cache()

        self.assertEqual(first, {"calls": 1})
        self.assertEqual(second, {"calls": 2})


if __name__ == "__main__":
    unittest.main()
