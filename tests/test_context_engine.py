import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opaihub import atomic_io, context_engine
from opaihub.atomic_io import InterprocessLockTimeout
from opaihub.context_engine import (
    CLIENT_IGNORE_FILES,
    generate_client_ignores,
    profile_context,
    render_profile_markdown,
)


def _make(root: Path, rel: str, size: int) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)


class ProfileTests(unittest.TestCase):
    def test_detects_dependency_build_cache_binary_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, "src/app.py", 100)
            _make(root, "node_modules/lib/x.js", 50_000)
            _make(root, "build/out.o", 40_000)
            _make(root, ".pytest_cache/c", 10_000)
            _make(root, "assets/big.png", 300_000)
            _make(root, "app.log", 20_000)
            profile = profile_context(root)
        cats = profile["by_category"]
        self.assertIn("dependency", cats)
        self.assertIn("build", cats)
        self.assertIn("cache", cats)
        self.assertIn("binary", cats)
        self.assertIn("log", cats)
        self.assertGreater(profile["waste_bytes"], 0)
        self.assertIn("OPai Context Profile", render_profile_markdown(profile))

    def test_top_sources_ranked_by_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, "small.png", 1000)
            _make(root, "huge.zip", 500_000)
            profile = profile_context(root)
        self.assertEqual(profile["top_sources"][0]["path"], "huge.zip")

    def test_before_after_reduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, "code.py", 10_000)
            _make(root, "node_modules/big.js", 90_000)
            profile = profile_context(root)
        ba = profile["before_after"]
        self.assertGreater(ba["before"]["bytes"], ba["after"]["bytes"])
        self.assertGreater(ba["reduction"]["percent"], 0)

    def test_monorepo_nested_node_modules_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, "packages/a/node_modules/x.js", 30_000)
            _make(root, "packages/b/node_modules/y.js", 30_000)
            _make(root, "packages/a/src/app.ts", 200)
            profile = profile_context(root)
        self.assertEqual(profile["by_category"].get("dependency"), 60_000)

    def test_profile_streams_without_materializing_a_recursive_path_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, "node_modules/package/lib.js", 100)
            with mock.patch.object(
                Path,
                "rglob",
                side_effect=AssertionError("profile must stream its directory walk"),
            ):
                profile_context(root)


class IgnoreGenerationTests(unittest.TestCase):
    def test_creates_all_four_client_ignores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = generate_client_ignores(
                root, ["cursor", "claude", "copilot", "cline"]
            )
            for name in [
                ".cursorignore",
                ".claudeignore",
                ".copilotignore",
                ".clineignore",
            ]:
                self.assertTrue((root / name).exists(), name)
        statuses = {r["client"]: r["status"] for r in result["results"]}
        self.assertEqual(statuses["copilot"], "created")
        self.assertEqual(statuses["cline"], "created")

    def test_preserves_user_authored_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".cursorignore").write_text("# mine\nsecrets/\n", encoding="utf-8")
            generate_client_ignores(root, ["cursor"])
            text = (root / ".cursorignore").read_text(encoding="utf-8")
        self.assertIn("secrets/", text)
        self.assertIn("OPai context-slimming", text)

    def test_idempotent_does_not_duplicate_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generate_client_ignores(root, ["cursor"])
            second = generate_client_ignores(root, ["cursor"])
            text = (root / ".cursorignore").read_text(encoding="utf-8")
        self.assertEqual(second["results"][0]["status"], "already_managed")
        self.assertEqual(text.count("OPai context-slimming rules (managed)"), 1)


class ConcurrentEditorTests(unittest.TestCase):
    """A user's editor may write the same ignore file while OPai is merging it."""

    def _editing_merge(self, path: Path, edits: list[str]):
        """Merge hook that lets an outside editor write between our read and publish."""
        real_merge = context_engine._merge_ignore_text

        def merge(existing: str) -> str:
            if edits:
                path.write_text(edits.pop(0), encoding="utf-8")
            return real_merge(existing)

        return merge

    def test_editor_write_between_read_and_publish_is_remerged(self):
        for client, name in CLIENT_IGNORE_FILES.items():
            with self.subTest(client=client), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / name
                path.write_text("# mine\nsecrets/\n", encoding="utf-8")
                merge = self._editing_merge(path, ["# mine\nsecrets/\nlate-rule/\n"])
                with mock.patch.object(context_engine, "_merge_ignore_text", merge):
                    result = generate_client_ignores(root, [client])
                text = path.read_text(encoding="utf-8")
                self.assertEqual(result["results"][0]["status"], "updated")
                self.assertIn("secrets/", text)
                self.assertIn("late-rule/", text)
                self.assertEqual(text.count(context_engine._MANAGED_START), 1)

    def test_relentless_editor_reports_conflict_without_discarding_rules(self):
        for client, name in CLIENT_IGNORE_FILES.items():
            with self.subTest(client=client), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / name
                path.write_text("# mine\nsecrets/\n", encoding="utf-8")
                edits = [f"# mine\nsecrets/\nedit-{i}/\n" for i in range(10)]
                merge = self._editing_merge(path, edits)
                with mock.patch.object(context_engine, "_merge_ignore_text", merge):
                    result = generate_client_ignores(root, [client])
                text = path.read_text(encoding="utf-8")
                entry = result["results"][0]
                self.assertEqual(entry["status"], "conflict")
                self.assertIn("secrets/", text)
                self.assertNotIn(context_engine._MANAGED_START, text)
                self.assertEqual(len(edits), 10 - context_engine._PUBLISH_ATTEMPTS)

    def test_concurrent_generators_write_one_managed_block(self):
        for client, name in CLIENT_IGNORE_FILES.items():
            with self.subTest(client=client), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / name
                path.write_text("# mine\nsecrets/\n", encoding="utf-8")
                start = threading.Barrier(6)
                statuses: list[str] = []
                lock = threading.Lock()

                def run() -> None:
                    start.wait(timeout=30)
                    result = generate_client_ignores(root, [client])
                    with lock:
                        statuses.append(result["results"][0]["status"])

                threads = [threading.Thread(target=run) for _ in range(6)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=30)
                    self.assertFalse(thread.is_alive())

                text = path.read_text(encoding="utf-8")
                self.assertIn("secrets/", text)
                self.assertEqual(text.count(context_engine._MANAGED_START), 1)
                self.assertEqual(statuses.count("updated"), 1)
                self.assertEqual(statuses.count("already_managed"), 5)


