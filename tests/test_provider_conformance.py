"""Every supported adapter is held to the same contract (#295, gate 10).

Workstream C: *"Define one adapter protocol … Conformance-test every supported
provider/model/CLI/local runtime."* Until now there was no such matrix, and the
two adapter families had quietly diverged:

===========================  ====================  ==============================
family                       entry point           on provider failure
===========================  ====================  ==============================
``AccountRunner`` (CLIs)     ``stream()``          returns a result dict
``LocalRunner`` (HTTP)       ``complete()``        **raises**
===========================  ====================  ==============================

Everything above them is written against *one* set of promises, so wherever an
adapter breaks one the symptom reaches the user as "sometimes it works and
sometimes it doesn't".

Each probe below wraps one real adapter and drives it through a scripted fake
transport, at the same seams the existing suites use (`accounts._popen`,
`local_runner._http_json_cancellable`). No CLI is launched, no socket is opened
and nothing is spent.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from typing import Any, Callable
from unittest import mock

from opaihub import accounts, local_runner
from opaihub.accounts import AccountRunner
from opaihub.ask import _complete_streaming
from opaihub.local_runner import OllamaRunner, OpenAICompatibleRunner
from opaihub.provider_conformance import (
    CLAUSE_IDS,
    CLAUSES,
    Outcome,
    Script,
    check,
    format_matrix,
)


# ---------------------------------------------------------------------------
# A scripted stand-in for a provider CLI's pipes
# ---------------------------------------------------------------------------


class _Pipe:
    def __init__(self, lines: list[str], hang: bool = False) -> None:
        self._lines = list(lines)
        self._hang = hang
        self._i = 0

    def readline(self) -> str:
        if self._i < len(self._lines):
            self._i += 1
            return self._lines[self._i - 1]
        if self._hang:
            time.sleep(0.02)
            return "\n"  # a keep-alive, not EOF, so the read loop stays live
        return ""


class _FakeProc:
    def __init__(
        self, out: list[str], err: list[str], *, hang: bool, code: int
    ) -> None:
        self.stdout = _Pipe(out, hang)
        self.stderr = _Pipe(err)
        self.returncode = code
        self._alive = True

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return self.returncode


def _claude_lines(script: Script) -> tuple[list[str], list[str], bool, int]:
    """Render a Script as claude's ``stream-json`` output."""
    out: list[str] = []
    for chunk in script.chunks:
        out.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": chunk}]},
                }
            )
            + "\n"
        )
    if script.then == "ok":
        payload: dict[str, Any] = {"type": "result"}
        if script.cost is not None:
            payload["total_cost_usd"] = script.cost
        out.append(json.dumps(payload) + "\n")
        return out, [], False, 0
    if script.then == "empty":
        return [], [], False, 0
    if script.then == "timeout":
        return out, [], True, 0
    return out, [script.error + "\n"], False, 1


def _plain_lines(script: Script) -> tuple[list[str], list[str], bool, int]:
    """Render a Script as a CLI that streams plain text (copilot)."""
    out = [chunk if chunk.endswith("\n") else chunk + "\n" for chunk in script.chunks]
    if script.then == "ok":
        return out, [], False, 0
    if script.then == "empty":
        return [], [], False, 0
    if script.then == "timeout":
        return out, [], True, 0
    return out, [script.error + "\n"], False, 1


class AccountProbe:
    """Drives a provider CLI adapter through a scripted subprocess."""

    def __init__(self, account_id: str, renderer: Callable[..., Any]) -> None:
        self.account_id = account_id
        self.name = f"{account_id} (CLI)"
        self._render = renderer

    def available(self) -> bool:
        return AccountRunner(self.account_id, "").available()

    def run(
        self,
        script: Script,
        *,
        cancel: threading.Event | None = None,
        timeout: float = 30.0,
        on_text: Callable[[str], None] | None = None,
    ) -> Outcome:
        runner = AccountRunner(self.account_id, "/nonexistent/cli", model="opus")
        out, err, hang, code = self._render(script)
        proc = _FakeProc(out, err, hang=hang, code=code)
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = runner.stream(
                "do the thing", cancel=cancel, timeout=timeout, on_text=on_text
            )
        return Outcome(
            text=str(result.get("text") or ""),
            cancelled=bool(result.get("cancelled")),
            timed_out=bool(result.get("timed_out")),
            error=str(result.get("error") or ""),
            cost=result.get("cost"),
        )


# ---------------------------------------------------------------------------
# Local HTTP adapters
# ---------------------------------------------------------------------------


