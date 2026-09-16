"""#613: projections rebuild deterministically, and say so when they cannot.

Two acceptance criteria meet here:

- "Event ordering, operation identity and terminal state are deterministic
  across repeated rebuilds."
- "Projection deletion and rebuild produces the same canonical serialized
  state."

Both are about *bytes*, not about dicts that happen to compare equal, so every
assertion below goes through :func:`canonical_bytes`. Two rebuilds that agree
as Python objects but serialise differently would still break a receipt
comparison or a cached ETag, and the criterion says "serialized" for that
reason.

The property tests are the ones #613 names: reducer determinism over generated
histories, and no cost attributed more than once. They use Hypothesis to build
the histories rather than a fixed list, because the interesting failures are
orderings nobody thought to write down.

The other half is refusal. A projection folded *past* a corrupt event would
look complete and describe a history that never happened -- worse than a short
one, because nothing about it invites suspicion. :func:`rebuild_projection`
stops at the first unreadable event and reports ``degraded`` with its sequence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vestahub.journal_store import (
    INTEGRITY_COMPLETE,
    INTEGRITY_DEGRADED,
    JournalStoreError,
    append_event,
    canonical_bytes,
    drop_projection,
    load_projection,
    open_store,
    rebuild_projection,
    record_cost,
    record_operation,
)

NOW = "2026-08-23T12:00:00+00:00"


def _empty() -> dict[str, object]:
    return {"count": 0, "types": [], "last_sequence": 0}


def _reduce(
    projection: dict[str, object], record: dict[str, object]
) -> dict[str, object]:
    """A deliberately order-sensitive fold.

    An order-*insensitive* reducer (a counter, a set) would pass a determinism
    test even if the store returned events in a different order each time,
    which would make the test worthless. Keeping the ordered type list means
    the assertion actually depends on sequence order.
    """

    return {
        "count": int(projection["count"]) + 1,
        "types": [*projection["types"], record["event_type"]],
        "last_sequence": int(record["sequence"]),
    }


class _ProjectionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = open_store(self.root)
        self.addCleanup(self.store.close)
        self.store.execute(
            "INSERT INTO tasks(task_id, origin_surface, created_at, schema_version,"
            " updated_at) VALUES ('task-a', 'cli', ?, 1, ?)",
            (NOW, NOW),
        )
        self.store.execute(
            "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
            " observed_state, created_at, updated_at)"
            " VALUES ('run-a', 'task-a', 1, 'running', 'queued', ?, ?)",
            (NOW, NOW),
        )

    def _append(self, event_type: str, **payload: object) -> int:
        return append_event(
            self.store,
            event_type=event_type,
            payload=payload,
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )

    def _rebuild(self, *, persist: bool = True):
        return rebuild_projection(
            self.store,
            projection_type="run_summary",
            projection_version=1,
            reduce=_reduce,
            empty=_empty,
            now=NOW,
            persist=persist,
        )


class RebuildIsDeterministicTests(_ProjectionFixture):
    def test_two_rebuilds_of_one_history_are_byte_identical(self):
        for index in range(5):
            self._append(f"event-{index}")

        first = self._rebuild()
        second = self._rebuild()

        self.assertEqual(first.to_bytes(), second.to_bytes())
        self.assertEqual(first.source_sequence, second.source_sequence)

    def test_deleting_and_rebuilding_reproduces_the_same_bytes(self):
        """The acceptance criterion, stated in its own terms."""

        for index in range(5):
            self._append(f"event-{index}")
        before = self._rebuild()
        stored_before = load_projection(
            self.store, projection_type="run_summary", projection_version=1
        )

        self.assertTrue(
            drop_projection(
                self.store, projection_type="run_summary", projection_version=1
            )
        )
        self.assertIsNone(
            load_projection(
                self.store, projection_type="run_summary", projection_version=1
            )
        )

        after = self._rebuild()
        stored_after = load_projection(
            self.store, projection_type="run_summary", projection_version=1
        )

        self.assertEqual(before.to_bytes(), after.to_bytes())
        self.assertEqual(
            canonical_bytes(stored_before["payload"]),
            canonical_bytes(stored_after["payload"]),
        )

    def test_the_projection_is_stored_in_canonical_form(self):
        """Stored bytes must match rebuilt bytes, or comparisons are meaningless."""

        self._append("only")
        result = self._rebuild()

        stored = load_projection(
            self.store, projection_type="run_summary", projection_version=1
        )

        raw = self.store.execute("SELECT payload FROM projections").fetchone()[0]

        # The stored text must itself be canonical, not merely parse back to
        # something equal: a receipt or cache comparing bytes would fail on a
        # re-serialised-but-equal payload.
        self.assertEqual(raw.encode("utf-8"), result.to_bytes())
        self.assertEqual(canonical_bytes(stored["payload"]), result.to_bytes())

    def test_an_empty_history_projects_to_the_empty_state_not_to_nothing(self):
        result = self._rebuild()

        self.assertEqual(result.payload, _empty())
        self.assertEqual(result.source_sequence, 0)
        self.assertTrue(result.complete)

    def test_a_never_built_projection_loads_as_none(self):
        self.assertIsNone(
            load_projection(self.store, projection_type="never", projection_version=1)
        )


class ADegradedHistoryProducesADegradedProjectionTests(_ProjectionFixture):
    """A projection folded past corruption would look complete and be wrong."""

    def test_rebuild_stops_at_the_first_unreadable_event(self):
        self._append("first")
        bad = self._append("second")
        self._append("third")
        self.store.execute(
            "UPDATE events SET payload = '{not json' WHERE sequence = ?", (bad,)
        )

        result = self._rebuild()

        self.assertEqual(result.integrity, INTEGRITY_DEGRADED)
        self.assertEqual(result.first_invalid_sequence, bad)
        self.assertFalse(result.complete)
        self.assertEqual(result.payload["count"], 1, "must not fold past the gap")
        self.assertNotIn("third", result.payload["types"])

    def test_a_healthy_history_stays_complete(self):
        """Teeth the other way: degraded must not be the default answer."""

        self._append("first")
        self._append("second")

        result = self._rebuild()

        self.assertEqual(result.integrity, INTEGRITY_COMPLETE)
        self.assertIsNone(result.first_invalid_sequence)
        self.assertEqual(result.payload["count"], 2)

    def test_an_unreadable_stored_projection_is_reported_not_returned_empty(self):
        self._append("first")
        self._rebuild()
        self.store.execute("UPDATE projections SET payload = '{broken'")

        stored = load_projection(
            self.store, projection_type="run_summary", projection_version=1
        )

        self.assertIsNotNone(stored)
        self.assertFalse(stored["readable"])
        self.assertIsNone(stored["payload"])


class ReducerDeterminismPropertyTests(unittest.TestCase):
    """#613's property requirement, over generated histories rather than a list."""

    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        types=st.lists(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz.", min_size=1, max_size=12),
            min_size=0,
            max_size=40,
        )
    )
    def test_repeated_rebuilds_agree_for_any_history(self, types: list[str]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = open_store(root)
            try:
                store.execute(
                    "INSERT INTO tasks(task_id, origin_surface, created_at,"
                    " schema_version, updated_at) VALUES ('t', 'cli', ?, 1, ?)",
                    (NOW, NOW),
                )
                store.execute(
                    "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
                    " observed_state, created_at, updated_at)"
                    " VALUES ('r', 't', 1, 'running', 'queued', ?, ?)",
                    (NOW, NOW),
                )
                for index, event_type in enumerate(types):
                    append_event(
                        store,
                        event_type=event_type,
                        payload={"index": index},
                        occurred_at=NOW,
                        recorded_at=NOW,
                        producer="test",
                        run_id="r",
                    )

                def build():
                    return rebuild_projection(
                        store,
                        projection_type="p",
                        projection_version=1,
                        reduce=_reduce,
                        empty=_empty,
                        now=NOW,
                    )

                first = build()
                drop_projection(store, projection_type="p", projection_version=1)
                second = build()

                self.assertEqual(first.to_bytes(), second.to_bytes())
                self.assertEqual(first.payload["types"], list(types))
            finally:
                store.close()


class CostIsAttributedAtMostOncePropertyTests(unittest.TestCase):
    """#613: "No operation or cost event attributed more than once.\""""

    @settings(
        max_examples=40,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        keys=st.lists(
            st.sampled_from(["op-a", "op-b", "op-c"]), min_size=1, max_size=25
        )
    )
    def test_repeated_claims_never_double_attribute(self, keys: list[str]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = open_store(Path(tmp))
            try:
                accepted = 0
                for key in keys:
                    record_operation(
                        store,
                        operation_key=key,
                        kind="model.call",
                        target_digest="d",
                        state="observed",
                        now=NOW,
                    )
                    try:
                        record_cost(
                            store,
                            operation_key=key,
                            amount=1.0,
                            measurement_kind="actual",
                            now=NOW,
                        )
                        accepted += 1
                    except JournalStoreError:
                        pass

                distinct = len(set(keys))
                rows = store.execute(
                    "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM cost_events"
                ).fetchone()

                self.assertEqual(accepted, distinct)
                self.assertEqual(rows[0], distinct)
                self.assertEqual(
                    float(rows[1]),
                    float(distinct),
                    "total spend must equal one unit per distinct operation",
                )
            finally:
                store.close()


class ProjectionsAreDisposableTests(_ProjectionFixture):
    def test_dropping_a_projection_never_touches_the_events(self):
        """The whole premise: projections are derived, events are the truth."""

        for index in range(4):
            self._append(f"event-{index}")
        before = self.store.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        self._rebuild()
        drop_projection(self.store, projection_type="run_summary", projection_version=1)

        after = self.store.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(before, after)

        rebuilt = self._rebuild()
        self.assertEqual(rebuilt.payload["count"], 4)

    def test_dropping_a_projection_that_does_not_exist_is_not_an_error(self):
        self.assertFalse(
            drop_projection(self.store, projection_type="nothing", projection_version=1)
        )

    def test_two_versions_of_one_projection_coexist(self):
        """Version is part of the key so a new reducer can be built alongside."""

        self._append("only")
        self._rebuild()
        rebuild_projection(
            self.store,
            projection_type="run_summary",
            projection_version=2,
            reduce=_reduce,
            empty=_empty,
            now=NOW,
        )

        self.assertIsNotNone(
            load_projection(
                self.store, projection_type="run_summary", projection_version=1
            )
        )
        self.assertIsNotNone(
            load_projection(
                self.store, projection_type="run_summary", projection_version=2
            )
        )


class StoredProjectionIsJsonTests(_ProjectionFixture):
    def test_the_stored_payload_parses_as_json(self):
        self._append("only")
        self._rebuild()

        raw = self.store.execute("SELECT payload FROM projections").fetchone()[0]

        json.loads(raw)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
