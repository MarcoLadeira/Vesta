import subprocess
import tempfile
import unittest
from pathlib import Path

from opaihub.evidence_cache import (
    cache_path,
    collect_evidence_cached,
    evidence_cache_key,
    repo_fingerprint,
)
from opaihub.router import route_task


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


class EvidenceCacheTests(unittest.TestCase):
    def test_read_is_side_effect_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_repo(root)
            _evidence, meta = collect_evidence_cached(root, "fix bug", write=False)
            self.assertFalse(meta["cache_hit"])
            self.assertFalse(cache_path(root, meta["cache_key"]).exists())

    def test_write_then_hit_same_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_repo(root)
            _e1, m1 = collect_evidence_cached(root, "fix bug", write=True)
            _e2, m2 = collect_evidence_cached(root, "fix bug", write=True)
        self.assertFalse(m1["cache_hit"])
        self.assertTrue(m2["cache_hit"])
        self.assertEqual(m1["cache_key"], m2["cache_key"])

    def test_repo_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_repo(root)
            _e1, m1 = collect_evidence_cached(root, "fix bug", write=True)
            (root / "new_module.py").write_text("x = 1\n", encoding="utf-8")
            _e2, m2 = collect_evidence_cached(root, "fix bug", write=False)
        self.assertFalse(m2["cache_hit"])
        self.assertNotEqual(m1["cache_key"], m2["cache_key"])

    def test_different_task_is_a_different_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_repo(root)
            self.assertNotEqual(
                evidence_cache_key(root, "fix bug"),
                evidence_cache_key(root, "add feature"),
            )

    def test_opai_state_dir_does_not_invalidate(self):
        # Writing the cache (under .opaihub) must not change the fingerprint.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_repo(root)
            before = repo_fingerprint(root)
            collect_evidence_cached(root, "fix bug", write=True)
            after = repo_fingerprint(root)
        self.assertEqual(before, after)

    def test_ttl_expiry_forces_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_repo(root)
            collect_evidence_cached(root, "fix bug", write=True)
            _e, meta = collect_evidence_cached(
                root, "fix bug", write=False, ttl_seconds=0
            )
        self.assertFalse(meta["cache_hit"])

    def test_non_git_project_bypasses_reuse_without_writing_a_cache_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            _e1, m1 = collect_evidence_cached(root, "fix bug", write=True)
            _e2, m2 = collect_evidence_cached(root, "fix bug", write=True)
            cache_root = root / ".opaihub" / "cache" / "evidence"
        self.assertFalse(m1["cache_hit"])
        self.assertFalse(m2["cache_hit"])
        self.assertTrue(m2["cache_bypassed"])
        self.assertEqual(m2["cache_bypass_reason"], "no_git_repository")
        self.assertFalse(cache_root.exists())


class RouterCacheIntegrationTests(unittest.TestCase):
    def test_route_is_read_only_and_does_not_persist_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_repo(root)
            decision = route_task(root, "fix bug")  # default: read-only
            self.assertIn("evidence_cache_hit", decision)
            self.assertFalse((root / ".opaihub" / "cache").exists())

    def test_persist_cache_then_route_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_repo(root)
            route_task(root, "fix bug", persist_cache=True)
            decision = route_task(root, "fix bug")  # read-only, should hit
        self.assertTrue(decision["evidence_cache_hit"])


if __name__ == "__main__":
    unittest.main()
