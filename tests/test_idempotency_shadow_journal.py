"""#613 Stage 2: operation claims mirror into the shadow journal.

``opaihub/idempotency.py`` is the claim store behind exactly-once external
operations -- the thing that stops a retried push or PR from happening twice.
Stage 1 classifies it under ``operations``.

Like ``scheduler``, it keeps one document rather than one file per record, so
the journalled record is the whole store. Unlike scheduler, it already fails
closed: ``_load`` raises ``OperationPersistenceError`` on a corrupt or
invalid-schema store rather than degrading to "no claims". That is the
correct behaviour for a claim store -- treating corruption as "nothing is
claimed" would authorise a duplicate paid operation -- and the mirror is
added without weakening it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from opaihub import idempotency


class OperationClaimShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.key = idempotency.operation_key("push", repo="acme/app", sha="abc123")

    def _store_path(self) -> Path:
        return idempotency._path(self.root)

    def test_a_claim_is_mirrored_and_agrees(self):
        idempotency.begin(self.root, self.key)

        projection = idempotency.shadow_journal_projection(self.root)

        self.assertIn(self.key, projection["operations"])
        self.assertIsNone(idempotency.operation_contradiction_report(self.root))

    def test_completion_is_mirrored(self):
        idempotency.begin(self.root, self.key)
        idempotency.complete(self.root, self.key, result={"ok": True})

        projection = idempotency.shadow_journal_projection(self.root)

        self.assertEqual(projection["operations"][self.key]["state"], "done")
        self.assertIsNone(idempotency.operation_contradiction_report(self.root))

    def test_abandoning_the_last_claim_is_recorded_not_dropped(self):
        """An empty store is a state change worth mirroring.

        A validator requiring a non-empty map would drop this and leave the
        shadow asserting a claim that no longer exists -- the same trap met in
        checkpoints (`pending`), agent runtime (`idle`) and scheduler.
        """
        idempotency.begin(self.root, self.key)
        idempotency.abandon(self.root, self.key)

        projection = idempotency.shadow_journal_projection(self.root)

        self.assertEqual(projection["operations"], {})
        self.assertIsNone(idempotency.operation_contradiction_report(self.root))

    def test_an_out_of_band_write_is_reported(self):
        idempotency.begin(self.root, self.key)
        self._store_path().write_text(
            json.dumps({"someone-elses-key": {"state": "done"}}), encoding="utf-8"
        )

        report = idempotency.operation_contradiction_report(self.root)

        self.assertIsNotNone(report)
        self.assertIn("operations", report["mismatched_fields"])

    def test_a_corrupt_store_is_reported_and_still_fails_closed(self):
        """Corruption must never read as "nothing is claimed".

        For a claim store that would authorise a duplicate paid operation.
        This module already handles it correctly and better than raising:
        status() reports ``in_flight`` with ``persistence_blocked``, which
        refuses to grant a fresh claim without pretending to know the truth.
        The mirror is added without weakening that -- and the shadow still
        holds what was genuinely claimed, so the report makes the gap visible
        rather than leaving the operator to guess.
        """
        idempotency.begin(self.root, self.key)
        self._store_path().write_text('{"broken', encoding="utf-8")

        blocked = idempotency.status(self.root, self.key)

        self.assertEqual(blocked["state"], "in_flight")
        self.assertTrue(blocked["persistence_blocked"])

        report = idempotency.operation_contradiction_report(self.root)
        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertIn(self.key, report["shadow"]["operations"])

    def test_a_never_used_store_agrees_as_both_empty(self):
        self.assertIsNone(idempotency.operation_contradiction_report(self.root))
        self.assertEqual(idempotency.shadow_journal_projection(self.root), {})

    def test_concurrent_claims_leave_file_and_shadow_agreeing(self):
        def claim(index: int) -> None:
            try:
                idempotency.begin(
                    self.root, idempotency.operation_key("push", n=str(index))
                )
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(claim, range(12)))

        self.assertIsNone(idempotency.operation_contradiction_report(self.root))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
