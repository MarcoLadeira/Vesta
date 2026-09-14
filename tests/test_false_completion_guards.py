"""A provider cannot manufacture a completed run (#295 gate 1, #539).

Gate 1 wants false completion to be **technically impossible**, and #539 states
it as an explicit acceptance criterion: *"Tests prove providers and presentation
layers cannot directly set a verified-completion result."*

`evaluate_completion`'s docstring already promised it — *"Only a canonical
terminal state and evidence produced by Vesta's execution path can return
COMPLETED"* — but nothing held it to that:

- `_has_successful_test` accepted a bare `{"tests": {"status": "passed"}}`
  mapping. A status string with no provenance was enough to satisfy the
  `tests_pass` acceptance requirement and stamp the run **completed**.
- The call site built the verdict's input as `{**payload, ...}`, spreading the
  whole provider-influenced result, so whether that key could be reached
  depended on which fields happened to exist rather than on the design.

Nothing populates `tests` on the live path today, so it was not exploitable.
That is exactly the distinction these tests exist to remove: "unreachable right
now" is one refactor away from "reachable", and the gate asks for impossible.
"""

from __future__ import annotations

import unittest

from opaihub.completion import (
    MEASURED_EVIDENCE_KEYS,
    CompletionVerdict,
    evaluate_completion,
    evidence_payload,
    objective_from_request,
)


def _edit_objective(text: str = "Fix the parser and run the tests"):
    return objective_from_request(text, mode="implement")


class SelfReportCannotCompleteTests(unittest.TestCase):
    """Claims are not evidence, however they are spelled."""

    def test_a_self_reported_test_pass_does_not_complete_a_run(self) -> None:
        # The regression: this returned COMPLETED on a status string alone.
        objective = _edit_objective()
        verdict = evaluate_completion(
            objective,
            {
                "status": "answered",
                "answer": "Fixed it and the tests pass.",
                "changed_files": ["parser.py"],
                "tests": {"status": "passed"},
            },
        )
        self.assertIsNot(verdict.verdict, CompletionVerdict.COMPLETED)
        self.assertEqual(verdict.reason_code, "tests_not_verified")

    def test_the_legacy_test_results_spelling_is_equally_powerless(self) -> None:
        verdict = evaluate_completion(
            _edit_objective(),
            {
                "status": "answered",
                "answer": "done",
                "changed_files": ["parser.py"],
                "test_results": {"result": "success"},
            },
        )
        self.assertIsNot(verdict.verdict, CompletionVerdict.COMPLETED)

    def test_prose_claiming_success_is_not_evidence(self) -> None:
        verdict = evaluate_completion(
            _edit_objective(),
            {
                "status": "answered",
                "answer": "All done — I ran the full suite and everything passes.",
                "changed_files": ["parser.py"],
            },
        )
        self.assertIsNot(verdict.verdict, CompletionVerdict.COMPLETED)
        self.assertEqual(verdict.reason_code, "tests_not_verified")

    def test_a_provider_supplied_verdict_is_ignored_entirely(self) -> None:
        # The bluntest attempt: stamp the terminal truth directly.
        verdict = evaluate_completion(
            _edit_objective(),
            {
                "status": "answered",
                "answer": "done",
                "changed_files": ["parser.py"],
                "completion_verdict": {"verdict": "completed", "reason": "trust me"},
            },
        )
        self.assertIsNot(verdict.verdict, CompletionVerdict.COMPLETED)


class ObservedEvidenceStillCompletesTests(unittest.TestCase):
    """The guard must not break real verification, or it will be removed."""

    def test_opais_own_tool_trace_completes_the_run(self) -> None:
        # A tool_trace entry records something Vesta actually executed.
        verdict = evaluate_completion(
            _edit_objective(),
            {
                "status": "answered",
                "answer": "Fixed.",
                "changed_files": ["parser.py"],
                "tool_trace": [{"tool": "run_tests", "ok": True}],
            },
        )
        self.assertIs(verdict.verdict, CompletionVerdict.COMPLETED)

    def test_a_structured_check_with_an_observed_exit_status_completes(self) -> None:
        # #539's check runner: exit status is the part a claim cannot fabricate,
        # because only the process that ran the command can report it.
        verdict = evaluate_completion(
            _edit_objective(),
            {
                "status": "answered",
                "answer": "Fixed.",
                "changed_files": ["parser.py"],
                "verification": {
                    "checks": [
                        {"kind": "tests", "exit_status": 0, "observed_by": "opai"}
                    ]
                },
            },
        )
        self.assertIs(verdict.verdict, CompletionVerdict.COMPLETED)

    def test_a_failing_check_does_not_complete(self) -> None:
        verdict = evaluate_completion(
            _edit_objective(),
            {
                "status": "answered",
                "answer": "Fixed.",
                "changed_files": ["parser.py"],
                "verification": {
                    "checks": [
                        {"kind": "tests", "exit_status": 1, "observed_by": "opai"}
                    ]
                },
            },
        )
        self.assertIsNot(verdict.verdict, CompletionVerdict.COMPLETED)

    def test_a_check_nobody_admits_to_running_does_not_count(self) -> None:
        # Without `observed_by`, the record is indistinguishable from a claim.
        verdict = evaluate_completion(
            _edit_objective(),
            {
                "status": "answered",
                "answer": "Fixed.",
                "changed_files": ["parser.py"],
                "verification": {"checks": [{"kind": "tests", "exit_status": 0}]},
            },
        )
        self.assertIsNot(verdict.verdict, CompletionVerdict.COMPLETED)

    def test_an_answer_only_run_still_completes_on_its_answer(self) -> None:
        # The gate's "explicitly defined non-mutating answer-only contract".
        objective = objective_from_request("What does this repo do?", mode="explain")
        verdict = evaluate_completion(
            objective, {"status": "answered", "answer": "It is a CLI for X."}
        )
        self.assertIs(verdict.verdict, CompletionVerdict.COMPLETED)


