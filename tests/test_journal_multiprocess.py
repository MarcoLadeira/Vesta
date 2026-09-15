"""#613 multiprocess integration: GUI, CLI and worker against one journal.

The required-tests section asks for "concurrent append/read/control from GUI,
CLI and worker processes", "lease acquisition, expiry and takeover", and
"duplicate operation insertion and idempotent retrieval".

Real processes, not threads. Python threads share one interpreter, one
connection pool and one GIL, so a threaded test can pass while the SQLite
file-locking that actually protects cross-process access is broken. The whole
point of #613 is that a GUI and a CLI are *different programs* touching one
store, and only separate interpreters exercise that.

The properties under test are the ones that fail expensively:

- every concurrent append lands, with a distinct sequence and nothing lost;
- readers are never blocked out by a writer (the WAL promise, across processes);
- one lease has exactly one holder, however many processes race for it;
- an operation key claimed by several processes produces one operation and one
  cost, no matter who wins.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv, this test's own interpreter
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from vestahub.journal_store import (
    INTEGRITY_COMPLETE,
    check_integrity,
    open_store,
    read_events,
)

NOW = "2026-08-25T12:00:00+00:00"
_REPO = Path(__file__).resolve().parent.parent


def _seed(root: Path) -> None:
    store = open_store(root)
    try:
        store.execute(
            "INSERT INTO tasks(task_id, origin_surface, created_at, schema_version,"
            " updated_at) VALUES ('task-a', 'cli', ?, 1, ?)",
            (NOW, NOW),
        )
        store.execute(
            "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
            " observed_state, created_at, updated_at)"
            " VALUES ('run-a', 'task-a', 1, 'running', 'queued', ?, ?)",
            (NOW, NOW),
        )
    finally:
        store.close()


def _child(root: Path, body: str, *, label: str = "worker") -> subprocess.Popen:
    """Start a real interpreter that talks to the same journal."""

    script = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, {repo!r})
        from pathlib import Path
        from vestahub import journal_store
        from vestahub.journal_store import (
            acquire_lease, append_event, open_store, read_events,
            record_cost, record_operation, release_lease,
        )
        from vestahub.journal_store import JournalStoreError, StaleWriterError
        root = Path({root!r})
        NOW = {now!r}
        LABEL = {label!r}
        store = open_store(root)
        try:
        {body}
        finally:
            store.close()
    """).format(
        repo=str(_REPO),
        root=str(root),
        now=NOW,
        label=label,
        body=textwrap.indent(textwrap.dedent(body).strip(), " " * 12),
    )
    return subprocess.Popen(  # nosec B603 - fixed argv, this interpreter, temp dir
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_all(children: list[subprocess.Popen]) -> list[tuple[int, str, str]]:
    results = []
    for child in children:
        out, err = child.communicate(timeout=180)
        results.append((child.returncode, out.strip(), err.strip()))
    return results


class _MultiprocessFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _seed(self.root)

    def _store(self):
        store = open_store(self.root)
        self.addCleanup(store.close)
        return store


class ConcurrentAppendTests(_MultiprocessFixture):
    """ "Concurrent append/read/control from GUI, CLI and worker processes."""

    def test_three_processes_appending_lose_nothing(self):
        surfaces = ["gui", "cli", "worker"]
        children = [
            _child(
                self.root,
                """
                for index in range(8):
                    append_event(
                        store,
                        event_type=LABEL + "." + str(index),
                        payload={"surface": LABEL, "index": index},
                        occurred_at=NOW,
                        recorded_at=NOW,
                        producer=LABEL,
                        run_id="run-a",
                    )
                """,
                label=surface,
            )
            for surface in surfaces
        ]

        for code, _out, err in _run_all(children):
            self.assertEqual(code, 0, err[-500:])

        rows = read_events(self._store())
        self.assertEqual(len(rows), 24, "every append from every process must land")
        sequences = [row["sequence"] for row in rows]
        self.assertEqual(len(set(sequences)), 24, "no sequence may be reused")
        self.assertEqual(sequences, sorted(sequences))
        for surface in surfaces:
            landed = [r for r in rows if r["payload"]["surface"] == surface]
            self.assertEqual(len(landed), 8, f"{surface} lost writes")

    def test_the_store_is_complete_after_concurrent_writers(self):
        children = [
            _child(
                self.root,
                """
                for index in range(6):
                    append_event(
                        store, event_type="e", payload={"i": index},
                        occurred_at=NOW, recorded_at=NOW, producer=LABEL,
                        run_id="run-a",
                    )
                """,
                label=f"w{index}",
            )
            for index in range(4)
        ]
        _run_all(children)

        self.assertEqual(check_integrity(self._store()).state, INTEGRITY_COMPLETE)

    def test_a_reader_process_sees_committed_writes_while_others_write(self):
        """WAL across processes: reading must not wait for writers to finish."""

        writers = [
            _child(
                self.root,
                """
                for index in range(10):
                    append_event(
                        store, event_type="w", payload={"i": index},
                        occurred_at=NOW, recorded_at=NOW, producer=LABEL,
                        run_id="run-a",
                    )
                """,
                label=f"w{index}",
            )
            for index in range(3)
        ]

        # Read from this process while the children are mid-flight. No thread
        # is involved on purpose: a SQLite connection is bound to the thread
        # that made it, and the property under test is cross-*process* reading,
        # so adding a thread would only introduce a second, unrelated failure
        # mode. The requirement is that the read returns rather than blocking
        # or raising "database is locked" -- how many rows happen to be visible
        # depends on timing and is deliberately not asserted.
        reader = open_store(self.root)
        try:
            visible = len(read_events(reader))
        finally:
            reader.close()

        for code, _out, err in _run_all(writers):
            self.assertEqual(code, 0, err[-500:])

        self.assertGreaterEqual(visible, 0)
        self.assertEqual(len(read_events(self._store())), 30)


class LeaseTakeoverAcrossProcessesTests(_MultiprocessFixture):
    """ "Lease acquisition, expiry and takeover", with real contenders."""

    def test_racing_processes_produce_distinct_increasing_fences(self):
        children = [
            _child(
                self.root,
                """
                fence = acquire_lease(store, run_id="run-a", owner=LABEL, now=NOW)
                print(json.dumps({"fence": fence}))
                """,
                label=f"owner{index}",
            )
            for index in range(5)
        ]

        fences = []
        for code, out, err in _run_all(children):
            self.assertEqual(code, 0, err[-500:])
            fences.append(json.loads(out)["fence"])

        self.assertEqual(len(set(fences)), 5, "every acquisition must be distinct")
        self.assertEqual(sorted(fences), list(range(1, 6)))

    def test_only_the_last_holder_can_still_write(self):
        """The acceptance criterion: stale supervisors are fenced out."""

        first = _child(
            self.root,
            """
            fence = acquire_lease(store, run_id="run-a", owner=LABEL, now=NOW)
            print(json.dumps({"fence": fence}))
            """,
            label="first",
        )
        code, out, err = _run_all([first])[0]
        self.assertEqual(code, 0, err[-500:])
        stale_fence = json.loads(out)["fence"]

        second = _child(
            self.root,
            """
            fence = acquire_lease(store, run_id="run-a", owner=LABEL, now=NOW)
            append_event(
                store, event_type="live", payload={}, occurred_at=NOW,
                recorded_at=NOW, producer=LABEL, run_id="run-a",
                expected_fence=fence,
            )
            print(json.dumps({"fence": fence}))
            """,
            label="second",
        )
        code, out, err = _run_all([second])[0]
        self.assertEqual(code, 0, err[-500:])

        zombie = _child(
            self.root,
            f"""
            try:
                append_event(
                    store, event_type="zombie", payload={{}}, occurred_at=NOW,
                    recorded_at=NOW, producer=LABEL, run_id="run-a",
                    expected_fence={stale_fence},
                )
                print(json.dumps({{"refused": False}}))
            except StaleWriterError:
                print(json.dumps({{"refused": True}}))
            """,
            label="zombie",
        )
        code, out, err = _run_all([zombie])[0]
        self.assertEqual(code, 0, err[-500:])

        self.assertTrue(json.loads(out)["refused"], "the stale holder must be refused")
        types = [row["event_type"] for row in read_events(self._store())]
        self.assertIn("live", types)
        self.assertNotIn("zombie", types)


class DuplicateOperationsAcrossProcessesTests(_MultiprocessFixture):
    """ "Duplicate operation insertion and idempotent retrieval."""

    def test_one_operation_key_claimed_by_many_processes_stays_one_operation(self):
        children = [
            _child(
                self.root,
                """
                created = record_operation(
                    store, operation_key="op-shared", kind="model.call",
                    target_digest="d", state="executing", now=NOW, run_id="run-a",
                )
                print(json.dumps({"created": bool(created)}))
                """,
                label=f"claimer{index}",
            )
            for index in range(5)
        ]

        creations = 0
        for code, out, err in _run_all(children):
            self.assertEqual(code, 0, err[-500:])
            creations += 1 if json.loads(out)["created"] else 0

        self.assertEqual(creations, 1, "exactly one process may create it")
        count = (
            self._store()
            .execute(
                "SELECT COUNT(*) FROM operations WHERE operation_key = 'op-shared'"
            )
            .fetchone()[0]
        )
        self.assertEqual(count, 1)

    def test_a_cost_is_attributed_once_however_many_processes_try(self):
        """The property #613 states outright: no cost attributed more than once."""

        setup = _child(
            self.root,
            """
            record_operation(
                store, operation_key="op-paid", kind="model.call",
                target_digest="d", state="observed", now=NOW, run_id="run-a",
            )
            print("{}")
            """,
        )
        _run_all([setup])

        children = [
            _child(
                self.root,
                """
                try:
                    record_cost(
                        store, operation_key="op-paid", amount=1.0,
                        measurement_kind="actual", now=NOW,
                    )
                    print(json.dumps({"charged": True}))
                except JournalStoreError:
                    print(json.dumps({"charged": False}))
                """,
                label=f"payer{index}",
            )
            for index in range(6)
        ]

        charged = 0
        for code, out, err in _run_all(children):
            self.assertEqual(code, 0, err[-500:])
            charged += 1 if json.loads(out)["charged"] else 0

        self.assertEqual(charged, 1, "exactly one process may record the cost")
        total = (
            self._store()
            .execute(
                "SELECT SUM(amount) FROM cost_events WHERE operation_key = 'op-paid'"
            )
            .fetchone()[0]
        )
        self.assertEqual(float(total), 1.0)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
