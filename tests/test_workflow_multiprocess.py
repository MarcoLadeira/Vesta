"""Cross-process persistence regressions for GUI workflow state (#313).

The thread store gained OS advisory locking; workflow.json is written by every
turn in every window and needs the same guarantee: concurrent windows must
never make a save raise (Windows replace-while-open), tear the file, or make a
reader observe transient empty state.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from opaihub import workflow_state


_CHILD = r"""
import sys
import time
from dataclasses import replace
from pathlib import Path

from opaihub.workflow_state import (
    WorkflowState,
    load_workflow_state,
    save_workflow_state,
)

root = Path(sys.argv[1])
barrier = Path(sys.argv[2])
worker = sys.argv[3]
(barrier / f"{worker}.ready").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 20
while not (barrier / "go").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("timed out waiting for multiprocess test barrier")
    time.sleep(0.01)
for cycle in range(25):
    state = load_workflow_state(root)
    if not isinstance(state, WorkflowState):
        raise TypeError("load returned a non-state")
    save_workflow_state(
        root,
        replace(state, message=f"worker-{worker}-cycle-{cycle}", phase="implementing"),
    )
    # Interleave a bare read: it must never see a torn or vanished file as a
    # silent empty default while writers are active.
    load_workflow_state(root)
"""


class WorkflowMultiprocessTests(unittest.TestCase):
    def test_concurrent_windows_never_fail_or_tear_workflow_state(self) -> None:
        workers = 6
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            barrier = base / "barrier"
            root.mkdir()
            barrier.mkdir()
            # Seed a real file so every worker starts from load-modify-save.
            workflow_state.save_workflow_state(
                root, workflow_state.WorkflowState(message="seed")
            )
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
                completed = [process.communicate(timeout=60) for process in processes]
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)

            failures = [
                {
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr[-500:],
                }
                for process, (stdout, stderr) in zip(processes, completed, strict=True)
                if process.returncode
            ]
            self.assertEqual(failures, [])

            target = workflow_state.workflow_state_path(root)
            raw = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(raw["phase"], "implementing")
            self.assertIn("worker-", raw["message"])
            restored = workflow_state.load_workflow_state(root)
            self.assertEqual(restored.message, raw["message"])
            # No orphaned temp files from interrupted replaces.
            self.assertEqual(list(target.parent.glob(f"{target.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
