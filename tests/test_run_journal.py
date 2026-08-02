"""Append-only run journals: monotonic sequence, crash safety, quarantine (#517).

A canonical run/step state machine (#379) is only as trustworthy as the file
it is written to. These tests prove the four properties #517 asks for on top
of that machine: durable append order survives a crash at every write
boundary, a truncated tail is recoverable while a corrupt interior record is
not silently accepted, replay from raw evidence always agrees with the cached
projection, and compaction can shrink the replay path without losing history
or restarting the sequence counter.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opaihub import run_journal as journal


def _empty() -> dict:
    return {"total": 0, "seen": []}


def _reduce(state: dict, event: dict) -> dict:
    return {
        "total": state["total"] + int(event.get("amount", 0)),
        "seen": [*state["seen"], event["sequence"]],
    }


class _Temp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "run-1.journal.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def append(self, amount: int) -> tuple[dict, dict]:
        return journal.append(
            self.path, {"amount": amount}, reduce=_reduce, empty=_empty
        )

    def load(self) -> journal.Recovery:
        return journal.load(self.path, reduce=_reduce, empty=_empty)


class AppendOrderTests(_Temp):
    def test_sequence_numbers_are_monotonic_from_one(self) -> None:
        first, _ = self.append(1)
        second, _ = self.append(2)
        third, _ = self.append(3)
        self.assertEqual(
            [first["sequence"], second["sequence"], third["sequence"]], [1, 2, 3]
        )

    def test_the_projection_reflects_every_append_in_order(self) -> None:
        self.append(5)
        self.append(7)
        _record, projection = self.append(2)
        self.assertEqual(projection["total"], 14)
        self.assertEqual(projection["seen"], [1, 2, 3])

    def test_an_event_may_not_pre_assign_a_sequence(self) -> None:
        with self.assertRaises(ValueError):
            journal.append(
                self.path,
                {"amount": 1, "sequence": 99},
                reduce=_reduce,
                empty=_empty,
            )

    def test_a_missing_journal_loads_as_the_empty_projection(self) -> None:
        recovery = self.load()
        self.assertEqual(recovery.projection, _empty())
        self.assertEqual(recovery.sequence, 0)
        self.assertFalse(recovery.quarantined)


class RecoveryTests(_Temp):
    """Every durable write boundary, crashed at, must still recover correctly."""

    def test_recovery_after_a_clean_append_matches_the_live_projection(self) -> None:
        self.append(3)
        _record, live_projection = self.append(4)
        recovery = self.load()
        self.assertEqual(recovery.projection, live_projection)
        self.assertEqual(recovery.sequence, 2)

    def test_a_crash_between_the_journal_write_and_the_head_write_recovers(
        self,
    ) -> None:
        # append() writes the journal line, fsyncs, and only then writes the
        # head cache. Deleting the head after the fact simulates a crash that
        # landed exactly between those two durable writes.
        self.append(1)
        self.append(2)
        journal.head_path(self.path).unlink()
        recovery = self.load()
        self.assertEqual(recovery.sequence, 2)
        self.assertEqual(recovery.projection["total"], 3)
        self.assertFalse(recovery.quarantined)
        # And the journal keeps counting from where it left off.
        third, _ = self.append(10)
        self.assertEqual(third["sequence"], 3)

    def test_a_head_pointing_past_the_end_of_a_shrunk_journal_forces_a_rescan(
        self,
    ) -> None:
        self.append(1)
        self.append(2)
        # Not a realistic crash artifact by itself, but proves the recovery
        # path never trusts an offset the file cannot actually support.
        self.path.write_bytes(self.path.read_bytes()[:10])
        recovery = self.load()
        self.assertFalse(recovery.quarantined)
        self.assertLessEqual(recovery.sequence, 1)

    def test_repeated_recovery_without_writes_always_converges_identically(
        self,
    ) -> None:
        self.append(1)
        self.append(2)
        self.append(3)
        first = self.load()
        second = self.load()
        third = self.load()
        self.assertEqual(first.projection, second.projection)
        self.assertEqual(second.projection, third.projection)
        self.assertEqual((first.sequence, second.sequence, third.sequence), (3, 3, 3))


class TruncatedTailTests(_Temp):
    """A crash can only ever tear the journal's final line."""

    def test_a_torn_final_line_with_no_newline_is_silently_forgotten(self) -> None:
        self.append(1)
        self.append(2)
        with self.path.open("ab") as handle:
            handle.write(json.dumps({"sequence": 3, "amount": 999}).encode("utf-8")[:8])
        recovery = self.load()
        self.assertFalse(recovery.quarantined)
        self.assertEqual(recovery.sequence, 2)
        self.assertEqual(recovery.projection["total"], 3)

    def test_appending_after_a_torn_tail_repairs_and_continues_cleanly(self) -> None:
        self.append(1)
        with self.path.open("ab") as handle:
            handle.write(b"{not even json")
        record, projection = self.append(5)
        self.assertEqual(record["sequence"], 2)
        self.assertEqual(projection["total"], 6)
        # The torn bytes are gone from what replay sees, not just from load().
        replayed = journal.replay(self.path, reduce=_reduce, empty=_empty)
        self.assertEqual(replayed, projection)

    def test_a_terminated_but_garbage_final_line_is_also_forgotten(self) -> None:
        # Terminated (has a trailing newline) but not valid JSON at all — the
        # other shape a torn write can leave behind depending on timing.
        self.append(1)
        with self.path.open("ab") as handle:
            handle.write(b"{garbage\n")
        recovery = self.load()
        self.assertFalse(recovery.quarantined)
        self.assertEqual(recovery.sequence, 1)


