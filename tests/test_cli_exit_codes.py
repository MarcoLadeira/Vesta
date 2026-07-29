"""CLI exit codes name which ending a run had (#295 Workstream H, gate 6).

Workstream H: *"CLI exit codes map deterministically to canonical terminal
states."* They did not. Every non-completed ending collapsed to `2`, so a script
could not tell:

- `timeout` — worth retrying, from
- `blocked` — a refusal that retrying will hit again, from
- `partial` — work that actually landed but could not be fully verified.

"It failed" was the only thing automation could learn, which makes OPai unusable
in the CI and scripted flows it is meant to serve.

The mapping lives in `run_state` beside the states themselves, so the CLI cannot
drift from the canonical vocabulary — the same reason the verdict *labels* live
in one place (`test_gui_cli_parity.py` guards that half).
"""

from __future__ import annotations

import unittest

from opaihub.completion import CompletionVerdict
from opaihub.run_state import (
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    RunState,
    exit_code_for,
)


class ContractTests(unittest.TestCase):
    def test_every_terminal_state_has_an_exit_code(self) -> None:
        for state in TERMINAL_STATES:
            with self.subTest(state=state):
                self.assertIsInstance(exit_code_for(state), int)

    def test_every_terminal_state_has_a_distinct_code(self) -> None:
        # The whole point: two endings sharing a code is the bug being fixed.
        codes = [exit_code_for(state) for state in TERMINAL_STATES]
        self.assertEqual(len(codes), len(set(codes)))

    def test_success_is_zero_and_nothing_else_is(self) -> None:
        # Keeps the ordinary idiom working: `if ! opai ask ...` behaves exactly
        # as it did before this contract existed.
        self.assertEqual(exit_code_for(RunState.COMPLETED), 0)
        for state in TERMINAL_STATES - {RunState.COMPLETED}:
            with self.subTest(state=state):
                self.assertNotEqual(exit_code_for(state), 0)

    def test_failed_keeps_the_code_it_already_meant(self) -> None:
        # Backward compatibility: 2 was the old catch-all, and it stays on the
        # ending it most nearly described.
        self.assertEqual(exit_code_for(RunState.FAILED), 2)

    def test_cancelled_keeps_the_sigint_convention(self) -> None:
        # 130 is the shell's SIGINT code and was already returned for Ctrl+C.
        self.assertEqual(exit_code_for(RunState.CANCELLED), 130)

    def test_one_is_left_free_for_usage_errors(self) -> None:
        # argparse and most shells spend 1 on "you typed the command wrong". A
        # lifecycle outcome must not be confused with that.
        self.assertNotIn(1, {exit_code_for(state) for state in TERMINAL_STATES})

    def test_the_retryable_endings_are_distinguishable(self) -> None:
        # The concrete thing automation needs: retry a timeout, do not retry a
        # refusal, and treat a partial as neither.
        codes = {
            state: exit_code_for(state)
            for state in (RunState.TIMEOUT, RunState.BLOCKED, RunState.PARTIAL)
        }
        self.assertEqual(len(set(codes.values())), 3, codes)

    def test_a_non_terminal_state_has_no_exit_code(self) -> None:
        # The run has not ended; inventing a code would be inventing an outcome.
        for state in NON_TERMINAL_STATES:
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    exit_code_for(state)

    def test_every_completion_verdict_is_covered(self) -> None:
        # Terminal states are one-for-one with verdicts, so a new verdict must
        # arrive with an exit code rather than silently falling through.
        for verdict in CompletionVerdict:
            with self.subTest(verdict=verdict):
                self.assertIsInstance(exit_code_for(verdict.value), int)

    def test_codes_are_stable_values_not_incidental(self) -> None:
        # Pinned deliberately: these are a public contract that scripts encode,
        # so changing one is a breaking change and should fail here first.
        self.assertEqual(
            {state.value: exit_code_for(state) for state in TERMINAL_STATES},
            {
                "completed": 0,
                "failed": 2,
                "partial": 3,
                "blocked": 4,
                "timeout": 5,
                "cancelled": 130,
            },
        )


class CliWiringTests(unittest.TestCase):
    """Both CLI paths must use the contract, not their own arithmetic."""

    def test_neither_cli_path_hardcodes_a_terminal_exit_code(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        for name in ("opai/cli_stream.py", "opai/cli.py"):
            with self.subTest(name=name):
                source = (root / name).read_text(encoding="utf-8")
                self.assertIn("exit_code_for", source)

    def test_each_ending_exits_distinctly_through_the_real_cli(self) -> None:
        # The mapping is only worth anything if the command actually returns it.
        import tempfile
        from pathlib import Path
        from unittest import mock

        from _helpers import make_repo

        from opai.cli_stream import stream_ask

        cases = {
            "completed": (
                {"status": "answered", "answer": "done"},
                "completed",
                0,
            ),
            "partial": ({"status": "answered", "answer": "half"}, "partial", 3),
            "blocked": ({"status": "blocked", "answer": "no"}, "blocked", 4),
            "timeout": ({"status": "failed", "answer": ""}, "timeout", 5),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            for name, (base, verdict, expected) in cases.items():
                with self.subTest(name=name):
                    result = dict(base)
                    result["completion_verdict"] = {"verdict": verdict}
                    with mock.patch(
                        "opaihub.gui_pipeline.handle_gui_message",
                        return_value=result,
                    ):
                        code = stream_ask(
                            root, "do it", model="auto", mode="ask", json_out=True
                        )
                    self.assertEqual(code, expected)

            # Cancellation has no verdict to read; it still exits distinctly.
            with mock.patch(
                "opaihub.gui_pipeline.handle_gui_message",
                return_value={"status": "cancelled", "answer": ""},
            ):
                self.assertEqual(
                    stream_ask(root, "do it", model="auto", mode="ask", json_out=True),
                    130,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
