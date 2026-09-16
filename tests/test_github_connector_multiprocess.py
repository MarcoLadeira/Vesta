"""Concurrent GitHub-connector config writes preserve every field (#477)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from vestahub import github_connector as gc

_CHILD = r"""
import os
import sys
import time
from pathlib import Path

home = Path(sys.argv[1])
barrier = Path(sys.argv[2])
worker = sys.argv[3]
os.environ["HOME"] = str(home)
os.environ["USERPROFILE"] = str(home)

from vestahub.github_connector import _update_config

(barrier / f"{worker}.ready").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 20
while not (barrier / "go").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("timed out waiting for multiprocess test barrier")
    time.sleep(0.01)

# Each worker sets its own independent field. A lost update (naive
# read-modify-write) would drop one of them.
_update_config(lambda config: config.__setitem__(f"field_{worker}", True))
"""


class GithubConfigMultiprocessTests(unittest.TestCase):
    def test_concurrent_independent_updates_never_clobber_each_other(self) -> None:
        workers = 8
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            barrier = base / "barrier"
            home.mkdir()
            barrier.mkdir()
            processes = [
                subprocess.Popen(  # nosec B603 - fixed hermetic Python argv
                    [sys.executable, "-c", _CHILD, str(home), str(barrier), str(index)],
                    cwd=Path(__file__).resolve().parents[1],
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
                out, err = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, err.decode("utf-8", "replace"))

            config = json.loads(
                (home / ".vesta" / "github.json").read_text(encoding="utf-8")
            )
        # Every worker's independent field survived the concurrent writes.
        for index in range(workers):
            self.assertTrue(config.get(f"field_{index}"), f"lost field_{index}")


class GithubConfigAtomicityTests(unittest.TestCase):
    def test_updates_are_atomic_and_preserve_independent_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "github.json"
            with mock.patch.object(gc, "_config_path", return_value=cfg):
                gc.set_push_allowed(True)
                gc.set_public_read_allowed(True)
                gc._update_config(lambda c: c.__setitem__("login", "octocat"))
                config = gc._load_config()
            # Independent fields all coexist; the file is complete JSON.
            self.assertTrue(config["allow_push"])
            self.assertTrue(config["allow_public_read"])
            self.assertEqual(config["login"], "octocat")
            self.assertEqual(list(cfg.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