class LocalProbe:
    """Drives a local HTTP adapter through a scripted transport.

    Calls through `ask._complete_streaming`, the product's own entry point,
    rather than `runner.complete` directly. The two local runners take
    *different* keyword sets — `OpenAICompatibleRunner.complete` accepts
    `on_text`, `OllamaRunner.complete` does not — and the product bridges that
    by catching `TypeError` and retrying without the argument. Probing the raw
    method would measure a call the product never makes and would report a
    crash the user never sees.

    The divergence is still real, and the matrix records its consequence:
    Ollama cannot emit partial output, so a mid-run failure loses everything,
    while an OpenAI-compatible endpoint keeps what it already streamed.
    """

    #: Response envelope, which differs per local API.
    ENVELOPES = {
        "ollama": lambda text: {"message": {"content": text}},
        "openai": lambda text: {"choices": [{"message": {"content": text}}]},
    }

    def __init__(self, name: str, factory: Callable[[], Any], envelope: str) -> None:
        self.name = name
        self._factory = factory
        self._envelope = self.ENVELOPES[envelope]

    def available(self) -> bool:
        # A dead endpoint, which is what the picker meets most often.
        with mock.patch.object(
            local_runner, "_http_json", side_effect=OSError("connection refused")
        ):
            return self._factory().available()

    def run(
        self,
        script: Script,
        *,
        cancel: threading.Event | None = None,
        timeout: float = 30.0,
        on_text: Callable[[str], None] | None = None,
    ) -> Outcome:
        runner = self._factory()
        emitted: list[str] = []

        def _fake_stream(
            url,
            *,
            payload,
            timeout,
            cancel=None,
            extra_headers=None,
            on_delta=None,
            decode=None,  # protocol selector: SSE for OpenAI, NDJSON for Ollama
            accept=None,
        ):
            for chunk in script.chunks:
                if cancel is not None and cancel.is_set():
                    raise local_runner.LocalRunCancelled()
                emitted.append(chunk)
                if on_delta is not None:
                    on_delta(chunk)
            if cancel is not None and cancel.is_set():
                raise local_runner.LocalRunCancelled()
            if script.then == "fail":
                raise OSError(script.error)
            if script.then == "timeout":
                time.sleep(max(timeout, 0.05) + 0.2)
                raise TimeoutError("read timed out")
            if script.then == "empty":
                return "", {}
            return "".join(script.chunks), {"total_cost_usd": script.cost}

        def _fake_blocking(
            url, *, method, payload, timeout, cancel=None, extra_headers=None
        ):
            if cancel is not None and cancel.is_set():
                raise local_runner.LocalRunCancelled()
            if script.then == "fail":
                raise OSError(script.error)
            if script.then == "timeout":
                raise TimeoutError("read timed out")
            text = "" if script.then == "empty" else "".join(script.chunks)
            return self._envelope(text)

        with mock.patch.object(local_runner, "_stream_chat", _fake_stream):
            with mock.patch.object(
                local_runner, "_http_json_cancellable", _fake_blocking
            ):
                try:
                    text, _streamed = _complete_streaming(
                        runner, "do the thing", cancel=cancel, on_text=on_text
                    )
                except local_runner.LocalRunCancelled:
                    return Outcome(text="".join(emitted), cancelled=True)
                except TimeoutError as exc:
                    return Outcome(
                        text="".join(emitted),
                        timed_out=True,
                        error=str(exc) or "timed out",
                    )
                except Exception as exc:  # noqa: BLE001 - the contract is the point
                    return Outcome(
                        text="".join(emitted), error=str(exc) or type(exc).__name__
                    )
        cost = script.cost if script.then == "ok" else None
        return Outcome(
            text=text,
            error="" if text.strip() else "the provider returned no content",
            cost=cost,
        )


def _probes() -> list[Any]:
    """Every adapter shape OPai advertises support for."""
    return [
        AccountProbe("claude", _claude_lines),
        AccountProbe("copilot", _plain_lines),
        LocalProbe("ollama (HTTP)", lambda: OllamaRunner(), "ollama"),
        LocalProbe(
            "openai-compatible (HTTP)",
            lambda: OpenAICompatibleRunner("http://127.0.0.1:9", model="local-model"),
            "openai",
        ),
    ]


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


class ClauseDefinitionTests(unittest.TestCase):
    def test_every_clause_names_the_symptom_it_prevents(self) -> None:
        # A clause nobody can trace to a user-visible failure is a style
        # preference, and style preferences must not block a release.
        for clause in CLAUSES:
            with self.subTest(clause=clause.id):
                self.assertTrue(clause.statement.strip())
                self.assertGreater(len(clause.symptom.strip()), 30, clause.id)

    def test_clause_ids_are_unique(self) -> None:
        self.assertEqual(len(CLAUSE_IDS), len(set(CLAUSE_IDS)))


