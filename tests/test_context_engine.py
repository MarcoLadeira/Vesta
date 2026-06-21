import tempfile
import unittest
from pathlib import Path

from opaihub.context_engine import (
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


if __name__ == "__main__":
    unittest.main()
