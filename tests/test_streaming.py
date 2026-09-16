"""Streaming activity through the GUI pipeline (account + local), no real CLI."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from _helpers import FakeStreamingRunner, make_repo

from vesta.provider_contract import normalize_provider_error
from vestahub.gui_pipeline import handle_gui_message


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
        # #378: the evidence-backed completion verdict closes the stream.
        self.assertEqual(types[-1], "completion_verdict")
        self.assertEqual(events[-1]["status"], "success")
        self.assertEqual(
            events[-1]["title"],
            "Response received — content not independently verified",
        )
        self.assertEqual(result["status"], "answered")

        authenticated = next(
            event for event in events if event["type"] == "provider_authenticated"
        )
        self.assertIn("sign-in", authenticated["title"].lower())
        self.assertNotIn("connection verified", authenticated["title"].lower())

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

    def test_preamble_coalesces_into_one_phase_row(self):
        # #225: every pre-provider step updates ONE derived id in place, and
        # the row closes ("Request sent") when the provider takes over.
        result, events, texts = self._run(FakeStreamingRunner())
        preamble_types = {
            "request_prepare",
            "context_read",
            "model_selected",
            "provider_checking",
            "provider_authenticated",
            "request_sending",
        }
        preamble = [
            e
            for e in events
            if e["type"] in preamble_types and e.get("channel") != "status"
        ]
        self.assertGreaterEqual(len(preamble), 6)
        self.assertEqual(len({e["id"] for e in preamble}), 1)
        self.assertTrue(preamble[0]["id"].endswith(":phase"))
        self.assertEqual(preamble[-1]["status"], "success")
        self.assertEqual(preamble[-1]["title"], "Request sent")
        # Ambient facts are mirrored to the status channel for the strip.
        status_ids = [e["id"] for e in events if e.get("channel") == "status"]
        self.assertTrue(any(i.endswith(":model") for i in status_ids))
        self.assertTrue(any(i.endswith(":connect") for i in status_ids))

    def test_auth_failure_closes_the_phase_row_as_error(self):
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
        phased = [e for e in events if str(e["id"]).endswith(":phase")]
        # The stream call itself failed after send; the preamble row closed
        # honestly at handoff and the terminal failed event reports the rest.
        self.assertEqual(phased[-1]["status"], "success")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("completed", [e["type"] for e in events])

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
                "vestahub.ask.run_ask",
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
        # #378: the evidence-backed completion verdict closes the stream.
        self.assertEqual(types[-1], "completion_verdict")
        self.assertEqual(events[-1]["status"], "success")
        self.assertEqual(result["answer"], "local answer")
        self.assertIn("local answer", "".join(texts))
        # An answer-only turn closes green but must not claim independent
        # objective verification merely because non-empty prose arrived.
        phased = [e for e in events if str(e["id"]).endswith(":phase")]
        self.assertEqual(phased[-1]["status"], "success")
        self.assertEqual(
            phased[-1]["title"],
            "Response received — content not independently verified",
        )
        self.assertEqual(
            result["completion_verdict"]["reason_code"], "answer_delivered"
        )


if __name__ == "__main__":
    unittest.main()
