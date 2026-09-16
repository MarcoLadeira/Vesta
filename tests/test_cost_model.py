"""A malformed local cost model degrades honestly, never silently (#471)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from vestahub.budget import budget_gate
from vestahub.cost_model import (
    DEFAULT_COST_MODEL,
    cost_model_status,
    is_degraded,
    load_cost_model,
    tier_cost,
)


class CostModelDegradationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        # The cost model lives in a global package hub; point it at an isolated
        # temp hub so a test never reads or writes the real shipped file.
        self._hub = Path(self._tmp.name) / "hub"
        self._patch = mock.patch("vestahub.cost_model.hub_root", return_value=self._hub)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def _write_model(self, text: str) -> None:
        path = self._hub / "model-intelligence" / "cost_model.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_absent_model_is_a_clean_default_not_degraded(self):
        model = load_cost_model(self.root)
        self.assertFalse(is_degraded(model))
        self.assertTrue(cost_model_status(self.root)["ok"])

    def test_malformed_model_degrades_with_a_typed_reason(self):
        self._write_model(": : not valid [")
        model = load_cost_model(self.root)
        self.assertTrue(is_degraded(model))
        status = cost_model_status(self.root)
        self.assertFalse(status["ok"])
        self.assertTrue(status["degraded"])
        self.assertTrue(status["reason"])  # a non-empty, typed reason

    def test_non_mapping_model_degrades(self):
        self._write_model("- just\n- a\n- list\n")
        self.assertTrue(is_degraded(load_cost_model(self.root)))

    def test_degraded_model_still_yields_usable_conservative_estimates(self):
        self._write_model("%%% broken")
        model = load_cost_model(self.root)
        # It falls back to the documented defaults so estimates never crash — the
        # honesty is in the flag, not a zeroed table.
        self.assertEqual(
            tier_cost("L3", 1000, model),
            tier_cost("L3", 1000, DEFAULT_COST_MODEL),
        )

    def test_user_file_cannot_smuggle_a_false_all_clear_flag(self):
        self._write_model("schema_version: 1\ndegraded: false\nbaseline_tier: L4\n")
        model = load_cost_model(self.root)
        self.assertFalse(is_degraded(model))  # a valid load is genuinely not degraded
        self.assertEqual(model["baseline_tier"], "L4")  # its real content still loads

    def test_paid_route_under_a_degraded_model_requires_confirmation(self):
        # #471: never silently allow a paid route on a possibly-understated
        # estimate — a degraded cost model forces explicit confirmation.
        self._write_model(": broken :")
        gate = budget_gate(self.root, next_cost_usd=0.02, tier="L3")
        self.assertTrue(gate["cost_model_degraded"])
        self.assertTrue(gate["requires_confirmation"])
        self.assertFalse(gate["allowed"])


if __name__ == "__main__":
    unittest.main()
