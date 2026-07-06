"""Guided local-model onboarding readiness (#3): state machine + smoke test."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opaihub.local_onboarding import (
    STATES,
    ProbeResult,
    local_onboarding_status,
    local_route_smoke_test,
    local_runtime_readiness,
)

from tests._helpers import FakeLocalRunner, make_repo


def _which(present):
    return lambda binary: f"/usr/bin/{binary}" if binary in present else None


def _probe(mapping):
    """mapping: runtime models_path -> ProbeResult."""

    def probe(url, models_path):
        return mapping.get(models_path, ProbeResult(reachable=False, valid=False))

    return probe


class RuntimeStateTests(unittest.TestCase):
    def test_installed_and_running_with_a_model_is_ready(self):
        runtimes = local_runtime_readiness(
            which=_which({"ollama"}),
            probe=_probe({"/api/tags": ProbeResult(True, True, ("qwen2.5-coder:7b",))}),
            env={},
        )
        ollama = next(r for r in runtimes if r["id"] == "ollama")
        self.assertEqual(ollama["state"], "ready")
        self.assertTrue(ollama["ready"])
        self.assertEqual(ollama["models"], ["qwen2.5-coder:7b"])
        self.assertEqual(ollama["next_step"]["command"], "")

    def test_installed_but_stopped_says_start_it(self):
        runtimes = local_runtime_readiness(
            which=_which({"ollama"}),
            probe=_probe({"/api/tags": ProbeResult(False, False)}),
            env={},
        )
        ollama = next(r for r in runtimes if r["id"] == "ollama")
        self.assertEqual(ollama["state"], "stopped")
        self.assertIn("ollama serve", ollama["next_step"]["command"])
        self.assertTrue(ollama["next_step"]["requires_consent"])

    def test_running_without_a_model_says_pull_one(self):
        runtimes = local_runtime_readiness(
            which=_which({"ollama"}),
            probe=_probe({"/api/tags": ProbeResult(True, True, ())}),
            env={},
        )
        ollama = next(r for r in runtimes if r["id"] == "ollama")
        self.assertEqual(ollama["state"], "no_model")
        self.assertIn("ollama pull", ollama["next_step"]["command"])
        self.assertTrue(ollama["next_step"]["requires_consent"])

    def test_not_installed_says_install_with_consent(self):
        runtimes = local_runtime_readiness(
            which=_which(set()),
            probe=_probe({"/api/tags": ProbeResult(False, False)}),
            env={},
        )
        ollama = next(r for r in runtimes if r["id"] == "ollama")
        self.assertEqual(ollama["state"], "not_installed")
        self.assertTrue(ollama["next_step"]["requires_consent"])
        self.assertTrue(ollama["next_step"]["command"])

    def test_configured_endpoint_that_is_down_is_unreachable(self):
        runtimes = local_runtime_readiness(
            which=_which(set()),
            probe=_probe({"/models": ProbeResult(False, False)}),
            env={"LOCAL_MODEL_URL": "http://127.0.0.1:1234"},
        )
        oai = next(r for r in runtimes if r["id"] == "openai-compatible")
        self.assertEqual(oai["state"], "unreachable")

    def test_reachable_but_malformed_response_is_incompatible(self):
        runtimes = local_runtime_readiness(
            which=_which({"lmstudio"}),
            probe=_probe({"/models": ProbeResult(True, False)}),
            env={},
        )
        oai = next(r for r in runtimes if r["id"] == "openai-compatible")
        self.assertEqual(oai["state"], "incompatible")
        self.assertFalse(oai["next_step"]["requires_consent"])

    def test_openai_endpoint_is_normalized_to_v1(self):
        runtimes = local_runtime_readiness(
            which=_which(set()),
            probe=_probe({"/models": ProbeResult(True, True, ("m",))}),
            env={"LOCAL_MODEL_URL": "http://127.0.0.1:1234"},
        )
        oai = next(r for r in runtimes if r["id"] == "openai-compatible")
        self.assertTrue(oai["endpoint"].endswith("/v1"))

    def test_states_tuple_is_ordered_worst_to_best(self):
        self.assertEqual(STATES[0], "not_installed")
        self.assertEqual(STATES[-1], "ready")


class OnboardingStatusTests(unittest.TestCase):
    def test_ready_when_any_runtime_is_ready(self):
        status = local_onboarding_status(
            which=_which({"ollama"}),
            probe=_probe({"/api/tags": ProbeResult(True, True, ("llama3.2",))}),
            env={},
        )
        self.assertTrue(status["ready"])
        self.assertIn("ollama:llama3.2", status["ready_models"])
        self.assertEqual(status["next_action"], "")

    def test_not_ready_guides_to_the_closest_runtime(self):
        status = local_onboarding_status(
            which=_which({"ollama"}),
            probe=_probe({"/api/tags": ProbeResult(True, True, ())}),  # no_model
            env={},
        )
        self.assertFalse(status["ready"])
        # no_model is closer to ready than the openai default (not_installed),
        # so it drives the next action.
        self.assertIn("ollama pull", status["next_action"])

    def test_status_states_that_nothing_runs_automatically(self):
        status = local_onboarding_status(
            which=_which(set()),
            probe=_probe({}),
            env={},
        )
        self.assertIn("never downloads", status["privacy"])


class SmokeTestTests(unittest.TestCase):
    def test_smoke_test_passes_with_an_injected_local_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = local_route_smoke_test(root, runner=FakeLocalRunner(answer="ok"))
        self.assertTrue(result["ran"])
        self.assertTrue(result["answered"])
        self.assertEqual(result["status"], "answered_locally")
        self.assertIn("not recorded", result["privacy"])

    def test_smoke_test_reports_failure_without_a_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            # No runner and no local model -> run_ask reports it, not a crash.
            result = local_route_smoke_test(root, runner=None)
        self.assertTrue(result["ran"])
        self.assertFalse(result["answered"])


class CliTests(unittest.TestCase):
    def test_cli_onboard_reports_readiness(self):
        import contextlib
        import io
        import json
        from unittest import mock

        from opai.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            # No local runtime installed or reachable -> exit 1, honest guidance.
            with mock.patch(
                "opaihub.local_onboarding._default_probe",
                return_value=ProbeResult(False, False),
            ):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(["models", "onboard", "--project", tmp])
        payload = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["ready"])
        self.assertIn("runtimes", payload)


if __name__ == "__main__":
    unittest.main()
