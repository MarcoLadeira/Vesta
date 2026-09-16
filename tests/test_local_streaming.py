"""Local/free model streaming (#154).

Tokens reach the UI as they arrive instead of after the whole completion. All
hermetic: the SSE transport and the runner HTTP are faked; no network.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from vestahub import local_runner
from vestahub.local_runner import OpenAICompatibleRunner, _stream_chat


class _FakeResponse:
    def __init__(self, lines, status=200):
        self.status = status
        self._lines = iter(lines)

    def readline(self):
        return next(self._lines, b"")


class _FakeConn:
    def __init__(self, response):
        self._response = response
        self.sock = None

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        return self._response

    def close(self):
        pass


class SseParserTests(unittest.TestCase):
    def test_parses_deltas_and_usage(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
            b": keep-alive comment line\n",
            b'data: {"choices":[{"delta":{}}],"usage":{"total_tokens":5}}\n',
            b"data: [DONE]\n",
            b"",
        ]
        conn = _FakeConn(_FakeResponse(lines))
        seen: list[str] = []
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=conn
        ):
            text, usage = _stream_chat(
                "http://localhost:1234/v1/chat/completions",
                payload={"model": "m", "messages": []},
                timeout=5.0,
                cancel=None,
                extra_headers=None,
                on_delta=seen.append,
            )
        self.assertEqual(seen, ["Hel", "lo"])
        self.assertEqual(text, "Hello")
        self.assertEqual(usage, {"total_tokens": 5})

    def test_eof_before_terminal_marker_rejects_partial_text(self):
        """A dropped stream must never promote its last token to a full answer."""

        lines = [
            b'data: {"choices":[{"delta":{"content":"G"}}]}\n',
            b"",
        ]
        conn = _FakeConn(_FakeResponse(lines))
        seen: list[str] = []
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=conn
        ):
            with self.assertRaisesRegex(RuntimeError, "before a terminal marker"):
                _stream_chat(
                    "http://localhost:1234/v1/chat/completions",
                    payload={"model": "m", "messages": []},
                    timeout=5.0,
                    cancel=None,
                    extra_headers=None,
                    on_delta=seen.append,
                )
        self.assertEqual(seen, ["G"])

    def test_finish_reason_is_a_terminal_marker_when_done_is_omitted(self):
        """Compatible servers may finish with finish_reason before closing."""

        lines = [
            b'data: {"choices":[{"delta":{"content":"Four"}}]}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            b"",
        ]
        conn = _FakeConn(_FakeResponse(lines))
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=conn
        ):
            text, _usage = _stream_chat(
                "http://localhost:1234/v1/chat/completions",
                payload={"model": "m", "messages": []},
                timeout=5.0,
                cancel=None,
                extra_headers=None,
                on_delta=lambda _text: None,
            )
        self.assertEqual(text, "Four")

    def test_usage_arriving_after_finish_reason_is_still_captured(self):
        """OpenAI sends token counts in a chunk *after* finish_reason.

        Treating the first terminal marker as the end of the stream loses the
        usage on every run, and usage is where cost comes from — a silent
        under-report that walks straight past the budget guard.
        """
        lines = [
            b'data: {"choices":[{"delta":{"content":"Four"}}]}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            b'data: {"choices":[],"usage":{"total_tokens":11}}\n',
            b"data: [DONE]\n",
            b"",
        ]
        conn = _FakeConn(_FakeResponse(lines))
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=conn
        ):
            text, usage = _stream_chat(
                "http://localhost:1234/v1/chat/completions",
                payload={"model": "m", "messages": []},
                timeout=5.0,
                cancel=None,
                extra_headers=None,
                on_delta=lambda _text: None,
            )
        self.assertEqual(text, "Four")
        self.assertEqual(usage, {"total_tokens": 11})

    def test_http_error_raises_so_caller_can_fall_back(self):
        conn = _FakeConn(_FakeResponse([b""], status=500))
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=conn
        ):
            with self.assertRaises(RuntimeError):
                _stream_chat(
                    "http://localhost:1234/v1/chat/completions",
                    payload={"model": "m", "messages": []},
                    timeout=5.0,
                    cancel=None,
                    extra_headers=None,
                    on_delta=lambda _t: None,
                )


class RunnerStreamingTests(unittest.TestCase):
    def test_complete_streams_when_on_text_is_given(self):
        runner = OpenAICompatibleRunner("http://localhost:1234/v1", "m")
        seen: list[str] = []
        with mock.patch.object(
            local_runner,
            "_stream_chat",
            return_value=("Hello world", {"total_tokens": 3}),
        ) as streamer:
            # The stub returns text; emit deltas ourselves to prove the sink path.
            streamer.side_effect = lambda *a, on_delta, **k: (
                [on_delta("Hello "), on_delta("world")],
                ("Hello world", {"total_tokens": 3}),
            )[1]
            answer = runner.complete("hi", on_text=seen.append)
        self.assertEqual(answer, "Hello world")
        self.assertEqual(seen, ["Hello ", "world"])

    def test_falls_back_to_blocking_and_emits_once(self):
        runner = OpenAICompatibleRunner("http://localhost:1234/v1", "m")
        seen: list[str] = []
        with (
            mock.patch.object(
                local_runner, "_stream_chat", side_effect=RuntimeError("no stream")
            ),
            mock.patch.object(
                local_runner,
                "_http_json_cancellable",
                return_value={"choices": [{"message": {"content": "blocking answer"}}]},
            ),
        ):
            answer = runner.complete("hi", on_text=seen.append)
        self.assertEqual(answer, "blocking answer")
        # Blocking path still owns the emit — exactly once, the whole answer.
        self.assertEqual(seen, ["blocking answer"])

    def test_incomplete_stream_is_not_silently_reissued(self):
        """A partial answer is evidence of a broken stream, not a retry trigger."""

        runner = OpenAICompatibleRunner("http://localhost:1234/v1", "m")
        conn = _FakeConn(
            _FakeResponse(
                [
                    b'data: {"choices":[{"delta":{"content":"G"}}]}\n',
                    b"",
                ]
            )
        )
        with (
            mock.patch.object(
                local_runner.http.client, "HTTPConnection", return_value=conn
            ),
            mock.patch.object(
                local_runner,
                "_http_json_cancellable",
                return_value={
                    "choices": [{"message": {"content": "unexpected retry"}}]
                },
            ) as blocking,
        ):
            with self.assertRaisesRegex(RuntimeError, "before a terminal marker"):
                runner.complete("What is 2+2?", on_text=lambda _text: None)
        blocking.assert_not_called()

    def test_no_on_text_uses_the_blocking_path_unchanged(self):
        runner = OpenAICompatibleRunner("http://localhost:1234/v1", "m")
        with (
            mock.patch.object(local_runner, "_stream_chat") as streamer,
            mock.patch.object(
                local_runner,
                "_http_json_cancellable",
                return_value={"choices": [{"message": {"content": "plain"}}]},
            ),
        ):
            answer = runner.complete("hi")
        streamer.assert_not_called()
        self.assertEqual(answer, "plain")


class _StreamingFakeRunner:
    name = "local"
    model = "fake"

    def available(self):
        return True

    def complete(self, prompt, *, system=None, timeout=60.0, cancel=None, on_text=None):
        if on_text is not None:
            on_text("streamed answer")
            return "streamed answer"
        return "streamed answer"


class _BlockingFakeRunner:
    name = "local"
    model = "fake"

    def available(self):
        return True

    def complete(self, prompt, *, system=None, timeout=60.0, cancel=None):
        return "blocking answer"


class RunAskWiringTests(unittest.TestCase):
    def _ask(self, runner, sink):
        from vestahub.ask import run_ask

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            return run_ask(
                root, "explain this", runner=runner, record=False, on_text=sink
            )

    def test_streaming_runner_marks_result_streamed(self):
        seen: list[str] = []
        result = self._ask(_StreamingFakeRunner(), seen.append)
        self.assertEqual(result["status"], "answered_locally")
        self.assertTrue(result["streamed"])
        self.assertEqual(seen, ["streamed answer"])

    def test_runner_without_on_text_is_not_marked_streamed(self):
        seen: list[str] = []
        result = self._ask(_BlockingFakeRunner(), seen.append)
        self.assertEqual(result["status"], "answered_locally")
        self.assertFalse(result["streamed"])
        self.assertEqual(result["answer"], "blocking answer")
        self.assertEqual(seen, [])  # caller (pipeline) emits, not run_ask


if __name__ == "__main__":
    unittest.main()


class OllamaNdjsonStreamingTests(unittest.TestCase):
    """Ollama streams too (#295 gate 10).

    It was the one adapter that could not, purely because it speaks NDJSON
    rather than SSE. The consequence was not just a missing nicety: a failure
    part-way through discarded output that every other provider kept, so the
    same tokens were regenerated on the retry.
    """

    def _conn(self, lines):
        return _FakeConn(_FakeResponse(lines))

    def test_ndjson_deltas_stream_and_report_usage(self):
        lines = [
            b'{"message":{"content":"Hel"},"done":false}\n',
            b'{"message":{"content":"lo"},"done":false}\n',
            b'{"done":true,"prompt_eval_count":7,"eval_count":2}\n',
            b"",
        ]
        seen: list[str] = []
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=self._conn(lines)
        ):
            text, usage = _stream_chat(
                "http://localhost:11434/api/chat",
                payload={"model": "llama3", "messages": []},
                timeout=5.0,
                cancel=None,
                extra_headers=None,
                on_delta=seen.append,
                decode=local_runner._ndjson_delta,
                accept="application/x-ndjson",
            )
        self.assertEqual(seen, ["Hel", "lo"])
        self.assertEqual(text, "Hello")
        self.assertEqual(usage, {"prompt_eval_count": 7, "eval_count": 2})

    def test_a_cut_stream_is_not_passed_off_as_a_complete_answer(self):
        # No {"done":true} ever arrives. Returning "Hel" as the answer would
        # present a truncated reply as a finished one.
        lines = [b'{"message":{"content":"Hel"},"done":false}\n', b""]
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=self._conn(lines)
        ):
            with self.assertRaises(local_runner.IncompleteStreamError):
                _stream_chat(
                    "http://localhost:11434/api/chat",
                    payload={"model": "llama3", "messages": []},
                    timeout=5.0,
                    cancel=None,
                    extra_headers=None,
                    on_delta=lambda _c: None,
                    decode=local_runner._ndjson_delta,
                )

    def test_an_error_object_in_the_stream_is_raised_not_ignored(self):
        lines = [b'{"error":"model llama3 not found"}\n', b""]
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=self._conn(lines)
        ):
            with self.assertRaises(RuntimeError) as caught:
                _stream_chat(
                    "http://localhost:11434/api/chat",
                    payload={"model": "llama3", "messages": []},
                    timeout=5.0,
                    cancel=None,
                    extra_headers=None,
                    on_delta=lambda _c: None,
                    decode=local_runner._ndjson_delta,
                )
        self.assertIn("not found", str(caught.exception))

    def test_content_on_the_terminal_line_is_not_dropped(self):
        # Ollama may put the last token on the same object that sets done.
        lines = [b'{"message":{"content":"done."},"done":true}\n', b""]
        seen: list[str] = []
        with mock.patch.object(
            local_runner.http.client, "HTTPConnection", return_value=self._conn(lines)
        ):
            text, _usage = _stream_chat(
                "http://localhost:11434/api/chat",
                payload={"model": "llama3", "messages": []},
                timeout=5.0,
                cancel=None,
                extra_headers=None,
                on_delta=seen.append,
                decode=local_runner._ndjson_delta,
            )
        self.assertEqual(text, "done.")
        self.assertEqual(seen, ["done."])

    def test_complete_streams_when_on_text_is_given(self):
        seen: list[str] = []
        runner = local_runner.OllamaRunner(model="llama3")
        with mock.patch.object(
            local_runner, "_stream_chat", return_value=("Hi there", {})
        ) as streamer:
            answer = runner.complete("hello", on_text=seen.append)
        self.assertEqual(answer, "Hi there")
        self.assertEqual(
            streamer.call_args.kwargs["decode"], local_runner._ndjson_delta
        )

    def test_a_failed_stream_falls_back_to_the_blocking_path_once(self):
        runner = local_runner.OllamaRunner(model="llama3")
        seen: list[str] = []
        with mock.patch.object(
            local_runner, "_stream_chat", side_effect=RuntimeError("no stream")
        ):
            with mock.patch.object(
                local_runner,
                "_http_json_cancellable",
                return_value={"message": {"content": "blocking answer"}},
            ) as blocking:
                answer = runner.complete("hello", on_text=seen.append)
        self.assertEqual(answer, "blocking answer")
        self.assertEqual(blocking.call_count, 1)
        self.assertEqual(seen, ["blocking answer"])  # emitted once, not twice

    def test_an_incomplete_stream_is_never_silently_reissued(self):
        # Re-requesting would duplicate content the user can already see and
        # pay for it again, while hiding the interruption as a clean answer.
        runner = local_runner.OllamaRunner(model="llama3")
        with mock.patch.object(
            local_runner,
            "_stream_chat",
            side_effect=local_runner.IncompleteStreamError("cut"),
        ):
            with mock.patch.object(local_runner, "_http_json_cancellable") as blocking:
                with self.assertRaises(local_runner.IncompleteStreamError):
                    runner.complete("hello", on_text=lambda _c: None)
        blocking.assert_not_called()

    def test_the_blocking_path_is_unchanged_without_on_text(self):
        runner = local_runner.OllamaRunner(model="llama3")
        with mock.patch.object(local_runner, "_stream_chat") as streamer:
            with mock.patch.object(
                local_runner,
                "_http_json_cancellable",
                return_value={"message": {"content": "plain"}},
            ):
                self.assertEqual(runner.complete("hello"), "plain")
        streamer.assert_not_called()
