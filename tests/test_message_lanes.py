"""Message laneing: one routing decision, made before any model runs.

Two things are being proven here.

**Laneing** — that a message is assigned a lane, and the lane fixes the runtime
policy, so behaviour follows from what was asked rather than from which provider
happened to pick the request up.

**Flapping** — replay identical and near-identical cases and flag anything whose
outcome oscillates. Message-shape variance is the defect being fixed
("semantically similar user messages succeed or fail depending on wording,
length, or the model selected"), so a paraphrase that changes the lane is itself
the bug, and these tests are how it gets caught.

(Not to be confused with ``test_message_contract.py``, which covers the
user-facing *status* contract of a finished message.)
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opaihub.agent_policy import resolve_agent_policy
from opaihub.message_contract import (
    EXPLORE,
    GOVERNED,
    LONG_HORIZON,
    STABLE,
    lane_description,
    lane_label,
    resolve_message_contract,
)


def _contract(root: Path, message: str):
    policy = resolve_agent_policy(message)
    return resolve_message_contract(root, message, agent_mode=policy.mode)


class LaneAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_routine_work_takes_the_predictable_lane(self) -> None:
        for message in (
            "explain this repo",
            "fix the failing test in app.py",
            "what does this function do?",
        ):
            with self.subTest(message=message):
                self.assertEqual(_contract(self.root, message).lane, STABLE)

    def test_discovery_takes_the_explore_lane_and_is_isolated(self) -> None:
        contract = _contract(self.root, "find me an issue to solve")
        self.assertEqual(contract.lane, EXPLORE)
        # A long search must not become the next request's baggage.
        self.assertTrue(contract.isolate_context)

    def test_multi_file_work_gets_a_longer_budget(self) -> None:
        contract = _contract(
            self.root, "implement a new billing feature across the api and the ui"
        )
        self.assertEqual(contract.lane, LONG_HORIZON)
        stable = _contract(self.root, "fix the failing test in app.py")
        self.assertGreater(contract.max_tool_calls, stable.max_tool_calls)
        self.assertGreater(contract.max_active_seconds, stable.max_active_seconds)

    def test_irreversible_work_takes_the_governed_lane(self) -> None:
        for message in (
            "publish the release to production",
            "delete the repository with rm -rf",
        ):
            with self.subTest(message=message):
                self.assertEqual(_contract(self.root, message).lane, GOVERNED)

    def test_the_governed_lane_is_not_given_a_tighter_execution_budget(self) -> None:
        # Its safety comes from refusing fallback and requiring confirmation.
        # A smaller allowance would only strand a legitimate release half-done.
        governed = _contract(self.root, "publish the release to production")
        stable = _contract(self.root, "fix the failing test in app.py")
        self.assertEqual(governed.max_tool_calls, stable.max_tool_calls)
        self.assertEqual(governed.max_active_seconds, stable.max_active_seconds)

    def test_the_governed_lane_never_reroutes_or_silently_retries(self) -> None:
        contract = _contract(self.root, "publish the release to production")
        # Moving an irreversible action to a different provider after a failure
        # is a second attempt at something the user approved once, for one
        # route. It must ask instead.
        self.assertFalse(contract.allow_provider_fallback)
        self.assertEqual(contract.max_transient_retries, 0)
        self.assertTrue(contract.requires_confirmation)

    def test_every_other_lane_can_still_recover_automatically(self) -> None:
        for message in (
            "explain this repo",
            "find me an issue to solve",
            "implement a new billing feature across the api and the ui",
        ):
            with self.subTest(message=message):
                contract = _contract(self.root, message)
                self.assertTrue(contract.allow_provider_fallback)
                self.assertGreaterEqual(contract.max_transient_retries, 1)

    def test_the_most_constrained_qualifying_lane_wins(self) -> None:
        # Reads as multi-file feature work *and* as a release. Governed has to
        # win: the wider budget is worthless if it routes around the gate.
        contract = _contract(
            self.root, "implement the deploy feature and publish it to production"
        )
        self.assertEqual(contract.lane, GOVERNED)
        self.assertFalse(contract.allow_provider_fallback)


class TransparencyTests(unittest.TestCase):
    """A router the user cannot see is one they cannot learn."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_every_lane_has_a_label_and_a_plain_language_reason(self) -> None:
        for lane in (STABLE, EXPLORE, LONG_HORIZON, GOVERNED):
            with self.subTest(lane=lane):
                self.assertTrue(lane_label(lane))
                self.assertTrue(lane_description(lane))

    def test_the_contract_serializes_for_display(self) -> None:
        payload = _contract(self.root, "publish the release to production").to_dict()
        self.assertEqual(payload["lane"], GOVERNED)
        self.assertEqual(payload["laneLabel"], "Governed")
        self.assertFalse(payload["allowProviderFallback"])
        self.assertTrue(payload["reason"])
        # Display shape only — nothing here can carry a prompt or a secret.
        self.assertTrue(
            all(
                isinstance(value, (str, bool, int, float, list))
                for value in payload.values()
            )
        )


class FlappingTests(unittest.TestCase):
    """Identical and near-identical requests must not oscillate."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_same_message_always_produces_the_same_contract(self) -> None:
        message = "fix the failing test in app.py"
        contracts = {_contract(self.root, message) for _ in range(10)}
        self.assertEqual(
            len(contracts), 1, "routing must not depend on time, order, or chance"
        )

    def test_paraphrases_of_one_request_land_in_one_lane(self) -> None:
        # The exact defect being fixed: "semantically similar user messages
        # succeed or fail depending on wording".
        groups = {
            STABLE: (
                "fix the failing test",
                "Fix the failing test.",
                "could you fix the failing test please",
                "the test is failing, fix it",
            ),
            GOVERNED: (
                "publish the release to production",
                "Publish this release to production.",
                "please publish the release to production now",
            ),
            LONG_HORIZON: (
                "implement a new billing feature across the api and the ui",
                "build a new billing feature spanning the api and the ui",
            ),
        }
        for expected, phrasings in groups.items():
            lanes = {_contract(self.root, text).lane for text in phrasings}
            self.assertEqual(
                lanes,
                {expected},
                f"paraphrases split across lanes {sorted(lanes)}: {phrasings}",
            )

    def test_trailing_whitespace_and_case_do_not_change_the_lane(self) -> None:
        base = _contract(self.root, "publish the release to production")
        for variant in (
            "  publish the release to production  ",
            "PUBLISH THE RELEASE TO PRODUCTION",
            "publish the release to production\n",
        ):
            with self.subTest(variant=variant):
                self.assertEqual(_contract(self.root, variant).lane, base.lane)

    def test_a_missing_taxonomy_degrades_to_the_predictable_lane(self) -> None:
        # Configuration trouble must never become message-shape variance.
        contract = _contract(Path(self._tmp.name) / "does-not-exist", "do the thing")
        self.assertEqual(contract.lane, STABLE)
        self.assertTrue(contract.allow_provider_fallback)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
