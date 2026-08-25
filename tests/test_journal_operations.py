"""#613 Stage 6: external effects become operation transactions.

Stage 6 asks that provider, tool, Git, GitHub, cost and approval actions use
operation transactions. They already share one choke point --
``opaihub.idempotency``, which every exact-once effect passes through to claim
a key before acting -- so the mirror hooks there. One hook covers all of them,
and covers the ones nobody has written yet.

The test that matters most is the one about **not** guessing.

``abandon`` is documented as being only for failures where the side effect
certainly did not happen. A network timeout is not one of those: the request
may have arrived. So a claimed-but-unconfirmed operation stays ``executing``
forever until something reconciles it against the far side. Downgrading it on
a timer would invent a conclusion; clearing it would be worse, because the next
retry would fire a second real effect.

That is the difference between a store that records what is known and one that
records what would be convenient.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import idempotency, journal_operations
from opaihub.journal_operations import (
    STATE_CLAIMED,
    STATE_CONFIRMED,
    operation_summary,
    record_claim,
    record_confirmation,
    record_release,
    unreconciled_operations,
)
from opaihub.journal_store import open_store

NOW = "2026-08-25T12:00:00+00:00"


class _OperationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _state(self, key: str) -> str | None:
        store = open_store(self.root)
        self.addCleanup(store.close)
        row = store.execute(
            "SELECT state FROM operations WHERE operation_key = ?", (key,)
        ).fetchone()
        return row["state"] if row else None


class TheMirrorFollowsIdempotencyTests(_OperationFixture):
    """Every exact-once effect in OPai passes through this one choke point."""

    def test_claiming_a_key_records_a_claimed_operation(self):
        key = idempotency.operation_key("github.pr", head="feat/x", base="main")

        idempotency.begin(self.root, key)

        self.assertEqual(self._state(key), STATE_CLAIMED)

    def test_completing_records_a_reconciled_operation(self):
        key = idempotency.operation_key("github.pr", head="feat/x", base="main")
        idempotency.begin(self.root, key)

        idempotency.complete(self.root, key, {"id": 4242})

        self.assertEqual(self._state(key), STATE_CONFIRMED)

    def test_the_external_reference_is_carried_across(self):
        """Without it, a claimed and a completed operation differ only by our word."""

        key = idempotency.operation_key("github.pr", head="feat/x", base="main")
        idempotency.begin(self.root, key)
        idempotency.complete(self.root, key, {"url": "https://example/pull/7"})

        store = open_store(self.root)
        self.addCleanup(store.close)
        ref = store.execute(
            "SELECT external_ref FROM operations WHERE operation_key = ?", (key,)
        ).fetchone()[0]

        self.assertEqual(ref, "https://example/pull/7")

    def test_abandoning_removes_the_row_so_a_retry_is_a_first_attempt(self):
        """idempotency's contract: an abandoned key becomes fresh again."""

        key = idempotency.operation_key("git.push", branch="feat/x")
        idempotency.begin(self.root, key)
        self.assertEqual(self._state(key), STATE_CLAIMED)

        idempotency.abandon(self.root, key)

        self.assertIsNone(self._state(key))

    def test_the_operation_kind_is_recovered_from_the_key(self):
        """The hook does not know what it is mirroring, so the key must say."""

        key = idempotency.operation_key("github.pr", head="feat/x")
        idempotency.begin(self.root, key)

        store = open_store(self.root)
        self.addCleanup(store.close)
        kind = store.execute(
            "SELECT kind FROM operations WHERE operation_key = ?", (key,)
        ).fetchone()[0]

        self.assertEqual(kind, "github.pr")

    def test_a_duplicate_claim_stays_one_operation(self):
        key = idempotency.operation_key("github.pr", head="feat/x")
        idempotency.begin(self.root, key)
        idempotency.begin(self.root, key)

        store = open_store(self.root)
        self.addCleanup(store.close)
        count = store.execute("SELECT COUNT(*) FROM operations").fetchone()[0]

        self.assertEqual(count, 1)


