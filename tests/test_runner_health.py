from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "check_runner_health.py"
    spec = importlib.util.spec_from_file_location("vesta_runner_health", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load runner health check")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _runner(
    *,
    status: str = "online",
    busy: bool = False,
    labels: tuple[str, ...] = ("self-hosted", "Windows", "X64"),
) -> dict:
    return {
        "id": 7,
        "name": "vesta-desktop-runner",
        "status": status,
        "busy": busy,
        "labels": [{"name": label} for label in labels],
    }


class RunnerHealthTests(unittest.TestCase):
    def test_exact_idle_online_runner_qualifies(self) -> None:
        module = _load_module()
        evidence = module.evaluate(
            {"runners": [_runner()]}, {"self-hosted", "Windows", "X64"}, "a" * 40
        )
        self.assertEqual(evidence["verdict"], "qualified")
        self.assertEqual(evidence["candidate_sha"], "a" * 40)

    def test_offline_busy_or_wrong_label_is_runner_unavailable(self) -> None:
        module = _load_module()
        for runner in (
            _runner(status="offline"),
            _runner(busy=True),
            _runner(labels=("self-hosted", "Windows")),
        ):
            with self.subTest(runner=runner):
                evidence = module.evaluate(
                    {"runners": [runner]}, {"self-hosted", "Windows", "X64"}, "b" * 40
                )
                self.assertEqual(evidence["verdict"], "runner_unavailable")

    def test_api_error_is_infrastructure_blocked(self) -> None:
        module = _load_module()
        evidence = module.evaluate(
            None, {"self-hosted"}, "c" * 40, api_error="HTTP 403"
        )
        self.assertEqual(evidence["verdict"], "infrastructure_blocked")
        self.assertNotIn("token", str(evidence).lower())


if __name__ == "__main__":
    unittest.main()
