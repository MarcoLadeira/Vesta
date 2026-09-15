"""A receipt shows the evidence behind its verdict (#295 gate 14).

The gate asks for receipt completeness. Receipts carried route, cost, model and
the verdict — but not the *evidence* that verdict rested on, nor what the turn
was authorised to do. A receipt that states an outcome without either asks the
user to take Vesta's word for it, which is the opposite of what a receipt is for.

#539 made the verdict trust only evidence Vesta actually observed. This is the
other half: letting the user see that evidence and check the reasoning.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import FakeStreamingRunner, make_repo

from vestahub import gui_pipeline
from vestahub.gui_pipeline import _authority_record


class AuthorityRecordTests(unittest.TestCase):
    def test_no_grants_records_an_empty_but_explicit_list(self) -> None:
        record = _authority_record(command_grant=None, edit_grant=False, mode="explain")
        self.assertEqual(record["approvals"], [])
        # "nothing was approved" and "approvals were not recorded" are very
        # different claims; only the flag makes the first one honest.
        self.assertTrue(record["approvals_recorded"])
        self.assertEqual(record["mode"], "explain")

    def test_a_command_grant_is_recorded_with_its_scope(self) -> None:
        record = _authority_record(
            command_grant="pytest -q", edit_grant=False, mode="implement"
        )
        self.assertEqual(len(record["approvals"]), 1)
        approval = record["approvals"][0]
        self.assertEqual(approval["kind"], "command_once")
        self.assertIn("pytest", approval["detail"])
        self.assertIn("turn", approval["scope"])

    def test_an_edit_grant_is_recorded(self) -> None:
        record = _authority_record(
            command_grant=None, edit_grant=True, mode="implement"
        )
        self.assertEqual([a["kind"] for a in record["approvals"]], ["edits_once"])

    def test_both_grants_are_recorded_separately(self) -> None:
        record = _authority_record(
            command_grant="gh pr create", edit_grant=True, mode="ship"
        )
        self.assertEqual(
            sorted(a["kind"] for a in record["approvals"]),
            ["command_once", "edits_once"],
        )

    def test_a_secret_in_an_approved_command_is_redacted(self) -> None:
        # The command was typed by a human and can carry a token in a flag. A
        # receipt is durable, so it must not become where a secret comes to rest.
        record = _authority_record(
            command_grant="curl -H 'Authorization: Bearer sk-live-abcdef123456' https://x",
            edit_grant=False,
            mode="implement",
        )
        detail = record["approvals"][0]["detail"]
        self.assertNotIn("sk-live-abcdef123456", detail)

    def test_a_pathological_command_is_bounded(self) -> None:
        record = _authority_record(
            command_grant="x" * 5000, edit_grant=False, mode="implement"
        )
        self.assertLessEqual(len(record["approvals"][0]["detail"]), 300)


class ReceiptCarriesEvidenceTests(unittest.TestCase):
    def _run(self, task: str, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"a.py": "x = 1\n"}, commit=True)
            return gui_pipeline.handle_gui_message(
                root,
                task,
                model_id="claude:opus",
                mode=kw.pop("mode", "ask"),
                account_runner=FakeStreamingRunner(chunks=["It is a CLI."]),
                **kw,
            )

    def test_the_receipt_carries_the_evidence_the_verdict_used(self) -> None:
        receipt = self._run("What does this repo do?").get("receipt") or {}
        self.assertIn("evidence", receipt)
        kinds = {item["kind"] for item in receipt["evidence"]}
        self.assertIn("answer", kinds)

    def test_every_evidence_entry_is_self_describing(self) -> None:
        # A bare kind with no summary would be a label, not evidence.
        receipt = self._run("What does this repo do?").get("receipt") or {}
        for item in receipt.get("evidence") or []:
            with self.subTest(kind=item.get("kind")):
                self.assertTrue(str(item.get("kind") or "").strip())
                self.assertTrue(str(item.get("summary") or "").strip())

    def test_the_receipt_records_what_the_turn_was_authorised_to_do(self) -> None:
        receipt = self._run("What does this repo do?").get("receipt") or {}
        authority = receipt.get("authority") or {}
        self.assertTrue(authority.get("approvals_recorded"))
        self.assertEqual(authority.get("approvals"), [])

    def test_an_approved_command_reaches_the_receipt(self) -> None:
        receipt = (
            self._run("Run the tests", allow_command="pytest -q").get("receipt") or {}
        )
        approvals = (receipt.get("authority") or {}).get("approvals") or []
        self.assertEqual([a["kind"] for a in approvals], ["command_once"])

    def test_the_receipt_still_carries_no_raw_prompt(self) -> None:
        # The privacy promise the receipt makes in its own text.
        secret_task = "Explain why my-super-secret-marker matters"
        receipt = self._run(secret_task).get("receipt") or {}
        self.assertNotIn("my-super-secret-marker", str(receipt))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
