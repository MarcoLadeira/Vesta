"""#613 performance: p50/p95 write, projection rebuild, startup, storage growth.

The required-tests section asks for exactly those four numbers "on
representative histories". This file measures them and records them, and the
design question it had to answer first was: *what should a performance test
assert?*

A tight threshold turns into a flaky test on a loaded CI runner, and the usual
response -- loosen it until it stops failing -- leaves a test that asserts
nothing while looking rigorous. So the budgets here are deliberately generous:
they are set where a *regression in kind* trips them (an accidental O(n²)
rebuild, a per-append fsync storm, an index dropped) while ordinary machine
noise does not. They are not a statement that the store is fast.

The numbers themselves are printed, because #613 asks for performance numbers
as *evidence*, and evidence that only exists when a test fails is not evidence.
Run this file directly to see them.

One property is asserted tightly rather than loosely, because it is about shape
rather than speed: projection rebuild must stay roughly linear in history
length. That is checkable by comparing two sizes against each other, which
cancels out how fast the machine is.
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
import unittest
from pathlib import Path

from opaihub.journal_store import (
    append_event,
    canonical_bytes,
    journal_path,
    open_store,
    rebuild_projection,
)

NOW = "2026-08-25T12:00:00+00:00"

#: Deliberately generous. See the module docstring: these catch a regression in
#: kind, not a slow afternoon on a shared runner.
BUDGET_P50_WRITE_MS = 50.0
BUDGET_P95_WRITE_MS = 250.0
BUDGET_STARTUP_MS = 2000.0
BUDGET_REBUILD_MS_PER_1K = 5000.0
BUDGET_BYTES_PER_EVENT = 4096


def _seed(store) -> None:
    store.execute(
        "INSERT INTO tasks(task_id, origin_surface, created_at, schema_version,"
        " updated_at) VALUES ('task-a', 'cli', ?, 1, ?)",
        (NOW, NOW),
    )
    store.execute(
        "INSERT INTO runs(run_id, task_id, attempt, desired_state, observed_state,"
        " created_at, updated_at)"
        " VALUES ('run-a', 'task-a', 1, 'running', 'queued', ?, ?)",
        (NOW, NOW),
    )


def _empty() -> dict[str, object]:
    return {"count": 0, "last": 0}


def _reduce(projection: dict[str, object], record: dict[str, object]):
    return {"count": int(projection["count"]) + 1, "last": int(record["sequence"])}


def _fill(store, count: int) -> list[float]:
    """Append ``count`` events, returning each append's latency in ms."""

    latencies = []
    for index in range(count):
        start = time.perf_counter()
        append_event(
            store,
            event_type="turn.event",
            payload={"index": index, "detail": "a representative payload"},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="bench",
            run_id="run-a",
        )
        latencies.append((time.perf_counter() - start) * 1000.0)
    return latencies


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


class WriteLatencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = open_store(self.root)
        self.addCleanup(self.store.close)
        _seed(self.store)

    def test_append_latency_stays_within_budget(self):
        latencies = _fill(self.store, 300)

        p50 = _percentile(latencies, 0.50)
        p95 = _percentile(latencies, 0.95)
        print(
            f"\n[perf] append p50={p50:.2f}ms p95={p95:.2f}ms "
            f"mean={statistics.mean(latencies):.2f}ms n={len(latencies)}"
        )

        self.assertLess(p50, BUDGET_P50_WRITE_MS)
        self.assertLess(p95, BUDGET_P95_WRITE_MS)

    def test_storage_growth_per_event_stays_bounded(self):
        """An unbounded per-event footprint is a slow-motion disk-full bug."""

        _fill(self.store, 500)
        self.store.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size = journal_path(self.root).stat().st_size
        per_event = size / 500

        print(f"[perf] storage total={size}B per_event={per_event:.0f}B n=500")

        self.assertLess(per_event, BUDGET_BYTES_PER_EVENT)


class StartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_opening_an_existing_store_is_fast(self):
        """Startup runs on every command; a slow open is felt everywhere."""

        store = open_store(self.root)
        _seed(store)
        _fill(store, 400)
        store.close()

        start = time.perf_counter()
        reopened = open_store(self.root)
        elapsed = (time.perf_counter() - start) * 1000.0
        reopened.close()

        print(f"[perf] startup open={elapsed:.2f}ms history=400")

        self.assertLess(elapsed, BUDGET_STARTUP_MS)

    def test_startup_does_not_degrade_sharply_with_history(self):
        """Opening must not read the whole history.

        Compared against a *smaller* store rather than a fixed budget, because
        the property is that open cost is roughly independent of history --
        which is checkable regardless of how fast the machine is.
        """

        small_root = Path(self._tmp.name) / "small"
        large_root = Path(self._tmp.name) / "large"
        for root, count in ((small_root, 50), (large_root, 1000)):
            root.mkdir(parents=True, exist_ok=True)
            store = open_store(root)
            _seed(store)
            _fill(store, count)
            store.close()

        def open_ms(root: Path) -> float:
            start = time.perf_counter()
            connection = open_store(root)
            elapsed = (time.perf_counter() - start) * 1000.0
            connection.close()
            return elapsed

        small = min(open_ms(small_root) for _ in range(3))
        large = min(open_ms(large_root) for _ in range(3))

        print(f"[perf] startup small(50)={small:.2f}ms large(1000)={large:.2f}ms")

        # 20x history must not mean 20x open cost. The allowance is wide
        # because both numbers are small enough that timer noise dominates.
        self.assertLess(large, max(small * 10.0, 200.0))


class ProjectionRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = open_store(self.root)
        self.addCleanup(self.store.close)
        _seed(self.store)

    def _rebuild_ms(self) -> float:
        start = time.perf_counter()
        rebuild_projection(
            self.store,
            projection_type="bench",
            projection_version=1,
            reduce=_reduce,
            empty=_empty,
            now=NOW,
        )
        return (time.perf_counter() - start) * 1000.0

    def test_rebuild_of_a_representative_history_is_within_budget(self):
        _fill(self.store, 1000)

        elapsed = self._rebuild_ms()
        print(f"[perf] rebuild n=1000 elapsed={elapsed:.2f}ms")

        self.assertLess(elapsed, BUDGET_REBUILD_MS_PER_1K)

    def test_rebuild_cost_stays_roughly_linear(self):
        """The tight assertion, because it is about shape rather than speed.

        Doubling the history must not quadruple the rebuild. An accidental
        O(n^2) reducer or a per-event query would show up here long before it
        showed up as a threshold breach, and comparing two sizes cancels out
        how fast the machine is.
        """

        _fill(self.store, 500)
        small = min(self._rebuild_ms() for _ in range(3))
        _fill(self.store, 500)
        large = min(self._rebuild_ms() for _ in range(3))

        ratio = large / max(small, 0.001)
        print(
            f"[perf] rebuild linearity 500={small:.2f}ms 1000={large:.2f}ms ratio={ratio:.2f}"
        )

        self.assertLess(ratio, 4.0, "doubling the history more than quadrupled cost")

    def test_a_rebuilt_projection_is_the_expected_size(self):
        """Guards the benchmark itself: a no-op rebuild would look very fast."""

        _fill(self.store, 200)

        result = rebuild_projection(
            self.store,
            projection_type="bench",
            projection_version=1,
            reduce=_reduce,
            empty=_empty,
            now=NOW,
        )

        self.assertEqual(result.payload["count"], 200)
        self.assertGreater(len(canonical_bytes(result.payload)), 0)


class PerformanceEvidenceTests(unittest.TestCase):
    """#613 asks for performance numbers as evidence, not just as thresholds."""

    def test_the_measurements_can_be_emitted_as_a_machine_readable_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = open_store(root)
            try:
                _seed(store)
                latencies = _fill(store, 200)
                start = time.perf_counter()
                rebuild_projection(
                    store,
                    projection_type="bench",
                    projection_version=1,
                    reduce=_reduce,
                    empty=_empty,
                    now=NOW,
                )
                rebuild_ms = (time.perf_counter() - start) * 1000.0
                store.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                size = journal_path(root).stat().st_size
            finally:
                store.close()

        evidence = {
            "report": "opai-journal-performance",
            "events": len(latencies),
            "append_p50_ms": round(_percentile(latencies, 0.50), 3),
            "append_p95_ms": round(_percentile(latencies, 0.95), 3),
            "rebuild_ms": round(rebuild_ms, 3),
            "bytes_total": size,
            "bytes_per_event": round(size / len(latencies), 1),
        }
        print("[perf] evidence " + json.dumps(evidence))

        json.loads(json.dumps(evidence))
        self.assertEqual(evidence["events"], 200)
        self.assertGreater(evidence["bytes_total"], 0)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main(verbosity=2)
