"""Contract tests for generated lifecycle projections (#612, #618)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_lifecycle.py"
GENERATED_PYTHON = ROOT / "opaihub" / "generated_lifecycle.py"
FIXTURE = ROOT / "opaihub" / "data" / "lifecycle-fixtures.json"


def run_generator(*args: str) -> int:
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    ).returncode


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_generated():
    spec = importlib.util.spec_from_file_location(
        "task_1_generated_lifecycle", GENERATED_PYTHON
    )
    if spec is None or spec.loader is None:
        raise AssertionError("generated Python lifecycle projection is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LifecycleGenerationTests(unittest.TestCase):
    def test_generator_write_repairs_projection_and_exits_zero(self):
        original = GENERATED_PYTHON.read_bytes()
        try:
            GENERATED_PYTHON.write_bytes(original + b"# drift\n")
            self.assertEqual(run_generator(), 0)
            self.assertEqual(GENERATED_PYTHON.read_bytes(), original)
        finally:
            GENERATED_PYTHON.write_bytes(original)

    def test_generator_check_rejects_changed_projection(self):
        original = GENERATED_PYTHON.read_bytes()
        try:
            GENERATED_PYTHON.write_bytes(original + b"# drift\n")
            self.assertEqual(run_generator("--check"), 1)
        finally:
            GENERATED_PYTHON.write_bytes(original)

    def test_fixture_contains_generated_repair_and_terminal_contract(self):
        fixture = load_fixture()
        generated = load_generated()
        self.assertIn(
            {
                "from": "verifying",
                "to": "running",
                "reason": "verification_repair",
            },
            fixture["vectors"],
        )
        self.assertEqual(
            fixture["terminal_states"], sorted(generated.TERMINAL_STATE_IDS)
        )

    def test_generated_contract_exposes_all_states_and_transition_metadata(self):
        generated = load_generated()
        self.assertEqual(generated.SCHEMA_VERSION, 1)
        self.assertEqual(
            generated.STATE_IDS,
            (
                "queued",
                "preparing",
                "running",
                "awaiting_input",
                "cancel_requested",
                "verifying",
                "completed",
                "partial",
                "blocked",
                "failed",
                "cancelled",
                "timeout",
                "needs_attention",
            ),
        )
        repair = generated.transition_spec("verifying", "running")
        self.assertEqual(repair["reason"], "verification_repair")
        self.assertFalse(repair["creates_new_attempt"])
        self.assertIn("verification", repair["guards"])
        self.assertIn("delivery", repair["guards"])
        self.assertIsNone(generated.transition_spec("completed", "running"))
        self.assertNotIn("cancel_requested", generated.TERMINAL_STATE_IDS)
        self.assertEqual(generated.EXIT_CODES["needs_attention"], 6)

    def test_unknown_inputs_have_typed_incompatible_degradation(self):
        expected = {
            "unknown_schema_version": {
                "state": "needs_attention",
                "compatibility": "incompatible",
                "automatic_retry": False,
            },
            "unknown_state": {
                "state": "needs_attention",
                "compatibility": "incompatible",
                "automatic_retry": False,
            },
            "unknown_status": {
                "state": "needs_attention",
                "compatibility": "incompatible",
                "automatic_retry": False,
            },
        }
        generated = load_generated()
        self.assertEqual(generated.DEGRADED_INPUTS, expected)
        self.assertEqual(load_fixture()["degraded_inputs"], expected)

    def test_browser_projection_executes_the_same_repair_and_terminal_contract(self):
        script = """
require('./opai/assets/web/generated-lifecycle.js');
const lifecycle = globalThis.OPaiLifecycle;
process.stdout.write(JSON.stringify({
  repair: lifecycle.transitionSpec('verifying', 'running'),
  terminal: lifecycle.isTerminal('cancelled'),
  cancellingTerminal: lifecycle.isTerminal('cancel_requested'),
  illegal: lifecycle.canTransition('completed', 'running')
}));
"""
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        contract = json.loads(result.stdout)
        self.assertEqual(contract["repair"]["reason"], "verification_repair")
        self.assertTrue(contract["terminal"])
        self.assertFalse(contract["cancellingTerminal"])
        self.assertFalse(contract["illegal"])


if __name__ == "__main__":
    unittest.main()
