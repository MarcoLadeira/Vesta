"""#656: honest recovery UX, and the defect it exists to remove.

The issue opens with a captured run:

    a timeline row saying the no-progress guard stopped the run; another row
    saying Vesta stopped without finishing; **a terminal card blaming the
    provider**; a single Retry button with unclear semantics.

Three separate failures in one card. The provider had produced no evidence of
failing -- the guard stopped the run -- and "Retry" could as easily have meant
"repeat the whole paid investigation" as "repeat the one thing that broke".

So the tests below are organised around what the card must never do:

* never name a cause it has no evidence for;
* never offer an action it cannot honour, without saying why;
* never render differently in the GUI than in the CLI;
* never change its answer when the same run is re-read.

The issue asks for a minimum of fifteen named scenarios. These are those,
grouped by the acceptance criterion each one defends.
"""

from __future__ import annotations

import copy
import unittest

from vestahub.recovery_actions import (
    ASK,
    CONTINUE_FROM_CHECKPOINT,
    RECOVERY_SCHEMA_VERSION,
    REPLAN,
    RETRY_OPERATION,
    START_OVER,
    STATE_BLOCKED,
    STATE_COMPLETED,
    STATE_NEEDS_ATTENTION,
    STATE_PARTIAL_RETAINED,
    STATE_PROVIDER_UNAVAILABLE,
    STATE_STOPPED_WITH_CHECKPOINT,
    STATE_UNKNOWN,
    STATE_WAITING,
    STOP,
    TRY_PROVIDER,
    build_recovery_actions,
    render_recovery_text,
)

ALL_ACTIONS = (
    CONTINUE_FROM_CHECKPOINT,
    RETRY_OPERATION,
    REPLAN,
    TRY_PROVIDER,
    ASK,
    START_OVER,
    STOP,
)


def _verdict(verdict: str, reason_code: str = "", **extra: object) -> dict:
    payload = {"completion_verdict": {"verdict": verdict, "reason_code": reason_code}}
    payload.update(extra)
    return payload


def _actions(recovery: dict) -> dict[str, dict]:
    return {action["id"]: action for action in recovery["actions"]}


class TheMotivatingDefectTests(unittest.TestCase):
    """The captured run from the issue, asserted directly."""

    def test_a_no_progress_stop_is_not_presented_as_a_provider_failure(self):
        """The defect in one line.

        The guard stopped the run. The provider produced no error, no code and
        no status. A card that says "the provider failed" is asserting
        something nobody observed.
        """

        recovery = build_recovery_actions(_verdict("partial", "no_progress_guard"))

        self.assertNotEqual(recovery["state"], STATE_PROVIDER_UNAVAILABLE)
        self.assertEqual(recovery["state"], STATE_PARTIAL_RETAINED)

    def test_the_provider_state_requires_provider_evidence(self):
        """AC: "no longer displays provider failure unless provider evidence exists"."""

        for reason in ("auth", "rate_limit", "network", "provider"):
            with self.subTest(evidence=reason):
                recovery = build_recovery_actions(_verdict("failed", reason))

                self.assertEqual(recovery["state"], STATE_PROVIDER_UNAVAILABLE)

    def test_an_unevidenced_failure_does_not_reach_the_provider_state(self):
        for reason in ("", "unknown", "no_progress_guard", "internal"):
            with self.subTest(reason=reason or "<none>"):
                recovery = build_recovery_actions(_verdict("failed", reason))

                self.assertNotEqual(recovery["state"], STATE_PROVIDER_UNAVAILABLE)

    def test_retry_is_never_offered_as_a_bare_repeat_of_the_run(self):
        """The cost-risk half of the defect.

        "Retry may imply repeating the same expensive investigation." The
        retry action is scoped to one operation and is unavailable unless the
        runtime names an operation known to be safe to repeat.
        """

        recovery = build_recovery_actions(_verdict("failed"))
        retry = _actions(recovery)[RETRY_OPERATION]

        self.assertFalse(retry["available"])
        self.assertIn("single", retry["summary"])
        self.assertTrue(retry["disabled_reason"])


