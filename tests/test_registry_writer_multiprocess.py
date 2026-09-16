"""Concurrent tool-registry additions never lose a tool (#459).

The tool registry is a hub-global file, so the test pins VESTA_HUB_ROOT to an
isolated temp hub — it never touches the shipped registry.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import time
from pathlib import Path

import yaml

_CHILD = r"""
import sys
import time
from pathlib import Path

from vestahub.registry_writer import add_tool_entry

barrier = Path(sys.argv[1])
worker = sys.argv[2]

(barrier / f"{worker}.ready").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 20
while not (barrier / "go").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("timed out waiting for multiprocess test barrier")
    time.sleep(0.01)

result = add_tool_entry(Path.cwd(), {"id": f"tool-{worker}", "name": f"Tool {worker}"})
if not result.get("ok"):
    raise SystemExit(f"add failed: {result}")
"""


class RegistryWriterMultiprocessTests(unittest.TestCase):
    def test_concurrent_additions_are_all_preserved(self) -> None:
        workers = 8
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            hub = base / "hub"
            (hub / "registry").mkdir(parents=True)
            tools_yaml = hub / "registry" / "tools.yaml"
            tools_yaml.write_text("tools: []\n", encoding="utf-8")
            barrier = base / "barrier"
            barrier.mkdir()

            env = {**os.environ, "VESTA_HUB_ROOT": str(hub)}
            processes = [
                subprocess.Popen(  # nosec B603 - fixed hermetic Python argv
                    [sys.executable, "-c", _CHILD, str(barrier), str(index)],
                    cwd=Path(__file__).resolve().parents[1],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for index in range(workers)
            ]
            deadline = time.monotonic() + 20
            while (
                len(list(barrier.glob("*.ready"))) < workers
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            (barrier / "go").write_text("go", encoding="utf-8")
            for process in processes:
                _out, err = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, err.decode("utf-8", "replace"))

            data = yaml.safe_load(tools_yaml.read_text(encoding="utf-8")) or {}
            ids = {tool.get("id") for tool in (data.get("tools") or [])}

        # Every concurrently-added tool survived — no lost update, no torn file.
        for index in range(workers):
            self.assertIn(f"tool-{index}", ids)


if __name__ == "__main__":
    unittest.main()
