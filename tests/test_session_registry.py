"""Single-flight active-session registry + orphan sweep (#169). All hermetic."""

from __future__ import annotations

import threading
import unittest

from vestahub.session_registry import (
    CANCELLED,
    DONE,
    RUNNING,
    SessionRegistry,
    sweep_orphans,
)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def tick(self, dt=1.0):
        self.t += dt


class SessionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.reg = SessionRegistry(now=self.clock)

    def test_start_tracks_a_running_session(self):
        s = self.reg.start("r1", "claude", pid=111)
        self.assertEqual(s.state, RUNNING)
        self.assertEqual(self.reg.active_count(), 1)
        self.assertEqual(self.reg.active_pids(), [111])
        snap = self.reg.snapshot()
        self.assertEqual(snap[0]["request_id"], "r1")
        self.assertEqual(snap[0]["provider"], "claude")

    def test_single_flight_supersedes_and_cancels_the_predecessor(self):
        cancel = threading.Event()
        self.reg.start("r1", "claude", cancel=cancel, pid=1)
        # A retry for the SAME request id must cancel the predecessor.
        self.reg.start("r1", "claude", pid=2)
        self.assertTrue(cancel.is_set())  # predecessor was told to stop
        self.assertEqual(self.reg.get("r1").state, RUNNING)  # newest is the live one
        self.assertEqual(self.reg.get("r1").pid, 2)
        self.assertEqual(self.reg.active_count(), 1)  # never two at once

    def test_finish_marks_terminal(self):
        self.reg.start("r1", "groq")
        self.reg.finish("r1", state=DONE)
        self.assertEqual(self.reg.get("r1").state, DONE)
        self.assertEqual(self.reg.active_count(), 0)
        self.assertEqual(self.reg.snapshot(), [])

    def test_cancel_signals_and_marks_cancelled(self):
        cancel = threading.Event()
        self.reg.start("r1", "claude", cancel=cancel)
        self.assertTrue(self.reg.cancel("r1"))
        self.assertTrue(cancel.is_set())
        self.assertEqual(self.reg.get("r1").state, CANCELLED)
        # Cancelling an unknown/terminal session is a no-op returning False.
        self.assertFalse(self.reg.cancel("r1"))
        self.assertFalse(self.reg.cancel("nope"))

    def test_a_late_finish_never_resurrects_a_superseded_session(self):
        self.reg.start("r1", "claude", pid=1)
        self.reg.start("r1", "claude", pid=2)  # supersedes #1
        # The old predecessor's process finishing late must not flip the truth.
        self.reg.finish("r1", state=DONE)  # applies to the CURRENT (running) one
        current = self.reg.get("r1")
        self.assertEqual(current.pid, 2)
        self.assertEqual(current.state, DONE)

    def test_snapshot_is_newest_first_with_elapsed(self):
        self.reg.start("old", "a")
        self.clock.tick(2.0)
        self.reg.start("new", "b")
        self.clock.tick(0.5)
        snap = self.reg.snapshot()
        self.assertEqual([s["request_id"] for s in snap], ["new", "old"])
        self.assertEqual(snap[1]["elapsed_ms"], 2500)  # "old" ran 2.5s

    def test_prune_drops_only_old_terminal_sessions(self):
        for i in range(5):
            self.reg.start(f"r{i}", "a")
            self.reg.finish(f"r{i}")
            self.clock.tick()
        self.reg.start("live", "a")
        self.reg.prune(max_terminal=2)
        # Two most-recent terminal kept + the running one.
        remaining = {s.request_id for s in self.reg._sessions.values()}  # type: ignore[attr-defined]
        self.assertIn("live", remaining)
        self.assertEqual(sum(r.startswith("r") for r in remaining), 2)


class SweepOrphansTests(unittest.TestCase):
    def test_kills_alive_pids_and_skips_dead_ones(self):
        killed: list[int] = []
        alive = {101, 103}
        terminated = sweep_orphans(
            [101, 102, 103, 0, -5],
            is_alive=lambda p: p in alive,
            kill=killed.append,
        )
        self.assertEqual(sorted(terminated), [101, 103])
        self.assertEqual(sorted(killed), [101, 103])

    def test_a_failing_kill_is_skipped_not_raised(self):
        def bad_kill(_pid):
            raise PermissionError("not allowed")

        terminated = sweep_orphans([200], is_alive=lambda _p: True, kill=bad_kill)
        self.assertEqual(terminated, [])


class PipelineIntegrationTests(unittest.TestCase):
    def test_a_turn_registers_and_finishes_in_the_shared_registry(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from _helpers import make_repo

        from vestahub import session_registry
        from vestahub.gui_pipeline import handle_gui_message

        fresh = session_registry.SessionRegistry()
        with mock.patch.object(session_registry, "_SHARED", fresh):
            with tempfile.TemporaryDirectory() as tmp:
                root = make_repo(Path(tmp))
                with mock.patch(
                    "vestahub.ask.run_ask",
                    return_value={"status": "answered_locally", "answer": "ok"},
                ):
                    res = handle_gui_message(
                        root, "summarize", model_id="auto", mode="ask"
                    )
            self.assertEqual(res["status"], "answered")
            # The turn was tracked and closed: nothing left running, one DONE.
            self.assertEqual(fresh.active_count(), 0)
            states = [s.state for s in fresh._sessions.values()]  # type: ignore[attr-defined]
            self.assertEqual(states, [session_registry.DONE])


if __name__ == "__main__":
    unittest.main()
