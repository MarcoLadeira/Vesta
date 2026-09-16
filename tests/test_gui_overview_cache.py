"""The GUI status hot path must not rescan an unchanged workspace (#146).

`statusLine()` runs after every message and calls `overview()`, which rebuilds
the cockpit (integration walk + local-model discovery), re-summarizes the audit
log, and re-reads the ledger. On a long-lived ledger / large repo that is a
per-message freeze. `cached_overview` memoizes the whole composition on a cheap
state-file fingerprint: unchanged state serves the cache (coalescing repeated
refreshes); a real ledger/audit/budget change recomputes exactly once; a short
TTL ceiling self-heals against inputs we do not fingerprint.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from vesta import app_state, gui_web
from vestahub.ledger import ledger_path, record_event


class OverviewCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        gui_web.clear_overview_cache()

    def tearDown(self) -> None:
        gui_web.clear_overview_cache()
        self._tmp.cleanup()

    def _counting_overview(self):
        """Patch the heavy builder with a call-counting stub."""
        calls = {"n": 0}

        def fake(root):
            calls["n"] += 1
            return {"savings": {"estimated_savings_usd": float(calls["n"])}, "on": True}

        return calls, mock.patch.object(app_state, "overview", side_effect=fake)

    def test_unchanged_state_is_served_from_cache(self):
        calls, patch = self._counting_overview()
        with patch:
            first = gui_web.cached_overview(self.root)
            for _ in range(25):
                again = gui_web.cached_overview(self.root)
        # The heavy builder ran exactly once for 26 refreshes of unchanged state.
        self.assertEqual(calls["n"], 1)
        self.assertEqual(again, first)

    def test_ledger_change_invalidates_the_cache(self):
        calls, patch = self._counting_overview()
        with patch:
            gui_web.cached_overview(self.root)
            # A completed turn appends to the ledger — its mtime is the signal.
            record_event(self.root, "route_decision", task="did a thing", route="local")
            _bump_mtime(ledger_path(self.root))
            gui_web.cached_overview(self.root)
        self.assertEqual(calls["n"], 2)

    def test_ttl_zero_always_recomputes(self):
        calls, patch = self._counting_overview()
        with patch:
            gui_web.cached_overview(self.root, ttl=0.0)
            gui_web.cached_overview(self.root, ttl=0.0)
        self.assertEqual(calls["n"], 2)

    def test_status_payload_uses_the_cache_and_stays_within_budget(self):
        # A realistic per-message pattern: many status refreshes, no state change.
        # The heavy builder must run once; with a stubbed cost the wall-clock proves
        # coalescing without depending on machine speed.
        cost_s = 0.05

        def slow(root):
            time.sleep(cost_s)
            return {"savings": {"estimated_savings_usd": 1.0}, "on": True}

        with (
            mock.patch.object(app_state, "overview", side_effect=slow),
            mock.patch.object(
                app_state,
                "inspector_state",
                return_value={"budget": {"spent_today": 0.0}},
            ),
        ):
            started = time.monotonic()
            for _ in range(20):
                gui_web.cached_overview(self.root)
            elapsed = time.monotonic() - started
        # 20 refreshes cost ~1x the heavy call, not 20x.
        self.assertLess(elapsed, cost_s * 5)

    def test_warm_lookup_is_flat_against_a_large_ledger(self):
        # Heartbeat/large-repo fixture (#146 validation): the per-message status
        # refresh must not scale with ledger size. Seed a big ledger, prime the
        # cache once, then prove many warm refreshes never touch the heavy
        # builder again — the fingerprint is O(1) (three stat calls).
        path = ledger_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for i in range(5000):
                handle.write(
                    '{"created_at":"2026-07-14T00:00:00Z","event_type":"route_decision",'
                    f'"task_hash":"{i:016x}","route":"local"}}\n'
                )
        calls, patch = self._counting_overview()
        with patch:
            gui_web.cached_overview(self.root)  # cold: one heavy build
            started = time.monotonic()
            for _ in range(200):
                gui_web.cached_overview(self.root)
            warm_elapsed = time.monotonic() - started
        self.assertEqual(calls["n"], 1)  # never rebuilt for the 200 warm reads
        # 200 fingerprint-only lookups over a 5k-line ledger are effectively free.
        self.assertLess(warm_elapsed, 0.5)


def _bump_mtime(path: Path) -> None:
    """Force a distinct mtime even on coarse-resolution clocks."""
    stat = path.stat()
    import os

    os.utime(path, ns=(stat.st_atime_ns + 2_000_000, stat.st_mtime_ns + 2_000_000))


if __name__ == "__main__":
    unittest.main()