class UncertaintyIsPreservedTests(_OperationFixture):
    """The heart of it: an unanswered operation must stay unanswered."""

    def test_a_claimed_operation_is_never_silently_resolved(self):
        key = idempotency.operation_key("github.pr", head="feat/x")
        idempotency.begin(self.root, key)

        # No completion, no abandonment -- the process died, or the network
        # timed out and we genuinely do not know whether the PR was opened.
        self.assertEqual(self._state(key), STATE_CLAIMED)
        self.assertEqual(len(unreconciled_operations(self.root)), 1)

    def test_unreconciled_operations_carry_what_a_reconciler_needs(self):
        key = idempotency.operation_key("github.pr", head="feat/x")
        idempotency.begin(self.root, key)

        pending = unreconciled_operations(self.root)[0]

        self.assertEqual(pending["operation_key"], key)
        self.assertEqual(pending["kind"], "github.pr")
        self.assertTrue(pending["created_at"])

    def test_a_confirmed_operation_leaves_the_unreconciled_list(self):
        key = idempotency.operation_key("github.pr", head="feat/x")
        idempotency.begin(self.root, key)
        idempotency.complete(self.root, key, {"id": 1})

        self.assertEqual(unreconciled_operations(self.root), [])

    def test_the_summary_counts_what_still_needs_answering(self):
        for index in range(3):
            idempotency.begin(
                self.root, idempotency.operation_key("git.push", branch=f"b{index}")
            )
        idempotency.complete(
            self.root, idempotency.operation_key("git.push", branch="b0"), {"id": 1}
        )

        summary = operation_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unreconciled"], 2)
        self.assertEqual(summary["states"][STATE_CONFIRMED], 1)


class TheMirrorNeverFailsAnEffectTests(_OperationFixture):
    """The idempotency file is authoritative until Stage 7; the mirror is not."""

    def test_a_broken_mirror_does_not_break_a_claim(self):
        key = idempotency.operation_key("github.pr", head="feat/x")

        with mock.patch.object(
            journal_operations, "record_claim", side_effect=OSError("disk full")
        ):
            status = idempotency.begin(self.root, key)

        self.assertEqual(status["state"], "fresh")
        self.assertEqual(idempotency.status(self.root, key)["state"], "in_flight")

    def test_a_broken_mirror_does_not_break_a_completion(self):
        key = idempotency.operation_key("github.pr", head="feat/x")
        idempotency.begin(self.root, key)

        with mock.patch.object(
            journal_operations, "record_confirmation", side_effect=OSError("gone")
        ):
            idempotency.complete(self.root, key, {"id": 1})

        self.assertEqual(idempotency.status(self.root, key)["state"], "done")

    def test_the_direct_helpers_return_false_rather_than_raising(self):
        # Patch where it actually fails: _store is a context manager that
        # yields None when open_store cannot open the file, so making _store
        # itself raise would test a failure mode that never occurs.
        from opaihub import journal_runtime

        with mock.patch.object(
            journal_runtime, "open_store", side_effect=OSError("gone")
        ):
            self.assertFalse(record_claim(self.root, "k", now=NOW))
            self.assertFalse(record_confirmation(self.root, "k", now=NOW))
            self.assertFalse(record_release(self.root, "k", now=NOW))

    def test_a_summary_of_an_absent_store_is_unavailable_not_empty(self):
        """Reporting zero operations for a store that is not there would lie."""

        summary = operation_summary(self.root)

        self.assertFalse(summary["available"])


class KeysDistinguishRealOperationsTests(_OperationFixture):
    """Two different effects must not collapse into one operation row."""

    def test_different_targets_are_different_operations(self):
        first = idempotency.operation_key("github.pr", head="feat/a", base="main")
        second = idempotency.operation_key("github.pr", head="feat/b", base="main")
        idempotency.begin(self.root, first)
        idempotency.begin(self.root, second)

        store = open_store(self.root)
        self.addCleanup(store.close)
        count = store.execute("SELECT COUNT(*) FROM operations").fetchone()[0]

        self.assertEqual(count, 2)

    def test_the_same_request_twice_is_one_operation(self):
        """A retry of the same request must not open a second PR."""

        key = idempotency.operation_key("github.pr", head="feat/a", base="main")
        again = idempotency.operation_key("github.pr", head="feat/a", base="main")

        self.assertEqual(key, again)
        idempotency.begin(self.root, key)
        second = idempotency.begin(self.root, again)

        self.assertEqual(second["state"], "in_flight", "the retry is recognised")
        store = open_store(self.root)
        self.addCleanup(store.close)
        self.assertEqual(
            store.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 1
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
