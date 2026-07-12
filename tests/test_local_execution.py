import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from opaihub import result_cache
from opaihub.ask import run_ask
from opaihub.ledger import EVENT_CACHE, EVENT_MODEL_CALL, read_events, summarize_ledger
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


class _WorkspaceChangingRunner(_FakeRunner):
    """Test double that changes tracked content while an answer is in flight."""

    def __init__(self, workspace_file: Path, answer="stale answer"):
        super().__init__(answer=answer)
        self._workspace_file = workspace_file

    def complete(self, prompt, *, system=None, timeout=60.0):
        answer = super().complete(prompt, system=system, timeout=timeout)
        self._workspace_file.write_text("value = 2\n", encoding="utf-8")
        return answer


def _git_repo(root: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=cache-tests@example.invalid",
            "-c",
            "user.name=OPai Cache Tests",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


class ResultCacheTests(unittest.TestCase):
    def test_near_duplicate_tasks_share_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(Path(tmp))
            self.assertEqual(
                result_cache.cache_key(root, "Summarize  the  DIFF!", "m"),
                result_cache.cache_key(root, "summarize the diff", "m"),
            )

    def test_different_task_is_a_different_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(Path(tmp))
            self.assertNotEqual(
                result_cache.cache_key(root, "fix bug", "m"),
                result_cache.cache_key(root, "add feature", "m"),
            )

    def test_store_and_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(Path(tmp))
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
            root = _git_repo(Path(tmp))
            runner = _FakeRunner()
            run_ask(root, "summarize the diff", runner=runner)
            second = run_ask(root, "Summarize  the  Diff!", runner=runner)
        self.assertEqual(second["status"], "cache_hit")
        self.assertEqual(second["source"], "cache")
        self.assertEqual(runner.calls, 1)  # model only ran once

    def test_workspace_change_during_answer_is_not_saved_as_the_new_cache_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(Path(tmp))
            changing_runner = _WorkspaceChangingRunner(root / "app.py")
            stable_runner = _FakeRunner(answer="fresh answer")
            with mock.patch("opaihub.ask._build_prompt", return_value="prompt"):
                first = run_ask(
                    root,
                    "summarize the diff",
                    runner=changing_runner,
                    selected_model_id="local",
                )
                second = run_ask(
                    root,
                    "summarize the diff",
                    runner=stable_runner,
                    selected_model_id="local",
                )

        self.assertEqual(first["status"], "answered_locally")
        self.assertEqual(second["status"], "answered_locally")
        self.assertEqual(second["answer"], "fresh answer")
        self.assertEqual(changing_runner.calls, 1)
        self.assertEqual(stable_runner.calls, 1)

    def test_result_cache_records_miss_and_hit_evidence_without_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(Path(tmp))
            runner = _FakeRunner()
            run_ask(root, "summarize the diff", runner=runner)
            second = run_ask(root, "summarize the diff", runner=runner)
            events = read_events(root)
            summary = summarize_ledger(root)

        cache_events = [
            event for event in events if event.get("event_type") == EVENT_CACHE
        ]
        self.assertEqual(second["status"], "cache_hit")
        self.assertEqual([event["outcome"] for event in cache_events], ["miss", "hit"])
        self.assertFalse(cache_events[0]["avoided_model_call"])
        self.assertTrue(cache_events[1]["avoided_model_call"])
        self.assertGreaterEqual(cache_events[1]["age_seconds"], 0)
        self.assertEqual(
            [event for event in events if event.get("event_type") == EVENT_MODEL_CALL],
            [],
        )
        self.assertEqual(summary["estimated_actual_spend_usd"], 0.0)

    def test_result_cache_bypass_records_reason_without_creating_an_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(Path(tmp))
            (root / "payload.bin").write_bytes(b"\x00binary")
            runner = _FakeRunner()
            with mock.patch("opaihub.ask._build_prompt", return_value="prompt"):
                result = run_ask(
                    root,
                    "summarize the diff",
                    runner=runner,
                    selected_model_id="local",
                )
            events = read_events(root)
            answers = root / ".opaihub" / "answers"

        cache_event = next(
            event for event in events if event.get("event_type") == EVENT_CACHE
        )
        self.assertEqual(result["status"], "answered_locally")
        self.assertEqual(runner.calls, 1)
        self.assertEqual(cache_event["outcome"], "bypass")
        self.assertEqual(cache_event["reason"], "binary_file")
        self.assertFalse(cache_event["avoided_model_call"])
        self.assertFalse(answers.exists())

    def test_expired_result_cache_records_expiry_before_running_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(Path(tmp))
            result_cache.store(
                root,
                "summarize the diff",
                "local",
                "expired answer",
                now=datetime.now(timezone.utc) - timedelta(hours=2),
                ttl_seconds=1,
            )
            runner = _FakeRunner()
            with mock.patch("opaihub.ask._build_prompt", return_value="prompt"):
                result = run_ask(
                    root,
                    "summarize the diff",
                    runner=runner,
                    selected_model_id="local",
                )
            events = read_events(root)

        cache_event = next(
            event for event in events if event.get("event_type") == EVENT_CACHE
        )
        self.assertEqual(result["status"], "answered_locally")
        self.assertEqual(runner.calls, 1)
        self.assertEqual(cache_event["outcome"], "expired")
        self.assertFalse(cache_event["avoided_model_call"])

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
