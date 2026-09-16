"""Startup instrumentation + the boot inspector deferral (#246).

Hermetic: the trace recorder is driven with an injected clock, and the boot
deferral is asserted without a running GUI.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from vesta import gui_web
from vestahub.startup_trace import StartupTrace, trace_enabled, trace_path


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class StartupTraceTests(unittest.TestCase):
    def test_disabled_trace_is_inert(self):
        clock = _FakeClock()
        trace = StartupTrace(enabled=False, clock=clock)
        clock.advance(1.0)
        trace.mark("a")
        self.assertEqual(trace.to_dict()["stages"], [])
        self.assertEqual(trace.to_dict()["total_ms"], 0.0)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(trace.write(Path(tmp) / "t.jsonl"))
            self.assertFalse((Path(tmp) / "t.jsonl").exists())

    def test_enabled_trace_records_ordered_offsets(self):
        clock = _FakeClock()
        trace = StartupTrace(enabled=True, clock=clock)
        clock.advance(0.010)
        trace.mark("boot:start")
        clock.advance(0.130)
        trace.mark("boot:done")
        clock.advance(0.040)
        trace.mark("interactive")
        stages = trace.to_dict()["stages"]
        self.assertEqual(
            [s["stage"] for s in stages], ["boot:start", "boot:done", "interactive"]
        )
        self.assertEqual([s["ms"] for s in stages], [10.0, 140.0, 180.0])
        self.assertEqual(trace.to_dict()["total_ms"], 180.0)

    def test_write_appends_only_when_enabled_and_nonempty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            enabled = StartupTrace(enabled=True)
            enabled.mark("boot:start")
            enabled.write(path)
            enabled2 = StartupTrace(enabled=True)
            enabled2.mark("boot:start")
            enabled2.write(path)
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)  # appended, not overwritten
            self.assertEqual(json.loads(lines[0])["kind"], "vesta_startup_trace")

    def test_enabled_but_empty_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            StartupTrace(enabled=True).write(path)
            self.assertFalse(path.exists())

    def test_trace_enabled_flag_parsing(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(trace_enabled({"VESTA_STARTUP_TRACE": value}), value)
        for value in ("", "0", "false", "off", "no"):
            self.assertFalse(trace_enabled({"VESTA_STARTUP_TRACE": value}), value)
        self.assertFalse(trace_enabled({}))

    def test_trace_path_is_local_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            path = trace_path(root)
        self.assertEqual(path.name, "startup-trace.jsonl")
        self.assertIn("gui", path.parts)


class BootDeferralTests(unittest.TestCase):
    def test_boot_defers_the_inspector_but_the_slot_still_serves_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = gui_web.boot_payload(root)
            # Deferred: not computed at boot.
            self.assertIsNone(payload["inspector"])
            # On demand it is fully available (behavior preserved).
            sel = {
                "model_label": "Auto",
                "model_advanced_label": "Auto",
                "model_kind": "auto",
                "mode": "safe-auto",
                "mode_label": "Safe Auto",
                "focus": "general",
                "format": "normal",
                "accounts": [],
            }
            inspector = gui_web._inspector(root, sel)
        self.assertIsInstance(inspector, dict)
        self.assertIn("rows", inspector)

    def test_boot_does_not_call_the_deferred_inspector(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch.object(gui_web, "_inspector") as spy:
                gui_web.boot_payload(root)
            spy.assert_not_called()

    def test_boot_records_stage_marks_when_tracing_is_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch.object(gui_web, "_STARTUP", StartupTrace(enabled=True)):
                gui_web.boot_payload(root)
                stages = [s["stage"] for s in gui_web._STARTUP.to_dict()["stages"]]
        self.assertEqual(stages[0], "boot:start")
        self.assertEqual(stages[-1], "boot:done")
        self.assertIn("boot:models", stages)


if __name__ == "__main__":
    unittest.main()
