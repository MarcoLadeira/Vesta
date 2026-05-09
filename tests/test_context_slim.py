import tempfile
import unittest
from pathlib import Path

from opai.context_slim import (
    clean_generated_context,
    context_bloat_report,
    write_ai_ignore_files,
)


class ContextSlimTests(unittest.TestCase):
    def test_write_ai_ignore_files_covers_generated_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claudeignore").write_text("custom-cache/\n", encoding="utf-8")

            written = write_ai_ignore_files(root)
            write_ai_ignore_files(root)

            text = (root / ".claudeignore").read_text(encoding="utf-8")
            self.assertIn("custom-cache/", text)
            self.assertIn(".opcoding-tools/", text)
            self.assertIn(".opaihub/install-test-*/", text)
            self.assertIn("**/node_modules/", text)
            self.assertEqual(text.count(".opcoding-tools/"), 1)
            self.assertTrue((root / ".opaiignore").exists())
            self.assertTrue(any(Path(path).name == ".claudeignore" for path in written))

    def test_context_bloat_report_and_clean_preserve_project_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".opcoding-tools" / "node").mkdir(parents=True)
            (root / ".opcoding-tools" / "node" / "large.txt").write_bytes(
                b"x" * (1024 * 1024)
            )
            (root / ".opaihub" / "install-test-abc").mkdir(parents=True)
            (root / ".opaihub" / "install-test-abc" / "venv.txt").write_bytes(
                b"x" * (1024 * 1024)
            )
            (root / ".opaihub" / "project.json").write_text("{}", encoding="utf-8")

            before = context_bloat_report(root)
            result = clean_generated_context(root, dry_run=False)

            self.assertGreater(before["total_generated_mb"], 0)
            self.assertGreater(result["total_mb"], 0)
            self.assertFalse((root / ".opcoding-tools").exists())
            self.assertFalse((root / ".opaihub" / "install-test-abc").exists())
            self.assertTrue((root / ".opaihub" / "project.json").exists())


if __name__ == "__main__":
    unittest.main()
