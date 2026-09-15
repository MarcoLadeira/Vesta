"""#613 Stage 2: verification_policy's shadow journal, and the one record that verifies itself.

Stage 1 named ``vestahub/verification_policy.py`` JOURNAL_OWNED -- "artifacts:
verification policy state". The artifact records *which verification a run was
dispatched under*: it is written immediately before provider dispatch, and
``verification_execution`` later refuses to produce a verdict without a policy
digest. Losing or altering it does not merely lose status, it detaches a run's
result from the rules it was supposed to have been judged by.

That gives this migration a property none of the previous eleven had. Those
validators could only check a record's *shape*, because nothing in those
records proved anything about their own content -- so the shadow was only ever
as trustworthy as the journal file itself. A policy carries a SHA-256 of its
own payload, so a mirrored event can be checked against itself. These tests pin
that a journal event altered after the fact is skipped on replay rather than
served as fact.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from vestahub import shadow_journal
from vestahub.state import state_dir
from vestahub.verification_policy import (
    persist_effective_policy,
    policy_artifact_contradiction_report,
    policy_artifact_projection,
    resolve_verification_policy,
)


class _PolicyFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _policy(self, *, task: str = "fix the failing login test", mode: str = "fix"):
        return resolve_verification_policy(self.root, task=task, mode=mode)

    def _persist(self, *, task_id: str = "task-a", run_id: str = "run-a", **kwargs):
        policy = self._policy(**kwargs)
        ref = persist_effective_policy(
            self.root, policy, task_id=task_id, run_id=run_id
        )
        return policy, ref

    def _artifact_path(self, *, task_id: str = "task-a", run_id: str = "run-a") -> Path:
        return (
            state_dir(self.root) / "verification-policies" / task_id / f"{run_id}.json"
        )


class ShadowMirrorsPersistedArtifactsTests(_PolicyFixture):
    def test_persisting_is_mirrored_and_the_shadow_agrees_with_the_artifact(self):
        policy, _ref = self._persist()

        self.assertEqual(
            policy_artifact_projection(self.root, task_id="task-a", run_id="run-a"),
            policy.to_dict(),
        )
        self.assertIsNone(
            policy_artifact_contradiction_report(
                self.root, task_id="task-a", run_id="run-a"
            )
        )

    def test_the_mirrored_digest_is_the_one_dispatch_bound_to(self):
        """The artifact's whole job: which policy did this run run under."""

        policy, ref = self._persist()
        shadow = policy_artifact_projection(self.root, task_id="task-a", run_id="run-a")

        self.assertEqual(shadow["digest"], ref.digest)
        self.assertEqual(shadow["digest"], policy.digest)

    def test_two_runs_of_one_task_keep_separate_shadows(self):
        first, _ = self._persist(run_id="run-a")
        second, _ = self._persist(
            run_id="run-b", task="add a README section", mode="explain"
        )

        self.assertEqual(
            policy_artifact_projection(self.root, task_id="task-a", run_id="run-a"),
            first.to_dict(),
        )
        self.assertEqual(
            policy_artifact_projection(self.root, task_id="task-a", run_id="run-b"),
            second.to_dict(),
        )
        self.assertIsNone(
            policy_artifact_contradiction_report(
                self.root, task_id="task-a", run_id="run-a"
            )
        )

    def test_rewriting_the_same_run_moves_both_sides_together(self):
        self._persist(task="add a README section", mode="explain")
        second, _ = self._persist(task="fix the failing login test", mode="fix")

        self.assertEqual(
            policy_artifact_projection(self.root, task_id="task-a", run_id="run-a"),
            second.to_dict(),
        )
        self.assertIsNone(
            policy_artifact_contradiction_report(
                self.root, task_id="task-a", run_id="run-a"
            )
        )

    def test_a_refused_persist_writes_neither_side(self):
        with self.assertRaises(ValueError):
            self._persist(task_id="../escape")

        self.assertEqual(
            policy_artifact_projection(self.root, task_id="task-a", run_id="run-a"), {}
        )


class TheRecordVerifiesItselfTests(_PolicyFixture):
    """The property no earlier #613 migration could assert.

    Every other module's validator checks shape. A policy carries a SHA-256 of
    its own payload, so an event altered *inside the journal* -- past the
    legacy file, past the lock, past everything the other migrations rely on --
    is still caught.
    """

    def _rewrite_last_event(self, mutate) -> None:
        journal = shadow_journal.journal_path_for(self._artifact_path())
        lines = journal.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[-1])
        mutate(event["record"])
        lines[-1] = json.dumps(event)
        journal.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_a_tampered_journal_event_is_skipped_rather_than_served(self):
        self._persist(task="fix the failing login test", mode="fix")

        def flip(record):
            # Flip the outcome the artifact exists to bind: the recorded
            # policy rewritten to demand nothing, digest left untouched.
            record["status"] = "ready"
            record["checks"] = []

        self._rewrite_last_event(flip)

        shadow = policy_artifact_projection(self.root, task_id="task-a", run_id="run-a")

        self.assertNotEqual(shadow.get("checks"), [])

    def test_a_tampered_event_with_a_recomputed_digest_still_contradicts_the_file(self):
        """Defence in depth: forging the digest still fails the file comparison.

        A tamperer who recomputes the digest produces a self-consistent event,
        so the validator accepts it -- and the dual-read comparator is what
        catches it. Neither check subsumes the other, which is why #613 keeps
        both a validator and a comparator rather than picking one.
        """

        self._persist()

        def forge(record):
            record["checks"] = []
            payload = {key: value for key, value in record.items() if key != "digest"}
            record["digest"] = sha256(
                json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("utf-8")
            ).hexdigest()

        self._rewrite_last_event(forge)

        report = policy_artifact_contradiction_report(
            self.root, task_id="task-a", run_id="run-a"
        )

        self.assertIsNotNone(report)
        self.assertIn("checks", report["mismatched_fields"])
        self.assertEqual(report["task_id"], "task-a")
        self.assertEqual(report["run_id"], "run-a")

    def test_a_valid_record_is_not_rejected_by_the_digest_check(self):
        """Teeth the other way: the strict validator must not eat real records."""

        policy, _ = self._persist()

        self.assertEqual(
            policy_artifact_projection(self.root, task_id="task-a", run_id="run-a"),
            policy.to_dict(),
        )


class ContradictionReportIsExactTests(_PolicyFixture):
    def test_an_out_of_band_artifact_write_is_reported(self):
        """The scenario #613 exists for: something wrote the artifact directly."""

        policy, _ = self._persist()
        tampered = {**policy.to_dict(), "checks": []}
        self._artifact_path().write_text(json.dumps(tampered), encoding="utf-8")

        report = policy_artifact_contradiction_report(
            self.root, task_id="task-a", run_id="run-a"
        )

        self.assertIsNotNone(report)
        self.assertIn("checks", report["mismatched_fields"])
        self.assertEqual(report["legacy"], tampered)

    def test_a_deleted_artifact_is_reported_against_a_surviving_shadow(self):
        """The loss case: the artifact is gone, the journal still knows."""

        policy, _ = self._persist()
        self._artifact_path().unlink()

        report = policy_artifact_contradiction_report(
            self.root, task_id="task-a", run_id="run-a"
        )

        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(report["shadow"], policy.to_dict())

    def test_a_never_persisted_run_agrees_as_both_empty(self):
        self.assertIsNone(
            policy_artifact_contradiction_report(
                self.root, task_id="task-a", run_id="never-run"
            )
        )
        self.assertEqual(
            policy_artifact_projection(self.root, task_id="task-a", run_id="never-run"),
            {},
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