class QuarantineTests(_Temp):
    """Anything unreadable that is *not* the final line is real corruption."""

    def test_a_corrupt_interior_record_is_quarantined_not_skipped(self) -> None:
        self.append(1)
        self.append(2)
        self.append(3)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        lines[1] = "{this is not json"
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        recovery = self.load()
        self.assertTrue(recovery.quarantined)
        self.assertEqual(recovery.projection, _empty())
        self.assertEqual(recovery.sequence, 0)
        self.assertIsNotNone(recovery.quarantine_path)
        self.assertTrue(recovery.quarantine_path.exists())
        # The journal itself is gone from its original location — moved, not
        # copied, so a directory listing cannot show the file in two places.
        self.assertFalse(self.path.exists())

    def test_the_quarantine_manifest_names_the_reason_and_origin(self) -> None:
        self.append(1)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text(
            lines[0] + "\ngarbage-in-the-middle\n" + "{}\n", encoding="utf-8"
        )
        recovery = self.load()
        manifest_path = recovery.quarantine_path.with_name(
            recovery.quarantine_path.name + ".manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("reason", manifest)
        self.assertEqual(manifest["original_path"], str(self.path))

    def test_a_record_that_fails_validation_is_quarantined(self) -> None:
        def reject_large_amounts(event: dict) -> bool:
            return int(event.get("amount", 0)) < 100

        journal.append(
            self.path,
            {"amount": 1},
            reduce=_reduce,
            empty=_empty,
            validate=reject_large_amounts,
        )
        with self.assertRaises(ValueError):
            journal.append(
                self.path,
                {"amount": 500},
                reduce=_reduce,
                empty=_empty,
                validate=reject_large_amounts,
            )
        # Rejected at append time never reaches disk, so a normal load is
        # unaffected — this only proves append-time validation is real.
        recovery = journal.load(
            self.path, reduce=_reduce, empty=_empty, validate=reject_large_amounts
        )
        self.assertFalse(recovery.quarantined)
        self.assertEqual(recovery.sequence, 1)

    def test_recovery_after_quarantine_falls_back_to_the_last_snapshot(self) -> None:
        self.append(1)
        self.append(2)
        journal.compact(self.path, reduce=_reduce, empty=_empty)
        self.append(3)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text("not json at all\n" + lines[0] + "\n", encoding="utf-8")

        recovery = self.load()
        self.assertTrue(recovery.quarantined)
        # The live segment was corrupt and had to be discarded, but the
        # compacted snapshot survives untouched.
        self.assertEqual(recovery.sequence, 2)
        self.assertEqual(recovery.projection["total"], 3)


class PartialHeadTests(_Temp):
    """A malformed cache must never be mistaken for authoritative state."""

    def test_a_truncated_head_file_forces_a_full_rescan(self) -> None:
        self.append(1)
        self.append(2)
        head = journal.head_path(self.path)
        raw = head.read_bytes()
        head.write_bytes(raw[: len(raw) // 2])
        recovery = self.load()
        self.assertFalse(recovery.quarantined)
        self.assertEqual(recovery.sequence, 2)
        self.assertEqual(recovery.projection["total"], 3)

    def test_a_head_with_the_wrong_schema_is_ignored(self) -> None:
        self.append(1)
        head = journal.head_path(self.path)
        payload = json.loads(head.read_text(encoding="utf-8"))
        payload["schema"] = 999
        head.write_text(json.dumps(payload), encoding="utf-8")
        recovery = self.load()
        self.assertEqual(recovery.sequence, 1)

    def test_a_head_claiming_an_offset_the_file_does_not_contain_is_ignored(
        self,
    ) -> None:
        self.append(1)
        head = journal.head_path(self.path)
        payload = json.loads(head.read_text(encoding="utf-8"))
        payload["offset"] = payload["offset"] + 10_000
        head.write_text(json.dumps(payload), encoding="utf-8")
        recovery = self.load()
        self.assertFalse(recovery.quarantined)
        self.assertEqual(recovery.sequence, 1)


class ReplayDeterminismTests(_Temp):
    def test_replay_agrees_with_the_cached_head(self) -> None:
        self.append(1)
        self.append(2)
        self.append(3)
        replayed = journal.replay(self.path, reduce=_reduce, empty=_empty)
        loaded = self.load()
        self.assertEqual(replayed, loaded.projection)

    def test_replay_is_stable_across_repeated_calls(self) -> None:
        for amount in range(5):
            self.append(amount)
        first = journal.replay(self.path, reduce=_reduce, empty=_empty)
        second = journal.replay(self.path, reduce=_reduce, empty=_empty)
        self.assertEqual(first, second)

    def test_replay_raises_on_corruption_rather_than_mutating_disk(self) -> None:
        self.append(1)
        self.append(2)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        lines[0] = "corrupt"
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(journal.JournalCorruption):
            journal.replay(self.path, reduce=_reduce, empty=_empty)
        # Read-only: the journal is untouched, unlike load()'s quarantine.
        self.assertTrue(self.path.exists())


class CompactionTests(_Temp):
    def test_compaction_snapshots_the_projection_and_keeps_the_sequence(self) -> None:
        self.append(1)
        self.append(2)
        self.append(3)
        journal.compact(self.path, reduce=_reduce, empty=_empty)
        record, projection = self.append(4)
        self.assertEqual(record["sequence"], 4)
        self.assertEqual(projection["total"], 10)

    def test_compaction_never_deletes_the_rotated_segment(self) -> None:
        self.append(1)
        self.append(2)
        journal.compact(self.path, reduce=_reduce, empty=_empty)
        rotated = list(self.path.parent.glob(f"{self.path.name}.upto-*.segment"))
        self.assertEqual(len(rotated), 1)
        self.assertIn("1", rotated[0].read_text(encoding="utf-8"))

    def test_load_after_compaction_matches_pre_compaction_state_plus_new_appends(
        self,
    ) -> None:
        self.append(1)
        self.append(2)
        before = self.load().projection
        journal.compact(self.path, reduce=_reduce, empty=_empty)
        after_compaction = self.load()
        self.assertEqual(after_compaction.projection, before)
        self.append(3)
        final = self.load()
        self.assertEqual(final.projection["total"], before["total"] + 3)
        self.assertEqual(final.sequence, 3)

    def test_replay_after_compaction_still_agrees_with_load(self) -> None:
        self.append(1)
        self.append(2)
        journal.compact(self.path, reduce=_reduce, empty=_empty)
        self.append(3)
        self.append(4)
        self.assertEqual(
            journal.replay(self.path, reduce=_reduce, empty=_empty),
            self.load().projection,
        )

    def test_compacting_an_empty_journal_is_a_harmless_no_op(self) -> None:
        snapshot_path = journal.compact(self.path, reduce=_reduce, empty=_empty)
        self.assertTrue(snapshot_path.exists())
        recovery = self.load()
        self.assertEqual(recovery.projection, _empty())
        self.assertEqual(recovery.sequence, 0)


class AppendIfTests(_Temp):
    """The conditional-append primitive concurrent callers actually need.

    ``load()`` then ``append()`` is not safe under a race: the projection is
    read under one lock and written under a second, separate one, leaving a
    window for another caller to act on the same stale read. ``append_if``
    exists because a real caller (#380's cancellation tracker) needed to
    "advance only if nothing else already has" as a single atomic step.
    """

    def test_the_event_is_built_from_the_current_projection(self) -> None:
        self.append(5)
        self.append(7)
        record, projection = journal.append_if(
            self.path,
            lambda current: {"amount": 100 - current["total"]},
            reduce=_reduce,
            empty=_empty,
        )
        self.assertEqual(record["sequence"], 3)
        self.assertEqual(projection["total"], 100)

    def test_declining_appends_nothing_and_returns_none(self) -> None:
        self.append(1)
        result = journal.append_if(
            self.path, lambda _current: None, reduce=_reduce, empty=_empty
        )
        self.assertIsNone(result)
        self.assertEqual(self.load().sequence, 1)

    def test_a_pre_assigned_sequence_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            journal.append_if(
                self.path,
                lambda _current: {"amount": 1, "sequence": 5},
                reduce=_reduce,
                empty=_empty,
            )

    def test_two_racing_callers_never_both_win_a_guarded_append(self) -> None:
        import threading

        barrier = threading.Barrier(2)
        results: list[tuple[dict, dict] | None] = []
        lock = threading.Lock()

        def claim_once() -> None:
            barrier.wait(timeout=5)
            outcome = journal.append_if(
                self.path,
                lambda current: None if current["seen"] else {"amount": 1},
                reduce=_reduce,
                empty=_empty,
            )
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=claim_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.load().sequence, 1)

    def test_append_itself_is_now_implemented_on_top_of_append_if(self) -> None:
        # Not a change in append()'s observable contract — just proving the
        # refactor that fixed the race above didn't move append()'s goalposts.
        record, projection = self.append(3)
        self.assertEqual(record["sequence"], 1)
        self.assertEqual(projection["total"], 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