def _raise_lock_timeout(*args: object, **kwargs: object):
    raise InterprocessLockTimeout("timed out waiting for interprocess transaction lock")


class IgnoreFailureInjectionTests(unittest.TestCase):
    def test_failed_publish_leaves_the_user_file_untouched(self):
        for client, name in CLIENT_IGNORE_FILES.items():
            with self.subTest(client=client), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / name
                original = "# mine\nsecrets/\n"
                path.write_text(original, encoding="utf-8")
                # Fail where a crash would hurt most: after the replacement content
                # is written, at the moment of publishing it.
                with mock.patch.object(
                    atomic_io,
                    "_replace_with_retry",
                    side_effect=OSError("disk full"),
                ):
                    result = generate_client_ignores(root, [client])
                entry = result["results"][0]
                self.assertEqual(entry["status"], "failed")
                self.assertIn("disk full", entry["error"])
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                self.assertEqual(list(root.glob(f".{name}.*.tmp")), [])

    def test_undecodable_ignore_file_is_reported_not_rewritten(self):
        for client, name in CLIENT_IGNORE_FILES.items():
            with self.subTest(client=client), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / name
                original = b"\xff\xfe not utf-8 \x00"
                path.write_bytes(original)
                result = generate_client_ignores(root, [client])
                self.assertEqual(result["results"][0]["status"], "failed")
                self.assertEqual(path.read_bytes(), original)

    def test_one_failing_client_does_not_block_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clients = list(CLIENT_IGNORE_FILES)
            failing = clients[0]
            real_write = context_engine.atomic_write_text

            def write(path: Path, text: str, **kwargs: object) -> None:
                if Path(path).name == CLIENT_IGNORE_FILES[failing]:
                    raise OSError("read-only file system")
                real_write(path, text, **kwargs)

            with mock.patch.object(context_engine, "atomic_write_text", write):
                result = generate_client_ignores(root, clients)

            statuses = {r["client"]: r["status"] for r in result["results"]}
            self.assertEqual(statuses.pop(failing), "failed")
            self.assertEqual(set(statuses.values()), {"created"})
            self.assertFalse((root / CLIENT_IGNORE_FILES[failing]).exists())

    def test_lock_timeout_is_reported_per_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                context_engine,
                "interprocess_transaction",
                side_effect=_raise_lock_timeout,
            ):
                result = generate_client_ignores(root, ["cursor"])
        entry = result["results"][0]
        self.assertEqual(entry["status"], "failed")
        self.assertFalse((root / ".cursorignore").exists())


class IgnorePermissionTests(unittest.TestCase):
    def test_new_ignore_file_matches_a_plain_write(self):
        for client, name in CLIENT_IGNORE_FILES.items():
            with self.subTest(client=client), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                reference = root / "reference.txt"
                reference.write_text("x", encoding="utf-8")
                generate_client_ignores(root, [client])
                self.assertEqual(
                    stat.S_IMODE((root / name).stat().st_mode),
                    stat.S_IMODE(reference.stat().st_mode),
                )

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_existing_permissions_are_preserved(self):
        for client, name in CLIENT_IGNORE_FILES.items():
            with self.subTest(client=client), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / name
                path.write_text("secrets/\n", encoding="utf-8")
                path.chmod(0o640)
                generate_client_ignores(root, [client])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)


class IgnoreLockPlacementTests(unittest.TestCase):
    def test_lock_files_stay_out_of_the_users_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generate_client_ignores(root)
            self.assertEqual(sorted(p.name for p in root.glob("*.lock")), [])
            self.assertTrue((root / ".opaihub" / "locks").is_dir())


if __name__ == "__main__":
    unittest.main()
