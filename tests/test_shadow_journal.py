"""#613 Stage 2: the extracted shadow-journal helper.

``opaihub.shadow_journal`` is the shape ``owner_lease`` and ``worktree_leases``
each hand-rolled before it existed. These tests pin the properties every
module that adopts it inherits -- including the two defects the earlier
hand-rolled versions hit, so a future refactor cannot quietly reintroduce
either.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from opaihub import run_journal, shadow_journal


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.record = self.root / "thing-1.json"

    def _save(self, **fields: object) -> dict[str, object]:
        """Write a legacy record the way a migrated module would, then mirror."""
        payload = {"id": "thing-1", **fields}
        self.record.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        shadow_journal.record_snapshot(self.record, payload)
        return payload

    def _read_legacy(self) -> dict[str, object]:
        try:
            return json.loads(self.record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


class MirroringTests(_Fixture):
    def test_a_saved_record_is_mirrored_and_agrees(self):
        saved = self._save(state="active")

        self.assertEqual(shadow_journal.projection(self.record), saved)
        self.assertIsNone(
            shadow_journal.contradiction_report(self.record, self._read_legacy)
        )

    def test_the_latest_snapshot_wins(self):
        self._save(state="active")
        self._save(state="completed")

        self.assertEqual(shadow_journal.projection(self.record)["state"], "completed")
        self.assertIsNone(
            shadow_journal.contradiction_report(self.record, self._read_legacy)
        )

    def test_a_never_written_record_agrees_as_both_empty(self):
        absent = self.root / "never.json"

        self.assertEqual(shadow_journal.projection(absent), {})
        self.assertIsNone(shadow_journal.contradiction_report(absent, lambda: {}))


class JournalPlacementTests(_Fixture):
    """Regression pin: a sibling journal poisoned the legacy listing.

    ``run_journal``'s head cache is unconditionally ``<name>.head.json``. A
    module that lists records with a non-recursive ``*.json`` glob would pick
    that up as if it were a record. Caught originally by worktree_leases'
    existing suite; pinned here so the helper can never regress it for every
    module at once.
    """

    def test_no_journal_artifact_lands_in_the_records_glob(self):
        self._save(state="active")
        self._save(state="completed")

        siblings = sorted(p.name for p in self.root.glob("*.json"))

        self.assertEqual(siblings, ["thing-1.json"])

    def test_the_journal_lives_in_its_own_subdirectory(self):
        self._save(state="active")

        journal = shadow_journal.journal_path_for(self.record)

        self.assertEqual(journal.parent.name, shadow_journal.JOURNAL_SUBDIR)
        self.assertTrue(journal.exists())


class NeverBreaksTheCallerTests(_Fixture):
    def test_a_failing_append_never_raises(self):
        with mock.patch.object(run_journal, "append", side_effect=OSError("disk full")):
            shadow_journal.record_snapshot(self.record, {"id": "thing-1"})

    def test_a_failing_projection_read_never_raises(self):
        with mock.patch.object(
            run_journal, "load", side_effect=RuntimeError("corrupt")
        ):
            self.assertEqual(shadow_journal.projection(self.record), {})

    def test_a_legacy_reader_that_raises_is_treated_as_empty(self):
        self._save(state="active")

        def explode() -> dict[str, object]:
            raise ValueError("unreadable")

        report = shadow_journal.contradiction_report(self.record, explode)

        # An unreadable legacy record and a populated shadow *is* a real
        # contradiction, and must be reported rather than crash the caller.
        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})


class ContradictionIsExactTests(_Fixture):
    def test_an_out_of_band_write_is_reported_field_by_field(self):
        self._save(state="active", owner="worker-a")
        tampered = {"id": "thing-1", "state": "released", "owner": "someone-else"}
        self.record.write_text(json.dumps(tampered), encoding="utf-8")

        report = shadow_journal.contradiction_report(self.record, self._read_legacy)

        self.assertIsNotNone(report)
        self.assertEqual(report["mismatched_fields"], ["owner", "state"])
        self.assertEqual(report["legacy"], tampered)

    def test_identity_fields_are_carried_into_the_report(self):
        self._save(state="active")
        self.record.write_text(json.dumps({"id": "x"}), encoding="utf-8")

        report = shadow_journal.contradiction_report(
            self.record, self._read_legacy, identity={"lease_id": "thing-1"}
        )

        self.assertEqual(report["lease_id"], "thing-1")

    def test_an_invalid_record_is_not_mirrored(self):
        """A caller's validator rejects it, so the shadow stays empty."""

        shadow_journal.record_snapshot(
            self.record,
            {"id": "thing-1", "state": "bogus"},
            is_valid_record=lambda record: record.get("state") == "active",
        )

        self.assertEqual(shadow_journal.projection(self.record), {})


class ReplayDeterminismTests(_Fixture):
    """#613 acceptance criterion: repeated rebuilds must agree."""

    def test_replay_agrees_with_itself_and_with_the_projection(self):
        self._save(state="active")
        self._save(state="completed")

        journal = shadow_journal.journal_path_for(self.record)
        replays = [
            run_journal.replay(
                journal,
                reduce=shadow_journal._reduce,
                empty=shadow_journal._empty,
                validate=shadow_journal._validator(None),
            )
            for _ in range(2)
        ]

        self.assertEqual(replays[0], replays[1])
        self.assertEqual(replays[0], shadow_journal.projection(self.record))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
