import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import result_cache
from opaihub.ask import run_ask
from opaihub.ledger import summarize_ledger
from opaihub.local_runner import (
    LocalRunner,
    OpenAICompatibleRunner,
    detect_local_runner,
)


class _FakeRunner(LocalRunner):
    name = "fake"
    model = "fake-7b"

    def __init__(self, available=True, answer="local answer"):
        self._available = available
        self._answer = answer
        self.calls = 0

    def available(self):
        return self._available

    def complete(self, prompt, *, system=None, timeout=60.0):
        self.calls += 1
        return self._answer


class ResultCacheTests(unittest.TestCase):
    def test_near_duplicate_tasks_share_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                result_cache.cache_key(root, "Summarize  the  DIFF!", "m"),
                result_cache.cache_key(root, "summarize the diff", "m"),
            )

    def test_different_task_is_a_different_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertNotEqual(
                result_cache.cache_key(root, "fix bug", "m"),
                result_cache.cache_key(root, "add feature", "m"),
            )

    def test_store_and_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_cache.store(root, "fix bug", "m", "the answer")
            hit = result_cache.lookup(root, "fix  BUG", "m")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["answer"], "the answer")


class AskExecutionTests(unittest.TestCase):
    def test_answers_locally_and_records_real_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _FakeRunner(answer="2 files changed; tests pass.")
            result = run_ask(root, "summarize the diff", runner=runner)
            ledger = summarize_ledger(root)
        self.assertEqual(result["status"], "answered_locally")
        self.assertTrue(result["free"])
        self.assertEqual(result["answer"], "2 files changed; tests pass.")
        self.assertEqual(ledger["route_count"], 1)
        self.assertEqual(ledger["cloud_calls_avoided"], 1)

    def test_second_near_duplicate_is_a_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _FakeRunner()
            run_ask(root, "summarize the diff", runner=runner)
            second = run_ask(root, "Summarize  the  Diff!", runner=runner)
        self.assertEqual(second["status"], "cache_hit")
        self.assertEqual(second["source"], "cache")
        self.assertEqual(runner.calls, 1)  # model only ran once

    def test_no_local_model_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_ask(
                root, "summarize the diff", runner=_FakeRunner(available=False)
            )
        self.assertEqual(result["status"], "no_local_model")
        self.assertIn("hint", result)

    def test_cloud_tier_requires_confirmation_not_auto_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_ask(
                root,
                "design a complex distributed system architecture",
                runner=_FakeRunner(available=False),
                allow_cloud=False,
            )
        self.assertEqual(result["status"], "confirmation_required")

    def test_no_record_skips_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_ask(root, "summarize the diff", runner=_FakeRunner(), record=False)
            ledger = summarize_ledger(root)
        self.assertEqual(ledger["event_count"], 0)


class LocalRunnerSafetyTests(unittest.TestCase):
    def test_public_endpoint_is_refused_even_if_available(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(
                os.environ,
                {"LOCAL_MODEL_URL": "https://api.public-host.com/v1"},
                clear=False,
            ),
        ):
            # Pretend every endpoint is reachable; the loopback gate must still
            # refuse the public LOCAL_MODEL_URL.
            with mock.patch.object(
                OpenAICompatibleRunner, "available", return_value=True
            ):
                with mock.patch(
                    "opaihub.local_runner.OllamaRunner.available", return_value=False
                ):
                    runner = detect_local_runner(Path(tmp))
        self.assertIsNone(runner)


if __name__ == "__main__":
    unittest.main()
