"""#617: provider capability decided from a signature before dispatch, never
from retrying after a caught exception.

The prior code at both sites called ``runner.complete(...)``, caught
``TypeError``, and retried with fewer arguments *only if the exception's
message happened to contain* a parameter name like "on_text", "cancel", or
"mode". That is unsafe by construction: the first call may already have
crossed the provider boundary (real cost, real output, a real subprocess
launched) before an *unrelated* internal ``TypeError`` was raised, and if its
message merely mentioned the magic substring, the retry dispatched the same
operation a second time. Reproduced empirically before either fix landed:

    >>> class FlakyRunner:
    ...     def complete(self, text, *, system, cancel=None, on_text=None):
    ...         # first call raises for an unrelated reason that happens to
    ...         # mention "on_text"
    ...         raise TypeError("unhashable type in on_text formatting internals")
    >>> _complete_streaming(FlakyRunner(), "task", cancel=None, on_text=lambda t: None)
    ('the real answer', False)   # runner.complete was called TWICE

These tests pin two things: the unrelated exception now propagates honestly
instead of being swallowed into a retry, and a *genuinely* narrower runner
(one that really lacks ``on_text``/``cancel``/``mode``) still degrades
gracefully — via signature inspection before the call, not exception text
after it.
"""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from opaihub.ask import _complete_streaming, _supports_kwarg
from tests._helpers import make_repo


class SupportsKwargTests(unittest.TestCase):
    def test_true_for_a_real_keyword_parameter(self):
        def f(*, on_text=None):
            pass

        self.assertTrue(_supports_kwarg(f, "on_text"))

    def test_false_for_a_missing_parameter(self):
        def f(*, cancel=None):
            pass

        self.assertFalse(_supports_kwarg(f, "on_text"))

    def test_true_when_the_callable_accepts_var_keyword(self):
        def f(**kwargs):
            pass

        self.assertTrue(_supports_kwarg(f, "anything"))

    def test_false_for_an_uninspectable_callable(self):
        # Unknown capability must never be assumed present.
        self.assertFalse(_supports_kwarg(len, "on_text"))


class CompleteStreamingSingleDispatchTests(unittest.TestCase):
    def test_normal_runner_streams_in_exactly_one_call(self):
        calls = []

        class Runner:
            def complete(self, text, *, system, cancel=None, on_text=None):
                calls.append({"cancel": cancel, "on_text": on_text})
                if on_text:
                    on_text("chunk")
                return "answer"

        answer, streamed = _complete_streaming(
            Runner(), "task", cancel="c", on_text=lambda t: None
        )
        self.assertEqual(answer, "answer")
        self.assertTrue(streamed)
        self.assertEqual(len(calls), 1)

    def test_an_unrelated_internal_typeerror_propagates_instead_of_retrying(self):
        """The actual bug: a TypeError raised for a reason that has nothing
        to do with argument support, but whose message happens to contain
        "on_text", must not be swallowed into a second dispatch."""
        calls = []

        class FlakyRunner:
            def complete(self, text, *, system, cancel=None, on_text=None):
                calls.append(1)
                raise TypeError("unhashable type in on_text formatting internals")

        with self.assertRaises(TypeError):
            _complete_streaming(
                FlakyRunner(), "task", cancel=None, on_text=lambda t: None
            )
        self.assertEqual(len(calls), 1, "must dispatch at most once, even on error")

    def test_an_unrelated_cancel_mentioning_typeerror_also_propagates(self):
        calls = []

        class FlakyRunner:
            def complete(self, text, *, system, cancel=None):
                calls.append(1)
                raise TypeError("cancel token comparison failed: NoneType")

        with self.assertRaises(TypeError):
            _complete_streaming(FlakyRunner(), "task", cancel=None, on_text=None)
        self.assertEqual(len(calls), 1)

    def test_a_runner_genuinely_missing_on_text_still_degrades_gracefully(self):
        """A real capability gap (not an unrelated bug) must still work —
        decided from the signature, not by triggering and catching an error."""
        calls = []

        class NoStreamingRunner:
            def complete(self, text, *, system, cancel=None):
                calls.append({"cancel": cancel})
                return "answer"

        answer, streamed = _complete_streaming(
            NoStreamingRunner(), "task", cancel="c", on_text=lambda t: None
        )
        self.assertEqual(answer, "answer")
        self.assertFalse(streamed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["cancel"], "c")

    def test_a_runner_genuinely_missing_cancel_still_degrades_gracefully(self):
        calls = []

        class MinimalRunner:
            def complete(self, text, *, system):
                calls.append(1)
                return "answer"

        answer, streamed = _complete_streaming(
            MinimalRunner(), "task", cancel="c", on_text=lambda t: None
        )
        self.assertEqual(answer, "answer")
        self.assertFalse(streamed)
        self.assertEqual(len(calls), 1)

    def test_kwargs_accepting_runner_gets_everything(self):
        received = {}

        class KwargsRunner:
            def complete(self, text, **kwargs):
                received.update(kwargs)
                return "answer"

        _complete_streaming(KwargsRunner(), "task", cancel="c", on_text=lambda t: None)
        self.assertIn("cancel", received)
        self.assertIn("on_text", received)

    def test_a_typeerror_raised_by_the_callback_cannot_cause_a_second_call(self):
        """#617 AC4: "Callback-raised TypeError is classified correctly and
        cannot cause a second call."

        Distinct from the runner-raised case above: here the *callback the
        caller supplied* is what raises, from inside a dispatch that has
        already reached the provider and started streaming. Under the old
        text-sniffing fallback this was the nastiest variant — a TypeError
        from user code, arriving mid-stream, whose message could mention
        anything at all. The answer must be: surface it, never re-dispatch.
        """
        calls = []

        def exploding_on_text(chunk):
            raise TypeError("on_text sink rejected a chunk")

        class StreamingRunner:
            def complete(self, text, *, system, cancel=None, on_text=None):
                calls.append(1)
                if on_text:
                    on_text("partial chunk")  # raises from inside the dispatch
                return "answer"

        with self.assertRaises(TypeError):
            _complete_streaming(
                StreamingRunner(), "task", cancel=None, on_text=exploding_on_text
            )
        self.assertEqual(
            len(calls), 1, "a callback failure must not re-dispatch the operation"
        )


