"""Process-tree isolation and full-tree termination (#108).

No real processes are spawned: a fake process object stands in for the tree,
and the platform kill strategy is injected. Cancellation must reach the whole
tree, be idempotent, and never leave the child "alive" after a stop.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

from opaihub.process_tree import (
    isolated_group_kwargs,
    terminate_tree,
)


class _FakeProc:
    """Minimal stand-in for a Popen: tracks terminate/kill and exit state."""

    def __init__(self, pid: int = 4321, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated += 1
        self._alive = False  # a cooperative child exits on terminate

    def wait(self, timeout=None):
        if self._alive:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return 0

    def kill(self):
        self.killed += 1
        self._alive = False


class IsolationKwargsTests(unittest.TestCase):
    def test_kwargs_isolate_the_child_group_per_platform(self):
        kwargs = isolated_group_kwargs()
        if sys.platform == "win32":
            flags = kwargs["creationflags"]
            self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)
            self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
            self.assertNotIn("start_new_session", kwargs)
        else:
            self.assertTrue(kwargs["start_new_session"])
            self.assertNotIn("creationflags", kwargs)

    def test_no_window_can_be_disabled_on_windows(self):
        kwargs = isolated_group_kwargs(no_window=False)
        if sys.platform == "win32":
            self.assertFalse(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(
                kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            self.assertTrue(kwargs["start_new_session"])


class TerminateTreeTests(unittest.TestCase):
    def test_signals_the_whole_tree_then_stops_the_child(self):
        killed: list[int] = []
        proc = _FakeProc(pid=9999)
        terminate_tree(proc, tree_killer=killed.append)
        self.assertEqual(killed, [9999])  # the tree killer saw the pid
        self.assertEqual(proc.terminated, 1)
        self.assertFalse(proc._alive)

    def test_hard_kills_the_group_when_the_child_ignores_terminate(self):
        killed: list[int] = []
        group_killed: list[int] = []

        class Stubborn(_FakeProc):
            def terminate(self):
                self.terminated += 1  # refuses to die on terminate

        proc = Stubborn(pid=7)
        terminate_tree(
            proc,
            timeout=0.01,
            tree_killer=killed.append,
            group_sigkill=group_killed.append,
        )
        self.assertEqual(killed, [7])
        self.assertEqual(proc.killed, 1)  # escalated to kill()
        self.assertEqual(group_killed, [7])  # and hard-killed the group

    def test_none_process_is_a_noop(self):
        terminate_tree(None, tree_killer=lambda pid: self.fail("must not run"))

    def test_already_exited_process_is_not_signalled(self):
        proc = _FakeProc(alive=False)
        killed: list[int] = []
        terminate_tree(proc, tree_killer=killed.append)
        self.assertEqual(killed, [])
        self.assertEqual(proc.terminated, 0)

    def test_second_call_after_exit_is_idempotent(self):
        killed: list[int] = []
        proc = _FakeProc(pid=42)
        terminate_tree(proc, tree_killer=killed.append)
        terminate_tree(proc, tree_killer=killed.append)  # already dead now
        self.assertEqual(killed, [42])  # only the first call signalled

    def test_a_failing_tree_killer_still_stops_the_direct_child(self):
        def boom(pid):
            raise RuntimeError("taskkill unavailable")

        proc = _FakeProc(pid=1)
        terminate_tree(proc, tree_killer=boom)
        self.assertFalse(proc._alive)  # fell back to terminate()

    def test_process_without_a_pid_still_terminates(self):
        proc = _FakeProc()
        proc.pid = None
        killed: list[int] = []
        terminate_tree(proc, tree_killer=killed.append)
        self.assertEqual(killed, [])  # no pid → no group signal
        self.assertEqual(proc.terminated, 1)


class PopenIsolationWiringTests(unittest.TestCase):
    def test_streaming_popen_launches_in_its_own_group(self):
        import opaihub.accounts as accounts

        with mock.patch.object(accounts.subprocess, "Popen") as popen:
            accounts._popen(["claude", "-p"], cwd=None, env={"PATH": "x"})
        kwargs = popen.call_args.kwargs
        if sys.platform == "win32":
            self.assertTrue(
                kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            self.assertTrue(kwargs["start_new_session"])

    def test_terminate_helper_delegates_to_the_tree_killer(self):
        import opaihub.accounts as accounts

        with mock.patch("opaihub.accounts.terminate_tree") as tree:
            accounts._terminate("proc-handle")
        tree.assert_called_once_with("proc-handle")


if __name__ == "__main__":
    unittest.main()
