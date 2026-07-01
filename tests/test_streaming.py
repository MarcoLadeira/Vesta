"""Streaming activity through the GUI pipeline (account + local), no real CLI."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from _helpers import FakeStreamingRunner, make_repo

from opai.provider_contract import normalize_provider_error
from opaihub.gui_pipeline import handle_gui_message


class AccountStreamingTests(unittest.TestCase):
    def _run(self, runner, **kw):
        events: list[dict] = []
        texts: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = handle_gui_message(
                root,
                "summarize this",
                model_id="account:claude:opus",
                mode="ask",
                account_runner=runner,
                on_event=events.append,
                on_text=texts.append,
                cancel=threading.Event(),
                **kw,
            )
        return result, events, texts

    def test_events_flow_prepare_to_completion(self):
        result, events, texts = self._run(FakeStreamingRunner())
        types = [e["type"] for e in events]
        # Real pre-call stages then the runner's own events, then completion.
        self.assertEqual(types[0], "request_prepare")
        self.assertIn("context_read", types)
        self.assertIn("model_selected", types)
        self.assertIn("provider_checking", types)
        self.assertIn("provider_authenticated", types)
        self.assertIn("request_sending", types)
        self.assertIn("file_read", types)  # from the runner's scripted events
        self.assertEqual(types[-1], "completed")
        self.assertEqual(result["status"], "answered")

    def test_auth_failure_never_emits_completed(self):
        class AuthFailureRunner(FakeStreamingRunner):
            def stream(self, *args, **kwargs):
                return {
                    "text": "",
                    "cost": None,
                    "returncode": 1,
                    "error": normalize_provider_error(
                        "claude", "401 Invalid authentication credentials", returncode=1
                    ),
                }

        result, events, texts = self._run(AuthFailureRunner())
        types = [event["type"] for event in events]

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")
        self.assertIn("provider_auth_failed", types)
        self.assertNotIn("completed", types)
        self.assertEqual(texts, [])

    def test_text_is_streamed_in_order(self):
        result, events, texts = self._run(
            FakeStreamingRunner(chunks=["Hel", "lo ", "world"])
        )
        self.assertEqual("".join(texts), "Hello world")
        self.assertEqual(result["answer"], "Hello world")

    def test_no_callbacks_still_works_unchanged(self):
        # Backward-compat: without callbacks the pipeline uses the blocking path.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))

            class Blocking:
                model = "opus"
                account_id = "claude"

                def available(self):
                    return True

                def complete(self, *a, **k):
                    return {"text": "done", "cost": 0.0}

            result = handle_gui_message(
                root,
                "x",
                model_id="account:claude:opus",
                mode="ask",
                account_runner=Blocking(),
            )
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["answer"], "done")


class LocalStreamingTests(unittest.TestCase):
    def test_local_emits_stages_and_delivers_answer(self):
        events: list[dict] = []
        texts: list[str] = []
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch(
                "opaihub.ask.run_ask",
                return_value={"status": "answered_locally", "answer": "local answer"},
            ):
                result = handle_gui_message(
                    root,
                    "hi",
                    model_id="auto",
                    mode="ask",
                    on_event=events.append,
                    on_text=texts.append,
                    cancel=threading.Event(),
                )
        types = [e["type"] for e in events]
        self.assertIn("request_sending", types)
        self.assertEqual(types[-1], "completed")
        self.assertEqual(result["answer"], "local answer")
        self.assertIn("local answer", "".join(texts))


if __name__ == "__main__":
    unittest.main()
