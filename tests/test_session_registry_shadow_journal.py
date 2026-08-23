"""#613 Stage 2: active provider sessions mirror into the shadow journal.

``opaihub/session_registry.py`` is Stage 1's "leases: cross-process active
provider sessions" -- one of the two entries the packaged-update work (#712)
added to the inventory while Stage 2 was in progress, which is the ratchet
doing its job on someone else's PR.

Two things make this module different from the six before it.

**It deletes records.** A finished session removes its durable file. Snapshots
alone cannot express that: the shadow would assert the last state forever and
every completed session would read as a contradiction against an absent file
-- constant noise exactly where the dual read should be quiet. The helper
gained :func:`shadow_journal.record_deletion`, a tombstone that reduces the
projection back to ``{}``, which is what the legacy reader sees once the file
is gone.

**Its listing does not merely miscount unknown files -- it deletes them.**
``durable_active_count`` globs ``*.json`` and ``unlink``s anything failing its
``schema_version == 1`` check. ``run_journal``'s head cache is unconditionally
``<name>.head.json``, so a sibling journal would have been *destroyed* on
every count, not just miscounted. Third variant of the glob trap and by far
the most damaging: this registry gates single-flight session admission, so
corrupting it either blocks legitimate work or lets two run at once.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opaihub import shadow_journal
from opaihub.session_registry import SessionRegistry, durable_active_count


class SessionShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.registry = SessionRegistry(durable_root=self.root, process_id=4242)

    def _durable_path(self, request_id: str) -> Path:
        path = self.registry._durable_path(request_id)
        assert path is not None
        return path

    def test_a_started_session_is_mirrored(self):
        self.registry.start("req-1", "claude")

        projection = shadow_journal.projection(self._durable_path("req-1"))

        self.assertEqual(projection["pid"], 4242)
        self.assertEqual(projection["provider"], "claude")

    def test_a_finished_session_leaves_a_tombstone_not_a_stale_claim(self):
        """The reason record_deletion exists.

        Without it the shadow would still assert a running session after the
        file was removed, and every completed session would look like a
        contradiction.
        """
        self.registry.start("req-1", "claude")
        path = self._durable_path("req-1")

        self.registry.finish("req-1")

        self.assertFalse(path.exists())
        self.assertEqual(shadow_journal.projection(path), {})

    def test_a_completed_session_reads_as_agreement(self):
        self.registry.start("req-1", "claude")
        path = self._durable_path("req-1")
        self.registry.finish("req-1")

        report = shadow_journal.contradiction_report(path, lambda: {})

        self.assertIsNone(report)

    def test_the_journal_is_never_counted_or_deleted_by_the_registry_sweep(self):
        """Regression pin for the most destructive glob trap so far.

        durable_active_count unlinks any *.json that fails its schema check.
        A sibling journal head (`<name>.head.json`) would have been destroyed
        on every count. The helper's subdirectory placement keeps it out of
        that glob entirely.
        """
        self.registry.start("req-1", "claude")
        journal = shadow_journal.journal_path_for(self._durable_path("req-1"))
        head = journal.with_name(journal.name + ".head.json")

        count = durable_active_count(self.root, is_pid_alive=lambda _pid: True)

        self.assertEqual(count, 1)
        self.assertTrue(journal.exists(), "journal segment was swept away")
        self.assertTrue(head.exists(), "journal head cache was swept away")

    def test_the_sweep_still_counts_only_real_sessions(self):
        self.registry.start("req-1", "claude")
        self.registry.start("req-2", "codex")

        self.assertEqual(
            durable_active_count(self.root, is_pid_alive=lambda _pid: True), 2
        )

    def test_a_dead_owner_is_still_reaped_and_tombstoned_consistently(self):
        """A record for a dead pid is removed by the sweep, not by us.

        The shadow is deliberately *not* tombstoned here: the sweep is an
        external reaper, not the owning module recording its own transition.
        The contradiction it produces is real and worth surfacing -- a record
        vanished without the owner saying so.
        """
        self.registry.start("req-1", "claude")
        path = self._durable_path("req-1")

        removed = durable_active_count(self.root, is_pid_alive=lambda _pid: False)

        self.assertEqual(removed, 0)
        self.assertFalse(path.exists())
        report = shadow_journal.contradiction_report(path, lambda: {})
        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(report["shadow"]["pid"], 4242)

    def test_an_out_of_band_write_is_reported(self):
        self.registry.start("req-1", "claude")
        path = self._durable_path("req-1")
        path.write_text(
            json.dumps({"schema_version": 1, "pid": 999, "provider": "other"}),
            encoding="utf-8",
        )

        def read_legacy() -> dict[str, object]:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {key: data[key] for key in ("schema_version", "pid", "provider")}

        report = shadow_journal.contradiction_report(path, read_legacy)

        self.assertIsNotNone(report)
        self.assertIn("pid", report["mismatched_fields"])


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
