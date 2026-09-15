"""AgentComputerInterface.run_command actually stops a running process (#380).

Before this, ``cancel`` reaching a tool call only refused the *next* one —
`RepositoryToolExecutor.invoke()` checked it before dispatching, but once
``run_command``/``run_tests`` had actually started a subprocess, nothing could
touch it until it finished on its own or hit the ordinary timeout. That is
precisely #380's own description of the bug: "'Cancel requested' is not
cancellation if provider calls, child processes... continue."

Two layers of proof, matching how ``test_orphan_processes.py`` proves #108:

- Fake/injected ``_popen`` for the fast, deterministic branch logic (does the
  poll loop actually notice the token, does it distinguish CANCELLED from
  TIMEOUT, does the blocking path stay byte-for-byte unchanged when no
  ``cancel`` is given).
- A real spawned process with a real grandchild for the thing logic alone
  cannot show: that cancelling a live ``run_command`` call genuinely reaps
  the whole tree, not just the direct child (the "runner-wedge" #380 names,
  #264).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from vestahub.aci import AgentComputerInterface


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakePopen:
    """A test double whose ``communicate`` can be scripted to time out N times."""

    def __init__(
        self,
        *,
        timeouts_before_result: int = 0,
        returncode: int = 0,
        stdout: str = "ok",
        stderr: str = "",
    ) -> None:
        self._remaining_timeouts = timeouts_before_result
        self.returncode: int | None = None
        self._final = (returncode, stdout, stderr)
        self.terminate_called = False
        self.kill_called = False
        self.pid = 4242

    def communicate(self, input=None, timeout=None):  # noqa: A002 - matches Popen's API
        if self._remaining_timeouts > 0:
            self._remaining_timeouts -= 1
            raise subprocess.TimeoutExpired(cmd=["fake"], timeout=timeout or 0)
        self.returncode = self._final[0]
        return self._final[1], self._final[2]

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_called = True
        self.returncode = -15

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9


class _Temp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class InjectedFastPathTests(_Temp):
    """Deterministic branch coverage — no real process, no real waiting."""

    def test_a_cancel_already_set_before_spawn_is_refused_without_spawning(
        self,
    ) -> None:
        aci = AgentComputerInterface(
            self.repo, popen=mock.Mock(side_effect=AssertionError)
        )
        cancel = threading.Event()
        cancel.set()
        observation = aci.run_command(["true"], purpose="test", cancel=cancel)
        self.assertFalse(observation.ok)
        self.assertEqual(observation.error_code, "CANCELLED")

    def test_a_command_that_finishes_before_cancel_fires_succeeds_normally(
        self,
    ) -> None:
        fake = _FakePopen(returncode=0, stdout="all good")
        aci = AgentComputerInterface(self.repo, popen=mock.Mock(return_value=fake))
        cancel = threading.Event()  # provided, but never set
        observation = aci.run_command(["true"], purpose="test", cancel=cancel)
        self.assertTrue(observation.ok)
        self.assertEqual(observation.data["stdout"], "all good")
        self.assertFalse(fake.terminate_called)

    def test_a_command_that_fails_normally_is_command_failed_not_cancelled(
        self,
    ) -> None:
        fake = _FakePopen(returncode=1, stderr="boom")
        aci = AgentComputerInterface(self.repo, popen=mock.Mock(return_value=fake))
        observation = aci.run_command(
            ["false"], purpose="test", cancel=threading.Event()
        )
        self.assertFalse(observation.ok)
        self.assertEqual(observation.error_code, "COMMAND_FAILED")

    def test_cancel_firing_mid_flight_terminates_the_tree_and_reports_cancelled(
        self,
    ) -> None:
        fake = _FakePopen(timeouts_before_result=1_000_000)  # never finishes on its own
        aci = AgentComputerInterface(self.repo, popen=mock.Mock(return_value=fake))
        cancel = threading.Event()

        def flip_soon() -> None:
            time.sleep(0.1)
            cancel.set()

        threading.Thread(target=flip_soon).start()
        with mock.patch("vestahub.aci.terminate_tree") as terminate_tree:
            observation = aci.run_command(
                ["sleep-forever"], purpose="test", cancel=cancel, drain_seconds=0.2
            )
        self.assertFalse(observation.ok)
        self.assertEqual(observation.error_code, "CANCELLED")
        terminate_tree.assert_called_once_with(fake)

    def test_a_timeout_with_no_cancellation_is_still_reported_as_timeout(self) -> None:
        fake = _FakePopen(timeouts_before_result=1_000_000)
        aci = AgentComputerInterface(
            self.repo, popen=mock.Mock(return_value=fake), timeout=0.05
        )
        with mock.patch("vestahub.aci.terminate_tree") as terminate_tree:
            observation = aci.run_command(
                ["sleep-forever"],
                purpose="test",
                cancel=threading.Event(),
                drain_seconds=0.1,
            )
        self.assertFalse(observation.ok)
        self.assertEqual(observation.error_code, "TIMEOUT")
        terminate_tree.assert_called_once_with(fake)

    def test_a_process_that_drains_within_the_grace_window_is_not_force_terminated(
        self,
    ) -> None:
        # Cancel fires, but the process exits on its own within the drain
        # window — draining succeeded, force-terminating must not fire.
        fake = _FakePopen(timeouts_before_result=1)
        aci = AgentComputerInterface(self.repo, popen=mock.Mock(return_value=fake))
        cancel = threading.Event()
        cancel.set()

        with mock.patch("vestahub.aci.terminate_tree") as terminate_tree:
            observation = aci.run_command(
                ["quick"], purpose="test", cancel=cancel, drain_seconds=5.0
            )
        self.assertEqual(observation.error_code, "CANCELLED")
        terminate_tree.assert_not_called()

    def test_the_blocking_path_is_untouched_when_no_cancel_is_given(self) -> None:
        run = mock.Mock(return_value=_FakeCompleted(0, "hi", ""))
        popen = mock.Mock(side_effect=AssertionError("popen must not be used"))
        aci = AgentComputerInterface(self.repo, run=run, popen=popen)
        observation = aci.run_command(["true"], purpose="test")
        self.assertTrue(observation.ok)
        run.assert_called_once()

    def test_run_tests_forwards_cancel_to_run_command(self) -> None:
        fake = _FakePopen(returncode=0, stdout="")
        aci = AgentComputerInterface(self.repo, popen=mock.Mock(return_value=fake))
        cancel = threading.Event()
        cancel.set()
        observation = aci.run_tests(["pytest"], scope="unit", cancel=cancel)
        self.assertEqual(observation.error_code, "CANCELLED")


class _DrainingFakePopen(_FakePopen):
    """A fake that exits on its own after N ``poll()`` calls — the drain window."""

    def __init__(self, polls_before_exit: int = 3, **kwargs) -> None:
        super().__init__(**kwargs)
        self._polls_left = polls_before_exit

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self._polls_left > 0:
            self._polls_left -= 1
            return None
        self.returncode = self._final[0]
        return self.returncode


class TeardownEvidenceTests(_Temp):
    """#666: a stopped command records the teardown lifecycle it actually went
    through — each phase written at the moment it really happened, tied to the
    real ``terminate_tree`` call, with latencies computed from the journal."""

    def _run_with_cancel(self, fake, *, drain_seconds=0.2):
        aci = AgentComputerInterface(self.repo, popen=mock.Mock(return_value=fake))
        cancel = threading.Event()

        def flip_soon() -> None:
            time.sleep(0.1)
            cancel.set()

        threading.Thread(target=flip_soon).start()
        return aci.run_command(
            ["sleep-forever"],
            purpose="test",
            cancel=cancel,
            drain_seconds=drain_seconds,
        )

    def test_forced_teardown_records_every_phase_as_it_happens(self) -> None:
        fake = _FakePopen(timeouts_before_result=1_000_000)
        with mock.patch(
            "vestahub.aci.terminate_tree",
            side_effect=lambda proc: proc.terminate(),
        ) as terminate_tree:
            observation = self._run_with_cancel(fake)
        self.assertEqual(observation.error_code, "CANCELLED")
        terminate_tree.assert_called_once_with(fake)
        evidence = observation.data.get("cancellation") or {}
        self.assertTrue(evidence.get("scope_id", "").startswith("aci-"))
        self.assertEqual(evidence.get("phase"), "terminated")
        self.assertEqual(
            [entry["phase"] for entry in evidence.get("history", [])],
            [
                "requested",
                "acknowledged",
                "draining",
                "force_terminating",
                "terminated",
            ],
        )
        metrics = evidence.get("metrics", {})
        self.assertTrue(metrics.get("forced"))
        self.assertIsNotNone(metrics.get("acknowledgement_latency_seconds"))
        self.assertIsNotNone(metrics.get("hard_stop_latency_seconds"))
        # The journal is durable, not just a return value.
        journals = list(Path(self.repo).rglob("cancellation/aci-*.journal.jsonl"))
        self.assertTrue(journals, "no cancellation journal was persisted")

    def test_a_process_that_drains_is_never_marked_forced(self) -> None:
        fake = _DrainingFakePopen(timeouts_before_result=1_000_000)
        with mock.patch("vestahub.aci.terminate_tree") as terminate_tree:
            observation = self._run_with_cancel(fake, drain_seconds=5.0)
        self.assertEqual(observation.error_code, "CANCELLED")
        terminate_tree.assert_not_called()
        evidence = observation.data.get("cancellation") or {}
        self.assertEqual(
            [entry["phase"] for entry in evidence.get("history", [])],
            ["requested", "acknowledged", "draining", "terminated"],
        )
        self.assertFalse(evidence.get("metrics", {}).get("forced"))

    def test_a_stubborn_process_never_claims_terminated(self) -> None:
        fake = _FakePopen(timeouts_before_result=1_000_000)
        with mock.patch("vestahub.aci.terminate_tree"):  # kill achieves nothing
            observation = self._run_with_cancel(fake)
        self.assertEqual(observation.error_code, "CANCELLED")
        evidence = observation.data.get("cancellation") or {}
        self.assertEqual(evidence.get("phase"), "force_terminating")
        self.assertIsNone(evidence.get("metrics", {}).get("terminated_at"))

    def test_a_preflight_cancel_records_nothing_in_flight(self) -> None:
        aci = AgentComputerInterface(
            self.repo, popen=mock.Mock(side_effect=AssertionError)
        )
        cancel = threading.Event()
        cancel.set()
        observation = aci.run_command(["true"], purpose="test", cancel=cancel)
        self.assertEqual(observation.error_code, "CANCELLED")
        evidence = observation.data.get("cancellation") or {}
        self.assertEqual(evidence.get("phase"), "terminated")
        self.assertEqual(
            [entry["phase"] for entry in evidence.get("history", [])],
            ["requested", "acknowledged", "terminated"],
        )


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

_BEAT_TIMEOUT = 20.0
_QUIET = 0.7


class RealGrandchildCancellationTests(unittest.TestCase):
    """The actual wedge #380/#264 describe, proved against real processes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.script = self.tmp / "worker.py"
        self.script.write_text(_WORKER, encoding="utf-8")
        self.parent_beat = self.tmp / "parent.beat"
        self.child_beat = self.tmp / "child.beat"
        self.child_pid_file = self.tmp / "child.pid"

    def tearDown(self) -> None:
        for path in (self.child_pid_file,):
            try:
                stray = int(path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            if sys.platform == "win32":
                subprocess.run(  # nosec B603 B607 - fixed argv, no shell
                    ["taskkill", "/F", "/T", "/PID", str(stray)],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
            else:
                import contextlib
                import os
                import signal

                with contextlib.suppress(OSError):
                    os.kill(stray, signal.SIGKILL)
        self._tmp.cleanup()

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
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            first = path.stat().st_size
            time.sleep(_QUIET)
            if path.stat().st_size == first:
                return
            time.sleep(0.05)
        self.fail(f"{who} is still running — that is an orphan")

    def test_cancelling_a_live_run_command_call_reaps_the_grandchild(self) -> None:
        aci = AgentComputerInterface(self.repo, timeout=30.0)
        cancel = threading.Event()

        def cancel_once_both_are_alive() -> None:
            self._assert_beating(self.parent_beat, "the child")
            self._assert_beating(self.child_beat, "the grandchild")
            cancel.set()

        canceller = threading.Thread(target=cancel_once_both_are_alive)
        canceller.start()
        observation = aci.run_command(
            [
                sys.executable,
                str(self.script),
                str(self.parent_beat),
                str(self.child_beat),
                str(self.child_pid_file),
            ],
            purpose="prove the wedge is closed",
            cancel=cancel,
            drain_seconds=0.5,
        )
        canceller.join(timeout=25)

        self.assertFalse(observation.ok)
        self.assertEqual(observation.error_code, "CANCELLED")
        self._assert_stopped(self.parent_beat, "the child")
        self._assert_stopped(self.child_beat, "the grandchild")

    def test_a_real_deadline_timeout_also_reaps_the_grandchild(self) -> None:
        # Not just cancellation: an ordinary timeout on this path must not
        # leave grandchildren behind either (the blocking path's plain
        # subprocess.run timeout never reaped anything but the direct child).
        aci = AgentComputerInterface(self.repo, timeout=0.3)
        observation = aci.run_command(
            [
                sys.executable,
                str(self.script),
                str(self.parent_beat),
                str(self.child_beat),
                str(self.child_pid_file),
            ],
            purpose="prove timeouts reap too",
            cancel=threading.Event(),
            drain_seconds=0.5,
        )
        self.assertFalse(observation.ok)
        self.assertEqual(observation.error_code, "TIMEOUT")
        self._assert_stopped(self.parent_beat, "the child")
        self._assert_stopped(self.child_beat, "the grandchild")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
