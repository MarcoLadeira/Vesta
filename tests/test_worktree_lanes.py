from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from opai.cli import main


def _repo(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


class WorktreeLaneTests(unittest.TestCase):
    def test_lane_status_is_read_only_and_recommends_safe_lane(self):
        from opaihub.worktree_lanes import lane_status

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            before = sorted(p.name for p in root.iterdir())
            status = lane_status(root)
            after = sorted(p.name for p in root.iterdir())

        self.assertEqual(before, after)
        self.assertEqual(
            [lane["id"] for lane in status["lanes"]],
            ["local", "worktree", "review"],
        )
        self.assertIn(status["recommended_lane"], {"local", "worktree", "review"})
        worktree = next(lane for lane in status["lanes"] if lane["id"] == "worktree")
        self.assertTrue(worktree["create_requires_confirmation"])

    def test_cli_lanes_status_returns_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["lanes", "status", "--project", str(root)])
            data = json.loads(buf.getvalue())

        self.assertEqual(code, 0)
        self.assertIn("lanes", data)
        self.assertIn("recommended_lane", data)


if __name__ == "__main__":
    unittest.main()