class AskAccountSingleDispatchTests(unittest.TestCase):
    """The second site the issue names: opai/app_state.py's account
    dispatch. Same reproduction shape, now against a fake mimicking
    AccountRunner.complete's real signature."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_account_runner_module(self, complete_impl):
        import unittest.mock as mock

        run = mock.MagicMock()
        run.stream = None
        del run.stream  # hasattr(run, "stream") must be False -> non-stream path
        run.complete = complete_impl
        return run

    def test_unrelated_mode_mentioning_typeerror_propagates_not_retried(self):
        # _ask_account wraps any exception from the dispatch into an honest
        # {"status": "failed", ...} result (its own outer except Exception) —
        # the fix under test is that this happens after exactly one dispatch,
        # not that the exception escapes uncaught.
        from opai.app_state import _ask_account

        calls = []

        def complete(task, *, project_root=None, allow_edits=False, mode=None):
            calls.append(1)
            raise TypeError("mode selection buffer overflow")

        run = self._fake_account_runner_module(complete)
        result = _ask_account(
            self.root,
            "do a paid task",
            "claude",
            allow_edits=False,
            runner=run,
            mode="ask",
        )
        self.assertEqual(len(calls), 1, "must dispatch at most once, even on error")
        self.assertEqual(result["status"], "failed")

    def test_a_runner_genuinely_missing_mode_still_completes(self):
        from opai.app_state import _ask_account

        calls = []

        def complete(task, *, project_root=None, allow_edits=False):
            calls.append(1)
            return {"text": "done", "cost": 0.01}

        run = self._fake_account_runner_module(complete)
        result = _ask_account(
            self.root,
            "do a task",
            "claude",
            allow_edits=False,
            runner=run,
            mode="ask",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.get("answer") or result.get("text"), "done")

    def test_a_kwargs_accepting_runner_still_receives_mode(self):
        """Regression: a naive `"mode" in inspect.signature(...).parameters`
        membership check has no literal "mode" to find on a **kwargs-only
        complete() — real for every current runner, including the shared
        tests/_helpers.py FakeAccountRunner double used across many test
        files. The first version of the #617 fix silently dropped `mode`
        for all of them; caught by test_agent_autonomy.py's
        test_pipeline_preserves_pinned_full_auto_for_implementation, which
        asserts a recorded call's mode. This test pins the same shape
        directly against _ask_account so the gap has its own guard too."""
        from opai.app_state import _ask_account

        received: dict = {}

        def complete(task, **kwargs):
            received.update(kwargs)
            return {"text": "done", "cost": 0.0}

        run = self._fake_account_runner_module(complete)
        _ask_account(
            self.root,
            "do a task",
            "claude",
            allow_edits=False,
            runner=run,
            mode="full-auto",
        )
        self.assertEqual(received.get("mode"), "full-auto")

    def test_kwargs_accepting_runner_receives_pre_recorded_operation_id(self):
        from opai.app_state import _ask_account

        received: dict = {}

        def complete(task, **kwargs):
            received.update(kwargs)
            return {"text": "done", "cost": 0.01}

        run = self._fake_account_runner_module(complete)
        result = _ask_account(
            self.root,
            "do a paid task",
            "claude",
            allow_edits=False,
            runner=run,
            mode="ask",
        )

        self.assertEqual(result["status"], "answered_by_account")
        self.assertTrue(received.get("operation_id"))
        self.assertEqual(received["operation_id"], result["ledger_call_id"])


if __name__ == "__main__":
    unittest.main()