class ConformanceMatrixTests(unittest.TestCase):
    """The gate itself: every advertised adapter passes every clause."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.reports = [check(probe) for probe in _probes()]

    def test_every_adapter_is_checked_against_every_clause(self) -> None:
        # A silently skipped clause would read as a pass in the matrix.
        for report in self.reports:
            with self.subTest(adapter=report.adapter):
                self.assertEqual(
                    sorted(r.clause for r in report.results), sorted(CLAUSE_IDS)
                )

    def test_every_adapter_conforms(self) -> None:
        broken = {
            report.adapter: [f"{r.clause}: {r.detail}" for r in report.failures()]
            for report in self.reports
            if not report.passed
        }
        self.assertEqual(broken, {}, "\n" + format_matrix(self.reports))


class DefectsTheMatrixFoundTests(unittest.TestCase):
    """The three real bugs the first conformance run surfaced.

    Covered directly as well as through the matrix: the matrix proves the
    contract holds across adapters, these prove each specific defect stays
    fixed and say plainly what it cost the user.
    """

    def _claude(self, script: Script, **kw):
        out, err, hang, code = _claude_lines(script)
        proc = _FakeProc(out, err, hang=hang, code=code)
        runner = AccountRunner("claude", "/nonexistent/cli", model="opus")
        with mock.patch.object(accounts, "_popen", return_value=proc):
            return runner.stream("do the thing", timeout=5, **kw)

    def test_output_the_user_watched_appear_survives_a_failure(self) -> None:
        # Was: text="" — the streamed answer was thrown away with the error,
        # so the retry regenerated and re-paid for the same tokens.
        result = self._claude(
            Script(chunks=("I found the ", "bug in parser.py"), then="fail")
        )
        self.assertIn("I found the ", result["text"])
        self.assertTrue(result["error"], "the failure must still be reported")

    def test_an_empty_reply_is_a_failure_not_a_silent_success(self) -> None:
        # Was: {"text": "", "cost": None} with no error — the blank reply that
        # renders as OPai having answered when it has not.
        result = self._claude(Script(then="empty"))
        self.assertTrue(result.get("error"), "an empty run must not look successful")
        self.assertEqual(result["error"]["code"], "NO_RESPONSE")

    def test_a_run_that_did_work_but_said_nothing_is_not_a_failure(self) -> None:
        # The other side of the same rule: an edit-mode run can legitimately
        # finish having changed files and said nothing. Calling that a failure
        # would be its own lie, so tool steps are the discriminator.
        edit = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Edit",
                            "input": {"file_path": "a.py"},
                        }
                    ]
                },
            }
        )
        proc = _FakeProc([edit + "\n"], [], hang=False, code=0)
        runner = AccountRunner("claude", "/nonexistent/cli", model="opus")
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = runner.stream("edit it", timeout=5, allow_edits=True)
        self.assertIsNone(result.get("error"), result.get("error"))

    def test_a_broken_listener_cannot_kill_the_run(self) -> None:
        # Was: the exception propagated out of stream(), destroying a healthy
        # run because one activity line failed to render.
        def boom(_chunk):
            raise RuntimeError("a rendering bug in the activity line")

        result = self._claude(Script(chunks=("a", "b"), cost=0.01), on_text=boom)
        self.assertEqual(result["text"], "ab")
        self.assertIsNone(result.get("error"))


class HarnessTests(unittest.TestCase):
    """The harness must fail a bad adapter, or the matrix means nothing."""

    def test_an_adapter_that_swallows_cancellation_fails(self) -> None:
        class Swallows:
            name = "swallows-cancel"

            def available(self) -> bool:
                return True

            def run(self, script, *, cancel=None, timeout=30.0, on_text=None):
                return Outcome(text="an answer")  # cancel ignored entirely

        report = check(Swallows())
        self.assertFalse(report.passed)
        failed = {r.clause for r in report.failures()}
        self.assertIn("cancel_is_reported", failed)

    def test_an_adapter_that_raises_is_reported_not_propagated(self) -> None:
        class Explodes:
            name = "explodes"

            def available(self) -> bool:
                raise RuntimeError("boom")

            def run(self, script, *, cancel=None, timeout=30.0, on_text=None):
                raise RuntimeError("boom")

        report = check(Explodes())  # must not raise: the matrix is the output
        self.assertFalse(report.passed)
        self.assertEqual(len(report.results), len(CLAUSE_IDS))

    def test_a_fabricated_zero_cost_fails(self) -> None:
        class FreeLunch:
            name = "free-lunch"

            def available(self) -> bool:
                return True

            def run(self, script, *, cancel=None, timeout=30.0, on_text=None):
                return Outcome(text="hi", cost=0.0)  # paid run reported as free

        self.assertIn(
            "cost_is_real_or_unknown",
            {r.clause for r in check(FreeLunch()).failures()},
        )

    def test_the_matrix_renders_every_clause(self) -> None:
        rendered = format_matrix([check(_probes()[0])])
        for clause in CLAUSE_IDS:
            self.assertIn(clause, rendered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
