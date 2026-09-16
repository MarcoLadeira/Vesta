from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "run_provider_canary.py"
    spec = importlib.util.spec_from_file_location("vesta_provider_canary", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load provider canary runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _environment() -> dict[str, str]:
    return {
        "VESTA_LIVE_PROVIDER_SMOKE": "1",
        "VESTA_CONFIRM_CLOUD_TESTS": "YES",
        "VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS": "groq",
        "VESTA_LIVE_MODELS": "free:groq:openai/gpt-oss-120b",
        "VESTA_PROVIDER_CANARY_MAX_USD": "0.25",
        "VESTA_PROVIDER_CANARY_NON_PRODUCTION": "YES",
    }


MODEL_ID = "free:groq:openai/gpt-oss-120b"


def _result(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "status": "answered_by_free_api",
        "answer": "OK",
        "source": "free_api",
        "model_id": MODEL_ID,
        "completion_state": "completed",
    }
    value.update(overrides)
    return value


def _observation(**overrides: object) -> dict[str, object]:
    from vestahub.ledger import EVENT_MODEL_CALL, task_fingerprint

    value: dict[str, object] = {
        "event_type": EVENT_MODEL_CALL,
        "task_hash": task_fingerprint("Reply with exactly: OK"),
        "provider_id": "groq",
        "model_id": MODEL_ID,
        "canonical_model_id": MODEL_ID,
        "provider_type": "free_api",
        "confirmed": True,
        "is_local_route": False,
        "model_calls": 1,
        "tokens": 12,
        "input_tokens": 8,
        "output_tokens": 4,
        "measurement": "provider",
        "estimated_actual_usd": 0.02,
        "cost_price_known": True,
    }
    value.update(overrides)
    return value


def _payload(
    *,
    result: dict[str, object] | None = None,
    observation: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        **(result or _result()),
        "canary_observation": observation or _observation(),
    }


class StrictProviderCanaryRunnerTests(unittest.TestCase):
    def test_runner_uses_production_owned_canary_contract(self) -> None:
        module = _load_module()
        production_module = types.ModuleType("vestahub.provider_canary")
        run = mock.Mock(return_value=(None, _payload()))
        production_module.run_selected_provider_canary = run

        with mock.patch.dict(
            sys.modules,
            {
                "vestahub.provider_canary": production_module,
                "tests.test_live_provider_smoke": None,
            },
        ):
            try:
                unavailable, result = module._run_provider("groq", MODEL_ID)
            except ModuleNotFoundError:
                self.fail("provider canary runtime imported test-only code")

        self.assertIsNone(unavailable)
        self.assertEqual(result, _payload())
        run.assert_called_once_with("groq", MODEL_ID)

    def test_real_runner_binds_one_fixed_remote_call_to_ledger_observation(
        self,
    ) -> None:
        module = _load_module()
        calls: list[tuple[Path, str, dict[str, object]]] = []

        def fake_ask(root: Path, task: str, **kwargs: object) -> dict[str, object]:
            from vestahub.ledger import record_model_call

            calls.append((root, task, kwargs))
            record_model_call(
                root,
                task,
                model_tier="L2",
                provider_type="free_api",
                tokens=12,
                confirmed=True,
                real_cost_usd=0.02,
                model_id=MODEL_ID,
                provider_id="groq",
                input_tokens=8,
                output_tokens=4,
                measurement="provider",
            )
            return _result()

        with (
            mock.patch.dict(os.environ, _environment(), clear=True),
            mock.patch(
                "vestahub.provider_canary.live_provider_prerequisite",
                return_value=None,
            ),
            mock.patch(
                "vestahub.provider_canary.adapter_prerequisite",
                return_value=None,
            ),
            mock.patch("vesta.app_state.ask", side_effect=fake_ask),
        ):
            unavailable, result = module._run_provider("groq", MODEL_ID)

        self.assertIsNone(unavailable)
        self.assertEqual(len(calls), 1)
        _root, prompt, kwargs = calls[0]
        self.assertEqual(prompt, "Reply with exactly: OK")
        self.assertEqual(
            kwargs,
            {
                "model_choice": MODEL_ID,
                "allow_cloud": True,
                "allow_edits": False,
                "tool_calling_enabled": False,
                "mode": "ask",
            },
        )
        self.assertIn("canary_observation", result)
        self.assertEqual(result["canary_observation"]["provider_id"], "groq")
        self.assertEqual(result["canary_observation"]["model_id"], MODEL_ID)
        self.assertEqual(result["canary_observation"]["tokens"], 12)
        self.assertEqual(result["canary_observation"]["estimated_actual_usd"], 0.02)

    def test_missing_or_excessive_budget_fails_before_provider_call(self) -> None:
        module = _load_module()
        for value in (None, "invalid", "2.00"):
            environment = _environment()
            if value is None:
                environment.pop("VESTA_PROVIDER_CANARY_MAX_USD")
            else:
                environment["VESTA_PROVIDER_CANARY_MAX_USD"] = value
            with (
                self.subTest(value=value),
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(
                    module, "_run_provider", return_value=(None, {})
                ) as run,
            ):
                self.assertEqual(module.main(), module.CREDENTIAL_EXIT)
                run.assert_not_called()

    def test_unready_selected_provider_is_not_a_green_skip(self) -> None:
        module = _load_module()
        with (
            mock.patch.dict(os.environ, _environment(), clear=True),
            mock.patch.object(
                module,
                "_run_provider",
                return_value=("requires safe provider diagnostic", None),
            ),
        ):
            self.assertEqual(module.main(), module.CREDENTIAL_EXIT)

    def test_selected_provider_must_return_exact_fixed_answer(self) -> None:
        module = _load_module()
        for answer in ("not ok", " OK ", "OK\n"):
            with (
                self.subTest(answer=answer),
                mock.patch.dict(os.environ, _environment(), clear=True),
                mock.patch.object(
                    module,
                    "_run_provider",
                    return_value=(
                        None,
                        _payload(result=_result(answer=answer)),
                    ),
                ),
            ):
                self.assertEqual(module.main(), module.PRODUCT_EXIT)

    def test_local_or_fallback_answer_cannot_qualify_a_provider(self) -> None:
        module = _load_module()
        cases = (
            _result(status="answered_locally", source="local_model"),
            _result(status="cache_hit", source="cache"),
            _result(source="local_model"),
            _result(model_id=None),
            _result(completion_state="failed"),
        )
        for result in cases:
            with (
                self.subTest(result=result),
                mock.patch.dict(os.environ, _environment(), clear=True),
                mock.patch.object(
                    module,
                    "_run_provider",
                    return_value=(None, _payload(result=result)),
                ),
            ):
                self.assertEqual(module.main(), module.PRODUCT_EXIT)

    def test_provider_and_model_identity_must_match_observed_call(self) -> None:
        module = _load_module()
        cases = (
            _observation(provider_id="mistral"),
            _observation(model_id="free:groq:different"),
            _observation(canonical_model_id="free:groq:different"),
            _observation(event_type="cache_hit"),
            _observation(task_hash="wrong-task"),
            _observation(provider_type="local_model"),
            _observation(confirmed=False),
            _observation(is_local_route=True),
            _observation(model_calls=2),
        )
        for observation in cases:
            with (
                self.subTest(observation=observation),
                mock.patch.dict(os.environ, _environment(), clear=True),
                mock.patch.object(
                    module,
                    "_run_provider",
                    return_value=(None, _payload(observation=observation)),
                ),
            ):
                self.assertEqual(module.main(), module.PRODUCT_EXIT)

    def test_provider_usage_and_cost_must_be_observed_and_priceable(self) -> None:
        module = _load_module()
        cases = (
            _observation(tokens=0),
            _observation(tokens=13),
            _observation(measurement="estimated"),
            _observation(estimated_actual_usd=None),
            _observation(estimated_actual_usd="0.02"),
            _observation(estimated_actual_usd=-0.01),
            _observation(cost_price_known=False),
        )
        for observation in cases:
            with (
                self.subTest(observation=observation),
                mock.patch.dict(os.environ, _environment(), clear=True),
                mock.patch.object(
                    module,
                    "_run_provider",
                    return_value=(None, _payload(observation=observation)),
                ),
            ):
                self.assertEqual(module.main(), module.PRODUCT_EXIT)

    def test_observed_cost_above_declared_run_budget_fails_closed(self) -> None:
        module = _load_module()
        with (
            mock.patch.dict(os.environ, _environment(), clear=True),
            mock.patch.object(
                module,
                "_run_provider",
                return_value=(
                    None,
                    _payload(observation=_observation(estimated_actual_usd=0.26)),
                ),
            ),
        ):
            self.assertEqual(module.main(), module.PRODUCT_EXIT)

    def test_qualified_canary_runs_only_explicit_provider_and_model(self) -> None:
        module = _load_module()
        with (
            mock.patch.dict(os.environ, _environment(), clear=True),
            mock.patch.object(
                module,
                "_run_provider",
                return_value=(None, _payload()),
            ) as run,
        ):
            self.assertEqual(module.main(), 0)
        run.assert_called_once_with("groq", MODEL_ID)


if __name__ == "__main__":
    unittest.main()
