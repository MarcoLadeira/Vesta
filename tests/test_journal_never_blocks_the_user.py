"""#818: none of this may stop a person doing what they asked for.

This epic adds a lot of checking -- liveness verdicts, approval binding,
launcher health, completion evidence, parity comparisons. Every one of them is
a place where a bug could turn into "Vesta refuses to send my message" or "the
Approve button does nothing", and that failure would be worse than any of the
lies being fixed. A tool that will not do the thing you asked is not more
trustworthy than one that occasionally reports it wrongly.

So the properties below are the ones that keep the epic honest in the other
direction. They are deliberately about *refusal*, not about correctness:

* the canonical journal is still write-only from the app's perspective -- no
  live surface reads it to decide anything, so no journal state can gate a
  turn, a push, a PR or a merge;
* a one-shot approval is refused only on positive evidence it belongs to a
  different run, never because the process could not identify itself;
* doctor's readiness is a report, not a gate;
* the CLI's exit code comes from the run's own result, not from the journal.

Stage 5 will make the journal readable by design, and when it does these tests
should be revisited deliberately rather than deleted quietly -- the point is
that a *read* which can refuse work is a decision somebody has to make on
purpose.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest import mock

from vestahub import command_consent

ROOT = Path(__file__).resolve().parents[1]

#: The modules a live turn actually runs through.
LIVE_SURFACES = (
    "vestahub/gui_pipeline.py",
    "vesta/gui_web.py",
    "vesta/gui_desktop.py",
    "vesta/cli_stream.py",
)

#: Journal calls a live surface may make. All of them write; none decide.
#: `beat_lease` restamps a heartbeat, and the `record_*` family appends.
WRITE_ONLY = {
    "beat_lease",
    "record_admission",
    "record_terminal",
    "record_event",
    "record_run_cost",
    "record_verification",
    "record_cancellation_phase",
    "record_run_snapshot",
}


def _journal_calls(source: str) -> list[tuple[str, int]]:
    """Every ``journal_*.something(...)`` call in a module."""

    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not isinstance(target, ast.Attribute):
            continue
        owner = target.value
        name = getattr(owner, "id", "") or getattr(owner, "attr", "")
        if name.startswith("journal_"):
            found.append((target.attr, node.lineno))
    return found


class TheJournalCannotGateATurnTests(unittest.TestCase):
    """Nothing a live surface does depends on reading the journal."""

    def test_live_surfaces_only_write_to_the_journal(self):
        reading = []
        for module in LIVE_SURFACES:
            source = (ROOT / module).read_text(encoding="utf-8")
            for attribute, line in _journal_calls(source):
                if attribute not in WRITE_ONLY:
                    reading.append(f"{module}:{line} journal_*.{attribute}")

        self.assertEqual(
            reading,
            [],
            "a live surface reads the journal. That is Stage 5's job and a "
            "deliberate decision, because a read that can refuse work is a "
            "read that can stop a user mid-task:\n  " + "\n  ".join(reading),
        )

    def test_the_write_list_is_honest_about_what_it_permits(self):
        """Every name on it must actually be a writer, or the test permits reads."""

        for name in WRITE_ONLY:
            with self.subTest(name=name):
                self.assertTrue(
                    name.startswith("record_") or name == "beat_lease",
                    f"{name} does not look like a writer",
                )


class AnApprovalIsRefusedOnlyOnEvidenceTests(unittest.TestCase):
    """The Approve button must never quietly do nothing.

    The process that spends a grant is the PreToolUse hook, and Vesta does not
    launch it -- the provider's CLI does. Whether VESTA_RUN_ID survives that hop
    is a third party's decision, so "I cannot say which run I am" has to be
    allowed. Refusing it would silently break every approved push on any
    provider that sanitises its hook environment.
    """

    def test_a_caller_that_cannot_identify_itself_is_allowed(self):
        self.assertTrue(command_consent.grant_belongs_to("run-A", ""))

    def test_a_grant_from_before_run_binding_is_allowed(self):
        self.assertTrue(command_consent.grant_belongs_to("", "run-A"))

    def test_neither_side_knowing_is_allowed(self):
        self.assertTrue(command_consent.grant_belongs_to("", ""))

    def test_only_two_different_named_runs_are_refused(self):
        self.assertFalse(command_consent.grant_belongs_to("run-A", "run-B"))

    def test_the_same_run_is_allowed(self):
        self.assertTrue(command_consent.grant_belongs_to("run-A", "run-A"))


class ReadinessIsAReportNotAGateTests(unittest.TestCase):
    """`vesta doctor` saying "attention" must not stop anything running.

    The launcher check added in this epic can flip that verdict, and a dead
    desktop icon is worth reporting -- but a report that silently became a
    precondition would mean a cosmetic problem could refuse real work.
    """

    def test_a_broken_launcher_does_not_fail_the_command(self):
        """A dead desktop icon must not make `vesta doctor` exit non-zero.

        The launcher check added in this epic can flip the verdict to
        "attention". If that also flipped the exit code, a cosmetic problem
        would start failing anybody's script that runs doctor.
        """

        import argparse
        import io
        import json
        import tempfile
        from contextlib import redirect_stdout
        from unittest import mock

        from vesta import cli

        broken = {
            "available": True,
            "healthy": False,
            "checked": 1,
            "broken": ["Vesta-Desktop"],
            "unreadable": [],
            "launchers": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                with mock.patch.object(cli, "_launcher_doctor", lambda: broken):
                    code = cli.cmd_doctor(argparse.Namespace(project=tmp, json=True))
            payload = json.loads(buffer.getvalue())

        self.assertEqual(payload["readiness"], "attention", "it should still say so")
        self.assertEqual(code, 0, "but saying so must not fail the command")

    def test_nothing_else_consults_doctor_to_make_a_decision(self):
        """The verdict has one consumer: the person reading it.

        A text scan for the string was the first version of this test and it
        flagged `_status_severity`, which maps a status onto a badge colour.
        Colouring a badge is not gating work, and a test that cannot tell the
        difference gets muted rather than fixed -- so this asks the precise
        question instead.
        """

        callers = []
        for path in sorted((ROOT / "vesta").rglob("*.py")) + sorted(
            (ROOT / "vestahub").rglob("*.py")
        ):
            if path.name == "cli.py":
                continue
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call):
                    target = node.func
                    name = getattr(target, "attr", "") or getattr(target, "id", "")
                    if name == "cmd_doctor":
                        rel = str(path.relative_to(ROOT)).replace("\\", "/")
                        callers.append(f"{rel}:{node.lineno}")

        self.assertEqual(
            callers,
            [],
            "doctor's readiness is a report; calling it to branch on the "
            "answer would make it a gate",
        )


class TheExitCodeComesFromTheRunNotTheJournalTests(unittest.TestCase):
    """A script driving `vesta ask` must not see its exit code move.

    The terminal verdicts this epic corrected are journal records. If the CLI
    derived its exit code from them instead of from the run's own result, the
    correction would have changed what every caller's automation sees.
    """

    def test_the_cli_reads_the_result_dict(self):
        source = (ROOT / "vesta" / "cli_stream.py").read_text(encoding="utf-8")

        self.assertIn(
            "def _terminal_verdict(result: dict[str, Any])",
            source,
            "the exit code must be derived from the run's own result",
        )
        self.assertEqual(
            _journal_calls(source),
            [],
            "cli_stream must not consult the journal at all",
        )


#: The reports this epic added. Every one is expensive by design -- they read
#: the whole event log, fold projections, compare populations -- and every one
#: belongs to `vesta doctor`, which a person runs when they want an answer.
DIAGNOSTIC_ONLY = (
    "run_table_parity",
    "turn_parity",
    "unevidenced_completions",
    "unconfirmed_cancellations",
    "inspect_launchers",
    "summary",
)

#: Where a turn and a boot actually happen. None of the above may appear here.
HOT_PATHS = (
    "vestahub/gui_pipeline.py",
    "vesta/gui_web.py",
    "vesta/gui_desktop.py",
    "vesta/cli_stream.py",
    "vesta/bootstrap.py",
)


class TheExpensiveChecksStayOutOfTheHotPathTests(unittest.TestCase):
    """A diagnostic that ran on every turn would be a diagnostic you feel.

    These reports replay the event log and compare populations. That is the
    right cost for a command someone chose to run, and the wrong cost for
    something between a person pressing Send and their answer arriving.
    """

    def test_no_hot_path_runs_a_report(self):
        offenders = []
        for module in HOT_PATHS:
            path = ROOT / module
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
                if name in DIAGNOSTIC_ONLY:
                    offenders.append(f"{module}:{node.lineno} {name}()")

        self.assertEqual(
            offenders,
            [],
            "a diagnostic report is on the path of every turn or boot:\n  "
            + "\n  ".join(offenders),
        )


class TheInspectorDidNotGetSlowerTests(unittest.TestCase):
    """The one render path this epic touched, measured as a share.

    `_github_row_value` now cites a stored verification instead of asserting
    readiness, which costs one small JSON read. Absolute milliseconds would
    make this a disk benchmark, so it is expressed as a fraction of the
    function it was added to.
    """

    def test_reading_the_stored_verdict_is_a_small_part_of_readiness(self):
        import time

        from vestahub import github_connector

        def one_round(fn, samples=30):
            started = time.perf_counter()
            for _ in range(samples):
                fn()
            return (time.perf_counter() - started) / samples

        # The best of several interleaved rounds, not one average: a load
        # spike during either measurement moved the ratio past the bound when
        # the whole suite ran in parallel. A minimum is what the code costs;
        # an average is what the machine happened to be doing.
        token = "t0ken"
        with mock.patch.object(
            github_connector, "stored_github_token", lambda: (token, "env")
        ):
            github_connector.github_readiness()
            added_rounds, whole_rounds = [], []
            for _ in range(7):
                added_rounds.append(
                    one_round(lambda: github_connector.last_verification(token))
                )
                whole_rounds.append(one_round(github_connector.github_readiness))
        added = min(added_rounds)
        whole = min(whole_rounds)

        self.assertGreater(whole, 0, "readiness took no measurable time at all")
        self.assertLess(
            added / whole,
            0.6,
            f"reading the stored verdict is {added / whole * 100:.0f}% of "
            "github_readiness; it should be a fraction of work that was "
            "already happening, not the bulk of it",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
