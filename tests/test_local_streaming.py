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

from opaihub import local_runner
from opaihub.local_runner import OpenAICompatibleRunner, _stream_chat


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
        from opaihub.ask import run_ask

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