class EvidenceAllowlistTests(unittest.TestCase):
    """The structural half: unknown fields never reach the verdict."""

    def test_only_allowlisted_keys_survive(self) -> None:
        built = evidence_payload(
            {
                "answer": "hi",
                "changed_files": ["a.py"],
                "tests": {"status": "passed"},
                "completion_verdict": {"verdict": "completed"},
                "some_future_field": "anything",
            }
        )
        self.assertEqual(set(built), {"answer", "changed_files"})

    def test_extra_is_also_allowlisted(self) -> None:
        # The call site's own overrides get the same treatment, so a typo or a
        # well-meaning addition there cannot smuggle a field in either.
        built = evidence_payload(
            {"answer": "hi"}, extra={"repo_change": {"changed": True}, "sneaky": 1}
        )
        self.assertIn("repo_change", built)
        self.assertNotIn("sneaky", built)

    def test_provider_cannot_supply_a_verification_manifest(self) -> None:
        provider_claim = {"digest": "a" * 64, "checks": []}

        self.assertNotIn(
            "verification_manifest",
            evidence_payload({"verification_manifest": provider_claim}),
        )
        pipeline_evidence = evidence_payload(
            {"verification_manifest": provider_claim},
            extra={"verification_manifest": {"digest": "b" * 64}},
        )

        self.assertEqual(
            pipeline_evidence["verification_manifest"], {"digest": "b" * 64}
        )

    def test_the_allowlist_excludes_every_claim_shaped_key(self) -> None:
        # Named explicitly so adding one back is a visible, deliberate act.
        for key in (
            "tests",
            "test_results",
            "completion_verdict",
            "verdict",
            "verified",
            "success",
        ):
            with self.subTest(key=key):
                self.assertNotIn(key, MEASURED_EVIDENCE_KEYS)

    def test_extra_overrides_the_measured_value(self) -> None:
        # The pipeline recomputes changed_files from the repository itself; its
        # value must win over whatever the result carried.
        built = evidence_payload(
            {"changed_files": ["claimed.py"]}, extra={"changed_files": ["real.py"]}
        )
        self.assertEqual(built["changed_files"], ["real.py"])


class PipelineWiringTests(unittest.TestCase):
    def test_the_pipeline_builds_the_verdict_payload_by_allowlist(self) -> None:
        # A direct spread here would silently reopen the hole.
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "opaihub" / "gui_pipeline.py"
        ).read_text(encoding="utf-8")
        self.assertIn("build_evidence_payload(", source)
        self.assertNotIn("evidence_payload = {\n            **payload,", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class ManifestHonestyTests(unittest.TestCase):
    """A verification problem must name itself accurately, and not mask the run.

    Both regressions here had `main` red. The pipeline stored an
    `{"integrity_errors": [...]}` dict under the *manifest* key when manifest
    creation failed. That is not a manifest, so the verdict tried to validate it,
    failed, and reported "verification evidence could not be validated" — naming
    the wrong problem (creation, not validation) and replacing the run's own more
    specific reason with a vaguer one.
    """

    def _edit_objective(self):
        return objective_from_request("Fix the parser", mode="implement")

    def test_a_creation_failure_says_unavailable_not_invalid(self) -> None:
        verdict = evaluate_completion(
            self._edit_objective(),
            {
                "status": "answered",
                "answer": "done",
                "changed_files": ["parser.py"],
                "verification_manifest": {"creation_error": "disk full"},
            },
        )
        self.assertEqual(verdict.reason_code, "verification_unavailable")
        self.assertIn("disk full", verdict.reason)

    def test_a_missing_edit_outranks_a_verification_problem(self) -> None:
        # The run changed nothing. That is the actionable truth; a harness
        # problem reported instead sends the user to debug Vesta.
        verdict = evaluate_completion(
            self._edit_objective(),
            {
                "status": "answered",
                "answer": "I changed it.",
                "changed_files": [],
                "verification_manifest": {"creation_error": "disk full"},
            },
        )
        self.assertEqual(verdict.reason_code, "change_not_verified")

    def test_verification_still_governs_once_the_edit_landed(self) -> None:
        # The reorder must not let a broken manifest through when work exists.
        verdict = evaluate_completion(
            self._edit_objective(),
            {
                "status": "answered",
                "answer": "done",
                "changed_files": ["parser.py"],
                "verification_manifest": {"schema_version": 1, "not": "a manifest"},
            },
        )
        self.assertIsNot(verdict.verdict, CompletionVerdict.COMPLETED)
        self.assertEqual(verdict.reason_code, "verification_invalid")

    def test_an_empty_manifest_is_not_treated_as_a_failed_one(self) -> None:
        # `{}` means the harness never ran, not that verification failed.
        verdict = evaluate_completion(
            objective_from_request("What does this do?", mode="explain"),
            {"status": "answered", "answer": "It parses.", "verification_manifest": {}},
        )
        self.assertIs(verdict.verdict, CompletionVerdict.COMPLETED)