class EveryControlIsDistinctAndAlwaysPresentTests(unittest.TestCase):
    """AC: Continue, operation Retry, Re-plan, provider change, Ask, Start over
    and Stop have distinct canonical commands."""

    def test_all_seven_controls_are_offered(self):
        recovery = build_recovery_actions(_verdict("failed"))

        self.assertEqual(sorted(_actions(recovery)), sorted(ALL_ACTIONS))

    def test_the_ids_are_distinct(self):
        recovery = build_recovery_actions(_verdict("failed"))
        ids = [action["id"] for action in recovery["actions"]]

        self.assertEqual(len(ids), len(set(ids)))

    def test_an_unavailable_action_is_present_rather_than_hidden(self):
        """AC: "Stale/unsafe actions are disabled with reasons".

        Hiding an action and disabling it are different promises. A hidden
        control leaves the user wondering whether it exists; a disabled one
        with a reason tells them what would have to change.
        """

        recovery = build_recovery_actions(_verdict("failed"))

        for action in recovery["actions"]:
            with self.subTest(action=action["id"]):
                if not action["available"]:
                    self.assertTrue(
                        action["disabled_reason"],
                        "an unavailable action must say why",
                    )

    def test_an_available_action_carries_no_disabled_reason(self):
        recovery = build_recovery_actions(_verdict("failed"), provider_count=2)

        for action in recovery["actions"]:
            if action["available"]:
                with self.subTest(action=action["id"]):
                    self.assertEqual(action["disabled_reason"], "")


class ContinueRequiresARealCheckpointTests(unittest.TestCase):
    """AC: "Useful retained work and checkpoint state are shown"."""

    def test_a_checkpoint_makes_continue_available_and_primary(self):
        recovery = build_recovery_actions(
            _verdict("partial", "no_progress_guard", checkpoint_id="ckpt-1")
        )
        action = _actions(recovery)[CONTINUE_FROM_CHECKPOINT]

        self.assertTrue(action["available"])
        self.assertEqual(action["style"], "primary")
        self.assertEqual(recovery["state"], STATE_STOPPED_WITH_CHECKPOINT)

    def test_no_checkpoint_disables_continue_with_a_reason(self):
        recovery = build_recovery_actions(_verdict("partial", "no_progress_guard"))
        action = _actions(recovery)[CONTINUE_FROM_CHECKPOINT]

        self.assertFalse(action["available"])
        self.assertIn("checkpoint", action["disabled_reason"].lower())

    def test_checkpoint_evidence_is_accepted_in_each_recorded_shape(self):
        """The runtime records this three ways; all three are real evidence."""

        for shape in (
            {"checkpoint_id": "ckpt-1"},
            {"resumable": True},
            {"checkpoint": {"id": "ckpt-1"}},
        ):
            with self.subTest(shape=sorted(shape)):
                recovery = build_recovery_actions(_verdict("partial", **shape))

                self.assertTrue(
                    _actions(recovery)[CONTINUE_FROM_CHECKPOINT]["available"]
                )

    def test_a_falsy_checkpoint_is_not_evidence(self):
        """An empty id is absence, not a checkpoint."""

        for shape in (
            {"checkpoint_id": ""},
            {"resumable": False},
            {"checkpoint": {"id": ""}},
        ):
            with self.subTest(shape=sorted(shape)):
                recovery = build_recovery_actions(_verdict("partial", **shape))

                self.assertFalse(
                    _actions(recovery)[CONTINUE_FROM_CHECKPOINT]["available"]
                )


