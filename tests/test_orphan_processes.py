"""Real processes are actually reaped — no orphans (#295 gate 5).

`test_process_tree.py` proves the *logic* with an injected killer and a fake
process. Gate 5 asks for something that logic cannot show: that the operating
system really reaped the tree. Its wording is explicit — *"Prove zero orphan
processes after cancel, timeout, app exit, provider crash and retry"* — so these
tests spawn genuine child **and grandchild** processes and prove each scenario
against the real platform.

Two deliberate choices:

**Liveness is proved by a heartbeat file, not by a pid probe.** A pid can be
recycled, so "pid 9182 exists" does not mean *our* process exists. Worse, on
Windows `os.kill(pid, 0)` does not probe at all — it calls `TerminateProcess`,
so the check would kill what it claims to measure. A process that has stopped
appending to its heartbeat has stopped running, whatever its pid now belongs to.

**Every test first proves the orphan is real.** Asserting that a grandchild is
dead is worthless if it was never alive, so each scenario waits for both
heartbeats before killing anything.

Nothing here is paid: the processes are `sys.executable` running a sleep loop.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from vestahub.process_tree import adopt, isolated_group_kwargs, terminate_tree

# A worker that heartbeats to a file forever, and — when given a second path —
# spawns one grandchild that does the same. The grandchild is deliberately NOT
# isolated: it inherits the group/session/job, which is what makes it reachable.
_WORKER = """
import subprocess, sys, time

beat = sys.argv[1]
if len(sys.argv) > 2:
    kid = subprocess.Popen([sys.executable, __file__, sys.argv[2]])
    with open(sys.argv[3], "w") as fh:
        fh.write(str(kid.pid))
while True:
    with open(beat, "a") as fh:
        fh.write(".")
        fh.flush()
    time.sleep(0.02)
"""

_BEAT_TIMEOUT = 20.0  # generous: a cold interpreter start on Windows is slow
_QUIET = 0.7  # a stopped process writes nothing in this window


class _Tree:
    """A real parent + grandchild pair with heartbeat files."""

    def __init__(self, tmp: Path) -> None:
        self.script = tmp / "worker.py"
        self.script.write_text(_WORKER, encoding="utf-8")
        self.parent_beat = tmp / "parent.beat"
        self.child_beat = tmp / "child.beat"
        self.child_pid_file = tmp / "child.pid"
        self.proc = subprocess.Popen(  # nosec B603 - our own script, argv list
            [
                sys.executable,
                str(self.script),
                str(self.parent_beat),
                str(self.child_beat),
                str(self.child_pid_file),
            ],
            **isolated_group_kwargs(),
        )
        adopt(self.proc)

    @property
    def child_pid(self) -> int | None:
        try:
            return int(self.child_pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def hard_cleanup(self) -> None:
        """Never leak a process out of the test suite, even when it fails."""
        for pid in (getattr(self.proc, "pid", None), self.child_pid):
            if not pid:
                continue
            if sys.platform == "win32":
                subprocess.run(  # nosec B603 B607 - fixed argv, no shell
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
            else:
                import contextlib
                import os
                import signal

                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)


class OrphanProcessTests(unittest.TestCase):
    """Each scenario named by gate 5, against real processes."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.trees: list[_Tree] = []

    def tearDown(self) -> None:
        for tree in self.trees:
            tree.hard_cleanup()
        # Give Windows a moment to release the handles before the dir goes.
        time.sleep(0.1)
        self._tmpdir.cleanup()

    # -- helpers ---------------------------------------------------------

    def _spawn_tree(self) -> _Tree:
        sub = Path(tempfile.mkdtemp(dir=self.tmp))
        tree = _Tree(sub)
        self.trees.append(tree)
        return tree

    def _assert_beating(self, path: Path, who: str) -> None:
        deadline = time.monotonic() + _BEAT_TIMEOUT
        while time.monotonic() < deadline:
            try:
                if path.stat().st_size > 0:
                    return
            except OSError:
                pass
            time.sleep(0.02)
        self.fail(f"{who} never started beating — the fixture proves nothing")

    def _assert_stopped(self, path: Path, who: str) -> None:
        # Let any in-flight write land, then require a fully silent window.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            first = path.stat().st_size
            time.sleep(_QUIET)
            if path.stat().st_size == first:
                return
            time.sleep(0.05)
        self.fail(f"{who} is still running — that is an orphan")

    def _live_tree(self) -> _Tree:
        tree = self._spawn_tree()
        self._assert_beating(tree.parent_beat, "the child")
        self._assert_beating(tree.child_beat, "the grandchild")
        return tree

    # -- gate 5 scenarios ------------------------------------------------

    def test_cancel_reaps_the_grandchild_not_just_the_child(self) -> None:
        # The plain case: Stop while everything is healthy.
        tree = self._live_tree()
        terminate_tree(tree.proc)
        self._assert_stopped(tree.parent_beat, "the child")
        self._assert_stopped(tree.child_beat, "the grandchild")

    def test_timeout_reaps_the_whole_tree(self) -> None:
        # A timeout takes the same path as cancel but with no user present, so
        # a survivor would keep spending unobserved.
        tree = self._live_tree()
        terminate_tree(tree.proc, timeout=0.5)
        self._assert_stopped(tree.parent_beat, "the child")
        self._assert_stopped(tree.child_beat, "the grandchild")

    def test_retry_terminating_twice_leaves_nothing_alive(self) -> None:
        tree = self._live_tree()
        terminate_tree(tree.proc)
        terminate_tree(tree.proc)  # a retry must not resurrect or raise
        self._assert_stopped(tree.parent_beat, "the child")
        self._assert_stopped(tree.child_beat, "the grandchild")

    def test_app_exit_reaps_a_tree_it_never_waited_on(self) -> None:
        # App exit calls the killer without having read the child's status
        # first — the state the GUI is actually in when a window closes.
        tree = self._live_tree()
        self.assertIsNone(tree.proc.returncode)  # nothing has reaped it
        terminate_tree(tree.proc)
        self._assert_stopped(tree.child_beat, "the grandchild")

    def test_a_provider_crash_does_not_leave_the_grandchild_behind(self) -> None:
        """The case the old code got wrong.

        `terminate_tree` used to return immediately when the direct child had
        already exited — reasonable-looking, and wrong: a crashed provider CLI
        is exactly when its grandchildren (language servers, git, sub-agents)
        are left running against the user's repository.
        """
        tree = self._live_tree()

        # Kill *only* the root, the way a crash does.
        tree.proc.kill()
        tree.proc.wait(timeout=10)
        self._assert_stopped(tree.parent_beat, "the child")

        # Prove the orphan is real before claiming we cleaned it up.
        before = tree.child_beat.stat().st_size
        time.sleep(_QUIET)
        self.assertGreater(
            tree.child_beat.stat().st_size,
            before,
            "the grandchild died with its parent, so this fixture proves nothing",
        )

        terminate_tree(tree.proc)
        self._assert_stopped(tree.child_beat, "the grandchild")


