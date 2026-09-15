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
    adopt,
    isolated_group_kwargs,
    terminate_tree,
)


class _FakeProc:
    """Minimal stand-in for a Popen: tracks terminate/kill and exit state.

    ``_alive`` and ``returncode`` are deliberately separate, because Popen
    separates them: a child can be gone from the OS while its pid is still
    held (a zombie) until something reaps it. The distinction decides whether
    the pid is safe to signal, so the fake has to model it.
    """

    def __init__(self, pid: int = 4321, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive
        self.returncode = None  # not reaped yet, exactly like a fresh Popen
        self.terminated = 0
        self.killed = 0

    def poll(self):
        if self._alive:
            return None
        self.returncode = 0  # observing the exit reaps it
        return 0

    def terminate(self):
        self.terminated += 1
        self._alive = False  # a cooperative child exits on terminate

    def wait(self, timeout=None):
        if self._alive:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        self.returncode = 0
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

    def test_an_exited_but_unreaped_child_still_gets_its_tree_reaped(self):
        # #295 gate 5. This used to assert the opposite — that an exited child
        # meant nothing to do. It is the single most costly case: a provider
        # CLI that crashed is exactly when its grandchildren (language servers,
        # git, sub-agents) are left running against the user's repository.
        proc = _FakeProc(alive=False)
        killed: list[int] = []
        terminate_tree(proc, tree_killer=killed.append)
        self.assertEqual(killed, [4321])
        self.assertEqual(proc.terminated, 0)  # the child itself needs nothing

    def test_a_reaped_child_is_never_signalled_again(self):
        # The other side of the same coin: once a pid has been reaped the OS
        # may hand it to an unrelated process, so signalling it is no longer
        # ours to do. `returncode` set is the "already reaped" evidence.
        proc = _FakeProc(alive=False)
        proc.returncode = 0
        killed: list[int] = []
        terminate_tree(proc, tree_killer=killed.append)
        self.assertEqual(killed, [])

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

    def test_the_spawned_child_is_adopted_so_a_crash_leaves_no_orphans(self):
        # Isolation alone is not enough on Windows: taskkill /T walks the
        # parent link, which is gone once the root crashes. adopt() is what
        # keeps the survivors reachable, so the real spawn path must call it.
        import opaihub.accounts as accounts

        with mock.patch.object(accounts.subprocess, "Popen", return_value="child"):
            with mock.patch("opaihub.accounts.adopt", return_value="adopted") as ad:
                got = accounts._popen(["claude", "-p"], cwd=None)
        ad.assert_called_once_with("child")
        self.assertEqual(got, "adopted")  # the adopted handle is what escapes


class AdoptionTests(unittest.TestCase):
    """adopt() is best effort: it must never be the reason a run fails."""

    def test_adopting_nothing_is_harmless(self):
        self.assertIsNone(adopt(None))

    def test_a_process_is_always_returned_even_when_adoption_fails(self):
        # A locked-down policy or an old Windows can refuse the job. Refusing
        # to launch would trade a cleanup weakness for an outage.
        proc = _FakeProc()
        with mock.patch("opaihub.process_tree._kernel32", return_value=None):
            self.assertIs(adopt(proc), proc)

    def test_a_test_double_is_never_adopted(self):
        # A job object is a live kill switch, so pointing one at a pid we did
        # not spawn would put someone else's process on it. The hazard is
        # concrete: int(MagicMock()) is 1, a real pid on both platforms. So the
        # pid must be a genuine int, not merely coercible to one.
        from opaihub.process_tree import _JOB_ATTR

        class _Double:
            pid = mock.MagicMock()

        self.assertEqual(int(_Double.pid), 1)  # the trap this guards against

        double = _Double()
        self.assertIs(adopt(double), double)
        self.assertFalse(hasattr(double, _JOB_ATTR))

    def test_terminating_without_a_job_reports_that_it_did_nothing(self):
        from opaihub.process_tree import _terminate_job

        self.assertFalse(_terminate_job(_FakeProc()))

    def test_an_adopted_group_is_reaped_even_after_the_pid_was_reaped(self):
        # The POSIX half of the crash fix. A bare pid becomes unsafe to signal
        # once reaped (the OS may reissue it), but a group recorded at spawn
        # names a group we created — so survivors stay reachable.
        from opaihub.process_tree import _PGID_ATTR

        proc = _FakeProc(pid=6100, alive=False)
        proc.returncode = 0  # something already waited on it
        setattr(proc, _PGID_ATTR, 6100)
        killed: list[int] = []
        terminate_tree(proc, tree_killer=killed.append)
        self.assertEqual(killed, [6100])

    @unittest.skipIf(sys.platform == "win32", "process groups are POSIX")
    def test_our_own_process_group_is_never_signalled(self):
        # Killing our own group would take Vesta down with the run it is
        # cleaning up — the one mistake here that is unrecoverable.
        import os

        from opaihub.process_tree import _killpg

        with mock.patch("opaihub.process_tree.os.killpg") as killpg:
            _killpg(os.getpgid(0), 15)
        killpg.assert_not_called()

    def test_a_job_is_released_once_and_only_once(self):
        # The handle is kill-on-close, so a double CloseHandle would release a
        # handle number the OS may have already reissued to something else.
        from opaihub.process_tree import _Job

        closed: list[tuple[int, bool]] = []

        class _K:
            def TerminateJobObject(self, handle, code):  # noqa: N802 - Win32 name
                closed.append((handle, True))

            def CloseHandle(self, handle):  # noqa: N802 - Win32 name
                closed.append((handle, False))

        job = _Job(77)
        with mock.patch("opaihub.process_tree._kernel32", return_value=_K()):
            job.close(terminate=True)
            job.close(terminate=True)  # a Stop after a natural exit
            job.close()
        self.assertEqual(closed, [(77, True), (77, False)])
        self.assertEqual(job.handle, 0)

    def test_releasing_a_job_without_kernel32_is_survivable(self):
        from opaihub.process_tree import _Job

        job = _Job(5)
        with mock.patch("opaihub.process_tree._kernel32", return_value=None):
            job.close(terminate=True)  # must not raise on a non-Windows host

    def test_terminate_reaps_the_job_before_touching_the_child(self):
        # Order is the whole fix: the tree is reaped first, so a root that has
        # already exited cannot short-circuit its survivors' cleanup.
        from opaihub.process_tree import _Job

        proc = _FakeProc(alive=False)
        seen: list[str] = []

        class _K:
            def TerminateJobObject(self, handle, code):  # noqa: N802 - Win32 name
                seen.append("job")

            def CloseHandle(self, handle):  # noqa: N802 - Win32 name
                pass

        setattr(proc, "_opai_job_handle", _Job(9))
        with mock.patch("opaihub.process_tree._kernel32", return_value=_K()):
            terminate_tree(proc, tree_killer=lambda pid: seen.append("taskkill"))
        # The job covers the whole tree, so no pid-based follow-up is needed.
        self.assertEqual(seen, ["job"])

    @unittest.skipUnless(sys.platform == "win32", "job objects are Windows-only")
    def test_a_real_child_is_adopted_and_the_job_is_released_on_terminate(self):
        from opaihub.process_tree import _JOB_ATTR

        proc = subprocess.Popen(  # nosec B603 - our own interpreter, argv list
            [sys.executable, "-c", "import time; time.sleep(30)"],
            **isolated_group_kwargs(),
        )
        try:
            adopt(proc)
            self.assertIsNotNone(
                getattr(proc, _JOB_ATTR, None), "a real child must get a job"
            )
            terminate_tree(proc)
            # The handle is closed and cleared, so a retry cannot double-close.
            self.assertIsNone(getattr(proc, _JOB_ATTR, None))
            terminate_tree(proc)
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