class EveryRequiredStateIsReachableTests(unittest.TestCase):
    """The issue lists the primary states a terminal card must be able to show."""

    def test_stopped_with_checkpoint(self):
        recovery = build_recovery_actions(_verdict("failed", checkpoint_id="c"))
        self.assertEqual(recovery["state"], STATE_STOPPED_WITH_CHECKPOINT)

    def test_blocked_by_policy(self):
        recovery = build_recovery_actions(_verdict("blocked", "permission_required"))
        self.assertEqual(recovery["state"], STATE_BLOCKED)

    def test_partial_result_retained(self):
        recovery = build_recovery_actions(_verdict("partial", "no_progress_guard"))
        self.assertEqual(recovery["state"], STATE_PARTIAL_RETAINED)

    def test_provider_unavailable(self):
        recovery = build_recovery_actions(_verdict("failed", "network"))
        self.assertEqual(recovery["state"], STATE_PROVIDER_UNAVAILABLE)

    def test_needs_attention(self):
        recovery = build_recovery_actions(_verdict("needs_attention", "x"))
        self.assertEqual(recovery["state"], STATE_NEEDS_ATTENTION)

    def test_waiting_for_one_user_answer(self):
        recovery = build_recovery_actions(
            {
                "status": "needs_edit_approval",
                "completion_verdict": {"verdict": "blocked"},
            }
        )
        self.assertEqual(recovery["state"], STATE_WAITING)

    def test_unknown_is_a_state_rather_than_a_guess(self):
        """An unrecognised verdict is admitted, not mapped to something plausible."""

        recovery = build_recovery_actions(_verdict("something_new"))
        self.assertEqual(recovery["state"], STATE_UNKNOWN)

    def test_a_completed_run_offers_no_recovery(self):
        recovery = build_recovery_actions(_verdict("completed"))

        self.assertEqual(recovery["state"], STATE_COMPLETED)
        self.assertEqual(recovery["actions"], [])


class AWaitingRunIsNotARecoverableOneTests(unittest.TestCase):
    """A run paused on one approval is answered, not recovered.

    Offering "continue from checkpoint" beside an approval prompt invites the
    user to route around the question they were asked.
    """

    def test_recovery_actions_are_disabled_while_waiting(self):
        recovery = build_recovery_actions(
            {
                "status": "needs_edit_approval",
                "completion_verdict": {"verdict": "blocked"},
            }
        )

        for action_id in (CONTINUE_FROM_CHECKPOINT, RETRY_OPERATION):
            with self.subTest(action=action_id):
                action = _actions(recovery)[action_id]
                self.assertFalse(action["available"])
                self.assertIn("waiting", action["disabled_reason"].lower())

    def test_the_stop_reason_points_at_the_prompt_rather_than_the_past(self):
        recovery = build_recovery_actions(
            {
                "status": "needs_edit_approval",
                "completion_verdict": {"verdict": "blocked"},
            }
        )

        self.assertIn("decline", _actions(recovery)[STOP]["disabled_reason"].lower())


class CostIsQualifiedNeverInventedTests(unittest.TestCase):
    """AC: "Incremental recovery cost is shown as actual/derived/estimated/unavailable"."""

    def test_an_actual_cost_is_reported_as_actual(self):
        recovery = build_recovery_actions(
            _verdict(
                "failed",
                receipt={"estimated_actual_usd": 0.42, "confidence": "actual"},
            )
        )

        self.assertEqual(recovery["cost"]["incurred"]["kind"], "actual")
        self.assertAlmostEqual(recovery["cost"]["incurred"]["value_usd"], 0.42)

    def test_an_unreconciled_cost_is_unavailable_rather_than_a_number(self):
        """#613's vocabulary: unreconciled means unknown, not zero."""

        recovery = build_recovery_actions(
            _verdict(
                "failed",
                receipt={"estimated_actual_usd": 0.42, "confidence": "unreconciled"},
            )
        )

        self.assertEqual(recovery["cost"]["incurred"], {"kind": "unavailable"})

    def test_a_missing_receipt_is_unavailable(self):
        recovery = build_recovery_actions(_verdict("failed"))

        self.assertEqual(recovery["cost"]["incurred"]["kind"], "unavailable")

    def test_a_non_numeric_cost_is_refused(self):
        """A string where a number belongs is absence, not a value."""

        recovery = build_recovery_actions(
            _verdict("failed", receipt={"estimated_actual_usd": "0.42"})
        )

        self.assertEqual(recovery["cost"]["incurred"]["kind"], "unavailable")