_HOST = """
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from vestahub.process_tree import adopt, isolated_group_kwargs

proc = adopt(subprocess.Popen(
    [sys.executable, sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]],
    **isolated_group_kwargs(),
))
with open(sys.argv[6], "w") as fh:
    fh.write("up")
while True:
    time.sleep(0.05)
"""


class AppExitTests(unittest.TestCase):
    """Vesta being killed outright must not strand the tree it spawned.

    Cancel and timeout run *our* cleanup code. App exit may not: a force-kill,
    an OOM kill or a crash gives Vesta no chance to terminate anything. The
    Windows job object covers that with ``KILL_ON_JOB_CLOSE`` — when the last
    handle closes, which the OS does on process exit, the job dies with it.
    """

    @unittest.skipUnless(sys.platform == "win32", "kill-on-close is a job object")
    def test_force_killing_vesta_takes_the_whole_tree_with_it(self) -> None:
        repo = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            worker = tmpdir / "worker.py"
            worker.write_text(_WORKER, encoding="utf-8")
            host_script = tmpdir / "host.py"
            host_script.write_text(_HOST, encoding="utf-8")
            pbeat, cbeat = tmpdir / "p.beat", tmpdir / "c.beat"
            cpid, ready = tmpdir / "c.pid", tmpdir / "ready"

            host = subprocess.Popen(  # nosec B603 - our own script, argv list
                [
                    sys.executable,
                    str(host_script),
                    repo,
                    str(worker),
                    str(pbeat),
                    str(cbeat),
                    str(cpid),
                    str(ready),
                ],
                **isolated_group_kwargs(),
            )
            try:
                deadline = time.monotonic() + _BEAT_TIMEOUT
                while time.monotonic() < deadline:
                    if cbeat.exists() and cbeat.stat().st_size > 0 and ready.exists():
                        break
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), "the host never got started")
                self.assertGreater(cbeat.stat().st_size, 0, "no grandchild to strand")

                # Kill ONLY the host — no /T — the way a crash or Task Manager
                # "End task" does. Nothing of ours runs after this point.
                subprocess.run(  # nosec B603 B607 - fixed argv, no shell
                    ["taskkill", "/F", "/PID", str(host.pid)],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                host.wait(timeout=10)

                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    first = cbeat.stat().st_size
                    time.sleep(_QUIET)
                    if cbeat.stat().st_size == first:
                        break
                else:
                    self.fail("the grandchild outlived Vesta — that is an orphan")
            finally:
                for path in (cpid,):
                    try:
                        stray = int(path.read_text(encoding="utf-8").strip())
                    except (OSError, ValueError):
                        continue
                    subprocess.run(  # nosec B603 B607
                        ["taskkill", "/F", "/T", "/PID", str(stray)],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                if host.poll() is None:
                    host.kill()
                time.sleep(0.1)


class IsolationTests(unittest.TestCase):
    """The child must not share our own group, or killing it kills us."""

    @unittest.skipIf(sys.platform == "win32", "POSIX process groups")
    def test_the_child_leads_its_own_group(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as tmp:
            tree = _Tree(Path(tmp))
            try:
                deadline = time.monotonic() + _BEAT_TIMEOUT
                while time.monotonic() < deadline and not tree.parent_beat.exists():
                    time.sleep(0.02)
                pgid = os.getpgid(tree.proc.pid)
                self.assertEqual(pgid, tree.proc.pid)  # setsid made it the leader
                self.assertNotEqual(pgid, os.getpgid(0))  # and not our group
            finally:
                tree.hard_cleanup()
                time.sleep(0.1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
