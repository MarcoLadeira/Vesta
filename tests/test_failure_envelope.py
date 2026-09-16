"""Structured failure diagnosis (#569).

Replaces the single opaque "provider failed" string the report calls out. The
two properties that make the envelope trustworthy are pinned hardest here:
unknown stays unknown, and evidence never carries a credential.
"""

from __future__ import annotations

import json
import unittest

from vestahub.failure_envelope import (
    EvidenceCollector,
    FailureCategory,
    FailureEnvelope,
    FailureEvidence,
    classify_failure,
)


class ClassificationTests(unittest.TestCase):
    def test_the_reports_own_example_is_a_compatibility_failure(self) -> None:
        # The worked example: `gh issue view` against a removed Projects field.
        self.assertIs(
            classify_failure(
                "Error: Unknown field 'projects' (projects_classic_removed)"
            ),
            FailureCategory.TOOL_COMPATIBILITY,
        )

    def test_each_category_matches_its_signature(self) -> None:
        cases = {
            "HTTP 401 Unauthorized": FailureCategory.AUTHENTICATION,
            "gh auth login required": FailureCategory.AUTHENTICATION,
            "429 rate limit exceeded": FailureCategory.QUOTA_OR_RATE_LIMIT,
            "You have insufficient quota": FailureCategory.QUOTA_OR_RATE_LIMIT,
            "403 Forbidden: protected branch": FailureCategory.PERMISSION_DENIED,
            "connection refused": FailureCategory.NETWORK,
            "Could not resolve host: api.example.com": FailureCategory.NETWORK,
            "flag is no longer supported": FailureCategory.TOOL_COMPATIBILITY,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertIs(classify_failure(text), expected)

    def test_unrecognised_text_stays_unknown(self) -> None:
        # The rule that matters: no fuzzy "closest category". Recovery acts on
        # this value, so a confident wrong answer is worse than an admitted one.
        for text in ("", "   ", "something went sideways", "�����"):
            with self.subTest(text=text):
                self.assertIs(classify_failure(text), FailureCategory.UNKNOWN)

    def test_the_most_specific_signature_wins(self) -> None:
        # Real provider errors bundle several words; a rate-limit message that
        # also says "error" must not degrade to a generic classification.
        self.assertIs(
            classify_failure("Error: request failed, 429 rate limit exceeded"),
            FailureCategory.QUOTA_OR_RATE_LIMIT,
        )


class EnvelopeTests(unittest.TestCase):
    def test_diagnose_builds_a_json_safe_envelope(self) -> None:
        envelope = FailureEnvelope.diagnose(
            error_text="Error: Unknown field 'projects'",
            tool="github-cli",
            tool_version="2.80.0",
            evidence=[
                FailureEvidence(
                    step=17, action="gh issue view", detail="GraphQL failed"
                )
            ],
            recovery_attempted=["gh api fallback"],
            outcome="continued_with_alternate_adapter",
            progress={"score": 12, "steps_since_best": 4},
        )
        payload = envelope.to_dict()
        json.dumps(payload)  # must not raise
        self.assertEqual(payload["primary_cause"], "tool_compatibility")
        self.assertEqual(payload["tool_version"], "2.80.0")
        self.assertEqual(payload["recovery_attempted"], ["gh api fallback"])
        self.assertEqual(payload["progress"]["score"], 12)
        self.assertTrue(payload["actionable"])

    def test_unknown_and_cancelled_are_not_actionable(self) -> None:
        self.assertFalse(FailureEnvelope.diagnose(error_text="???").is_actionable)
        self.assertFalse(
            FailureEnvelope.diagnose(category=FailureCategory.CANCELLED).is_actionable
        )

    def test_every_category_has_a_user_message_that_is_not_provider_failed(
        self,
    ) -> None:
        for category in FailureCategory:
            with self.subTest(category=category):
                message = FailureEnvelope.diagnose(category=category).user_message()
                self.assertTrue(message.strip())
                self.assertNotEqual(message.lower(), "provider failed")

    def test_an_explicit_category_overrides_text_classification(self) -> None:
        # Stagnation is decided by the controller's evidence ledger, not by
        # sniffing an error string that may not exist at all.
        envelope = FailureEnvelope.diagnose(
            error_text="401 unauthorized", category=FailureCategory.NO_PROGRESS
        )
        self.assertIs(envelope.primary_cause, FailureCategory.NO_PROGRESS)

    def test_the_envelope_is_immutable(self) -> None:
        envelope = FailureEnvelope.diagnose(error_text="429 rate limit")
        with self.assertRaises(Exception):
            envelope.primary_cause = FailureCategory.NETWORK  # type: ignore[misc]


class RedactionTests(unittest.TestCase):
    """Evidence quotes raw tool output — the likeliest place for a credential."""

    SECRET = "AIzaSyA1234567890abcdefghijklmnopqrstuv"

    def test_a_credential_in_evidence_detail_is_redacted(self) -> None:
        item = FailureEvidence(step=3, action="curl", detail=f"used key {self.SECRET}")
        self.assertNotIn(self.SECRET, json.dumps(item.to_dict()))

    def test_a_credential_in_the_error_signature_is_redacted(self) -> None:
        envelope = FailureEnvelope.diagnose(error_text=f"401 with {self.SECRET}")
        self.assertNotIn(self.SECRET, json.dumps(envelope.to_dict()))

    def test_evidence_detail_is_bounded(self) -> None:
        item = FailureEvidence(step=1, action="read", detail="x" * 10_000)
        self.assertLessEqual(len(item.to_dict()["detail"]), 400)


class EvidenceCollectorTests(unittest.TestCase):
    def test_successes_are_not_evidence_of_failure(self) -> None:
        collector = EvidenceCollector()
        collector.observe(1, "read_file", ok=True)
        self.assertEqual(collector.as_tuple(), ())
        self.assertIsNone(collector.first_failure_step)

    def test_the_first_failing_step_is_remembered(self) -> None:
        # "Critical-error localization: identifies the first failing tool."
        collector = EvidenceCollector()
        collector.observe(4, "read_file", ok=True)
        collector.observe(5, "gh issue view", ok=False, detail="GraphQL failed")
        collector.observe(6, "gh issue view", ok=False, detail="GraphQL failed")
        self.assertEqual(collector.first_failure_step, 5)

    def test_evidence_is_bounded_and_keeps_the_earliest(self) -> None:
        # Later noise is usually a consequence of the first fault, so the cap
        # must not discard the cause in favour of the symptoms.
        collector = EvidenceCollector()
        for step in range(50):
            collector.observe(step, f"action{step}", ok=False, detail="boom")
        items = collector.as_tuple()
        self.assertLessEqual(len(items), 10)
        self.assertEqual(items[0].step, 0)


if __name__ == "__main__":
    unittest.main()
