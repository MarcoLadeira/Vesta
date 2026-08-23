"""#613 Stage 2: packaged-update state mirrors into the shadow journal.

``opai/update/storage.py`` is Stage 1's "operations: packaged update state and
fencing" -- added to the inventory by #712 while Stage 2 was in progress.

It is the first migrated module that keeps **several distinct documents**
rather than one: policy, in-flight operation, trust floors, and the updater
lease. Each already has its own lock and its own atomic write, so each simply
gets its own journal; the helper is per-path, so nothing new was needed.

The validator is deliberately permissive (any mapping). Four documents with
four different schemas already validate themselves on read, and a stricter
mirror-side validator would silently drop a record the module itself
considers valid -- adding the failure mode #613 exists to remove.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opaihub import shadow_journal
from opai.update.storage import UpdaterPaths, UpdateStore


class UpdateStorageShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.store = UpdateStore(paths=UpdaterPaths.for_home(self.home))

    def test_policy_writes_are_mirrored_and_agree(self):
        policy = self.store.load_policy()
        self.store.save_policy(policy)

        projection = shadow_journal.projection(self.store.paths.policy)

        self.assertTrue(projection)
        report = shadow_journal.contradiction_report(
            self.store.paths.policy,
            lambda: json.loads(self.store.paths.policy.read_text(encoding="utf-8")),
        )
        self.assertIsNone(report)

    def test_the_metadata_floor_is_mirrored(self):
        """Anti-rollback high-water marks are the value worth reconstructing.

        A lost or reset floor is exactly what an attacker replaying an old
        signed metadata document would want, so it must survive the file.
        """
        self.store.record_metadata_version("stable", 7)

        projection = shadow_journal.projection(self.store.paths.trust)

        self.assertEqual(projection["metadata_versions"]["stable"], 7)

    def test_a_floor_only_moves_forward_on_both_sides(self):
        self.store.record_metadata_version("stable", 9)
        self.store.record_metadata_version("stable", 3)

        projection = shadow_journal.projection(self.store.paths.trust)

        self.assertEqual(projection["metadata_versions"]["stable"], 9)
        self.assertEqual(self.store.load_metadata_floor("stable"), 9)

    def test_each_document_gets_its_own_journal(self):
        """Four documents, four journals -- never one shared history."""
        self.store.save_policy(self.store.load_policy())
        self.store.record_metadata_version("stable", 2)

        policy_journal = shadow_journal.journal_path_for(self.store.paths.policy)
        trust_journal = shadow_journal.journal_path_for(self.store.paths.trust)

        self.assertNotEqual(policy_journal, trust_journal)
        self.assertTrue(policy_journal.exists())
        self.assertTrue(trust_journal.exists())

    def test_an_out_of_band_trust_write_is_reported(self):
        self.store.record_metadata_version("stable", 5)
        self.store.paths.trust.write_text(
            json.dumps({"schema_version": 1, "metadata_versions": {"stable": 1}}),
            encoding="utf-8",
        )

        report = shadow_journal.contradiction_report(
            self.store.paths.trust,
            lambda: json.loads(self.store.paths.trust.read_text(encoding="utf-8")),
        )

        self.assertIsNotNone(report)
        self.assertIn("metadata_versions", report["mismatched_fields"])

    def test_an_untouched_document_agrees_as_both_empty(self):
        self.assertEqual(shadow_journal.projection(self.store.paths.operation), {})
        self.assertIsNone(
            shadow_journal.contradiction_report(self.store.paths.operation, lambda: {})
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
