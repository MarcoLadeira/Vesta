"""ActivityBatcher: the Qt-free buffer behind the bridge's activityBatch
signal (#226). The QTimer glue lives in gui_web and is exercised via the e2e
mock bridge; here we lock the buffering contract hermetically."""

from __future__ import annotations

import json
import threading
import unittest

from opai.activity import make_event
from opai.activity_batch import FLUSH_INTERVAL_MS, ActivityBatcher


class ActivityBatcherTests(unittest.TestCase):
    def test_flush_returns_none_when_empty(self):
        self.assertIsNone(ActivityBatcher("req-1").flush())

    def test_flush_drains_in_order_into_one_payload(self):
        b = ActivityBatcher("req-1")
        b.append(make_event("streaming", "running", "a"))
        b.append(make_event("streaming", "running", "b"))
        payload = json.loads(b.flush())
        self.assertEqual(payload["requestId"], "req-1")
        self.assertEqual([e["title"] for e in payload["events"]], ["a", "b"])

    def test_flush_empties_the_buffer(self):
        b = ActivityBatcher("req-1")
        b.append(make_event("streaming", "running", "a"))
        b.flush()
        self.assertEqual(b.pending(), 0)
        self.assertIsNone(b.flush())

    def test_wire_shape_matches_the_mock_bridge_contract(self):
        # {"requestId": str, "events": [event, ...]} — mirrored by
        # window.__mock.emitActivityBatch in the e2e harness.
        b = ActivityBatcher("r")
        b.append(make_event("tool_call", "success", "Used tool"))
        payload = json.loads(b.flush())
        self.assertEqual(sorted(payload), ["events", "requestId"])
        self.assertIsInstance(payload["events"], list)

    def test_concurrent_appends_are_all_delivered_once(self):
        b = ActivityBatcher("req-1")
        total = 500

        def producer(start):
            for i in range(start, start + 100):
                b.append(make_event("streaming", "running", str(i)))

        threads = [
            threading.Thread(target=producer, args=(s,)) for s in range(0, total, 100)
        ]
        for t in threads:
            t.start()
        # Interleave flushes with production, like the GUI-thread timer does.
        drained = []
        while any(t.is_alive() for t in threads) or b.pending():
            payload = b.flush()
            if payload is not None:
                drained.extend(json.loads(payload)["events"])
        for t in threads:
            t.join()
        payload = b.flush()
        if payload is not None:
            drained.extend(json.loads(payload)["events"])
        self.assertEqual(len(drained), total)
        self.assertEqual(sorted(int(e["title"]) for e in drained), list(range(total)))

    def test_flush_interval_is_sane(self):
        # Cache TTL isn't relevant, but the tick must be sub-frame so batched
        # activity still feels live.
        self.assertGreater(FLUSH_INTERVAL_MS, 0)
        self.assertLessEqual(FLUSH_INTERVAL_MS, 50)


if __name__ == "__main__":
    unittest.main()