class TheProjectionIsDeterministicAndPureTests(unittest.TestCase):
    """AC: "Renderer reconnect reconstructs the same state"."""

    def test_rebuilding_from_the_same_payload_is_identical(self):
        payload = _verdict("partial", "no_progress_guard", checkpoint_id="ckpt-1")

        first = build_recovery_actions(payload, provider_count=2)
        second = build_recovery_actions(payload, provider_count=2)

        self.assertEqual(first, second)

    def test_building_does_not_mutate_the_payload(self):
        """The payload is the persisted record; a projection must not edit it."""

        payload = _verdict("partial", "no_progress_guard", checkpoint_id="ckpt-1")
        before = copy.deepcopy(payload)

        build_recovery_actions(payload)

        self.assertEqual(payload, before)

    def test_the_schema_is_versioned(self):
        recovery = build_recovery_actions(_verdict("failed"))

        self.assertEqual(recovery["schema_version"], RECOVERY_SCHEMA_VERSION)


class NoRunContentReachesTheCardTests(unittest.TestCase):
    """AC: "No raw secrets, private paths outside policy or chain of thought".

    The projection carries enums, static copy and numbers. Nothing is copied
    out of the run, so there is no path by which a prompt, a provider message
    or a private path could arrive here.
    """

    def test_run_prose_is_not_copied_into_the_projection(self):
        import json

        needle = "sk-ant-api03-CANARYFAKE1234567890"  # pragma: allowlist secret
        payload = _verdict(
            "failed",
            "unknown",
            answer=f"my key is {needle}",
            stopped_reason=f"crashed with {needle}",
            error={"message": needle},
            objective=f"fix {needle}",
        )

        rendered = json.dumps(build_recovery_actions(payload))

        self.assertNotIn(needle, rendered)

    def test_the_reason_code_is_an_enum_not_a_sentence(self):
        """A reason code is dispatched on; prose is not."""

        recovery = build_recovery_actions(
            _verdict("failed", "the provider exploded while reading /home/me/.ssh")
        )

        import json

        self.assertNotIn("/home/me/.ssh", json.dumps(recovery))


class ProviderChoiceIsGatedOnRealAlternativesTests(unittest.TestCase):
    """Offering "try another provider" with only one configured is a dead end."""

    def test_a_single_provider_disables_the_provider_action(self):
        recovery = build_recovery_actions(_verdict("failed"), provider_count=1)
        action = _actions(recovery)[TRY_PROVIDER]

        self.assertFalse(action["available"])
        self.assertTrue(action["disabled_reason"])

    def test_more_than_one_provider_enables_it(self):
        recovery = build_recovery_actions(_verdict("failed"), provider_count=2)

        self.assertTrue(_actions(recovery)[TRY_PROVIDER]["available"])


class TheCliRendersTheSameProjectionTests(unittest.TestCase):
    """AC: "Actions from GUI and CLI control the same run/recovery attempt".

    Both surfaces consume this one dict. The CLI renderer is tested against the
    same structure the GUI receives, so a control cannot exist on one surface
    and not the other.
    """

    def test_every_action_appears_in_the_cli_text(self):
        recovery = build_recovery_actions(
            _verdict("partial", "no_progress_guard", checkpoint_id="c"),
            provider_count=2,
        )

        text = "\n".join(render_recovery_text(recovery))

        for action in recovery["actions"]:
            with self.subTest(action=action["id"]):
                self.assertIn(action["label"], text)

    def test_an_unavailable_action_shows_its_reason_in_the_cli(self):
        recovery = build_recovery_actions(_verdict("failed"))
        text = "\n".join(render_recovery_text(recovery))

        self.assertIn("unavailable", text.lower())

    def test_a_completed_run_renders_no_recovery_block(self):
        recovery = build_recovery_actions(_verdict("completed"))

        self.assertEqual(render_recovery_text(recovery), [])

    def test_rendering_survives_a_missing_projection(self):
        """An older persisted payload has no recovery block; the CLI still runs."""

        self.assertEqual(render_recovery_text({}), [])
        self.assertEqual(render_recovery_text(None), [])


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
