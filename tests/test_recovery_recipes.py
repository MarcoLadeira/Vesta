"""Deterministic recovery recipes (#569).

The rules that make recovery safe rather than just automatic are pinned here:
never repeat the failing action, never mutate, never retry a recipe that
already failed for the same signature, and return None honestly when nothing
applies.
"""

from __future__ import annotations

import unittest

from vestahub.failure_envelope import FailureCategory, FailureEnvelope
from vestahub.recovery_recipes import (
    READ_ONLY_ACTIONS,
    RECIPES,
    RecipeError,
    RecoveryAction,
    RecoveryLedger,
    RecoveryRecipe,
    select_recipe,
)


def envelope(text: str, tool: str = "", category: FailureCategory | None = None):
    return FailureEnvelope.diagnose(error_text=text, tool=tool, category=category)


class SelectionTests(unittest.TestCase):
    def test_the_reports_worked_example_routes_to_the_rest_api(self) -> None:
        # gh 2.80 lost the Projects field -> use REST, deterministically.
        recipe = select_recipe(
            envelope(
                "Error: Unknown field 'projects' projects_classic_removed", "github-cli"
            )
        )
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.recipe_id, "recover.gh.issue_read.deprecation.v1")
        self.assertEqual(recipe.action.action, "github_rest_api")

    def test_a_tool_specific_recipe_beats_the_generic_one(self) -> None:
        specific = select_recipe(envelope("unknown field", "github-cli"))
        generic = select_recipe(envelope("deprecated api", "some-other-tool"))
        self.assertEqual(specific.action.action, "github_rest_api")
        self.assertEqual(generic.action.action, "vesta_github_connector")

    def test_each_category_has_a_deterministic_action(self) -> None:
        cases = {
            "401 unauthorized": "provider_reauth_prompt",
            "429 rate limit exceeded": "switch_provider",
            "connection refused": "wait_and_retry",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(select_recipe(envelope(text)).action.action, expected)

    def test_stagnation_recovers_by_compacting_not_by_asking_the_model(self) -> None:
        recipe = select_recipe(envelope("", category=FailureCategory.NO_PROGRESS))
        self.assertEqual(recipe.action.action, "compact_context")

    def test_selection_is_deterministic(self) -> None:
        # The whole reason recipes exist instead of a provider round-trip.
        text = "429 rate limit exceeded"
        picks = {select_recipe(envelope(text)).recipe_id for _ in range(20)}
        self.assertEqual(len(picks), 1)

    def test_an_unknown_failure_has_no_recipe(self) -> None:
        # "No deterministic recovery applies" is an honest answer; it turns a
        # stall into a clear stop instead of another expensive guess.
        self.assertIsNone(select_recipe(envelope("something inexplicable")))

    def test_a_cancelled_run_is_never_recovered(self) -> None:
        self.assertIsNone(
            select_recipe(envelope("", category=FailureCategory.CANCELLED))
        )


class SafetyTests(unittest.TestCase):
    def test_a_recipe_never_repeats_the_action_that_just_failed(self) -> None:
        # If the REST API is what failed, the REST recipe must not be chosen.
        chosen = select_recipe(
            envelope("unknown field", "github-cli"), failing_action="github_rest_api"
        )
        self.assertNotEqual(
            getattr(chosen, "action", None) and chosen.action.action, "github_rest_api"
        )

    def test_a_mutating_action_cannot_be_registered_as_a_recipe(self) -> None:
        # Recovery must not become a side-door around the approval path.
        for unsafe in ("apply_patch", "git_commit", "open_pr", "write_file"):
            with self.subTest(action=unsafe):
                with self.assertRaises(RecipeError):
                    RecoveryRecipe(
                        recipe_id="unsafe.v1",
                        category=FailureCategory.NETWORK,
                        action=RecoveryAction(action=unsafe, rationale="no"),
                    )

    def test_every_shipped_recipe_is_read_only(self) -> None:
        for recipe in RECIPES:
            with self.subTest(recipe=recipe.recipe_id):
                self.assertIn(recipe.action.action, READ_ONLY_ACTIONS)

    def test_every_shipped_recipe_explains_itself(self) -> None:
        # The rationale is shown to the user; a recipe with no reason is a
        # black box, which is what this whole epic exists to remove.
        for recipe in RECIPES:
            with self.subTest(recipe=recipe.recipe_id):
                self.assertTrue(recipe.action.rationale.strip())
                self.assertGreater(len(recipe.action.rationale), 20)

    def test_recipe_ids_are_unique_and_versioned(self) -> None:
        ids = [recipe.recipe_id for recipe in RECIPES]
        self.assertEqual(len(ids), len(set(ids)))
        for recipe_id in ids:
            self.assertRegex(recipe_id, r"\.v\d+$")


class LedgerTests(unittest.TestCase):
    def test_a_recipe_is_not_retried_for_the_same_signature(self) -> None:
        # Otherwise recovery becomes the loop it exists to break.
        ledger = RecoveryLedger()
        first = select_recipe(envelope("429 rate limit"), ledger=ledger)
        ledger.mark("429 rate limit", first.recipe_id)
        again = select_recipe(envelope("429 rate limit"), ledger=ledger)
        self.assertIsNone(again)

    def test_the_same_recipe_may_apply_to_a_different_signature(self) -> None:
        ledger = RecoveryLedger()
        first = select_recipe(envelope("429 rate limit"), ledger=ledger)
        ledger.mark(
            FailureEnvelope.diagnose(error_text="429 rate limit").error_signature,
            first.recipe_id,
        )
        other = select_recipe(envelope("quota exceeded on another host"), ledger=ledger)
        self.assertIsNotNone(other)

    def test_history_is_stable_and_json_safe(self) -> None:
        import json

        ledger = RecoveryLedger()
        ledger.mark("sig-a", "recover.network.retry.v1")
        ledger.mark("sig-a", "recover.quota.switch_provider.v1")
        json.dumps(ledger.history())
        self.assertEqual(ledger.history(), sorted(ledger.history()))


if __name__ == "__main__":
    unittest.main()
