"""Cross-process persistence regressions for resumable GUI threads (#313)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from opai import gui_recents


_CHILD = r"""
import sys
import time
from pathlib import Path

from opai.gui_recents import begin_thread_turn

root = Path(sys.argv[1])
barrier = Path(sys.argv[2])
worker = sys.argv[3]
(barrier / f"{worker}.ready").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 20
while not (barrier / "go").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("timed out waiting for multiprocess test barrier")
    time.sleep(0.01)
begin_thread_turn(
    root,
    request_id=f"request-{worker}",
    text=f"worker-{worker}|" + ("x" * 4_000),
    mode="safe-auto",
)
"""


class GuiThreadMultiprocessTests(unittest.TestCase):
    def test_concurrent_turns_are_one_atomic_load_modify_save_transaction(self) -> None:
        workers = 8
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            barrier = base / "barrier"
            root.mkdir()
            barrier.mkdir()
            processes = [
                subprocess.Popen(  # nosec B603 - fixed hermetic Python argv
                    [sys.executable, "-c", _CHILD, str(root), str(barrier), str(index)],
                    cwd=Path(__file__).resolve().parents[1],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index in range(workers)
            ]
            try:
                deadline = time.monotonic() + 20
                while len(list(barrier.glob("*.ready"))) < workers:
                    if time.monotonic() >= deadline:
                        self.fail("worker processes did not reach the start barrier")
                    time.sleep(0.01)
                (barrier / "go").write_text("go", encoding="utf-8")
                completed = [process.communicate(timeout=30) for process in processes]
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)

            failures = [
                {
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                }
                for process, (stdout, stderr) in zip(processes, completed, strict=True)
                if process.returncode
            ]
            restored = gui_recents.load_thread(root)
            target = gui_recents.thread_path(root)
            raw = json.loads(target.read_text(encoding="utf-8"))
            persisted_workers = {
                message["text"].split("|", 1)[0]
                for message in restored.get("messages") or ()
                if message.get("role") == "user"
            }

            self.assertEqual(failures, [])
            self.assertEqual(
                persisted_workers,
                {f"worker-{index}" for index in range(workers)},
            )
            self.assertEqual(raw["messages"], restored["messages"])
            self.assertFalse(target.with_suffix(".tmp").exists())
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
