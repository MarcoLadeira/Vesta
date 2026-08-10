"""Event-ordering properties and cross-language golden vectors (#612).

`test_lifecycle_properties.py` already drives random walks against the graph
and proves terminal states never regress. The issue asks for more than that:
sequences "covering duplication, reordering, stale revision and terminal-state
attacks", and the guarantee that nothing reaches completion "without required
guard evidence". Those are event-stream properties rather than edge properties,
so they live here.

The cross-language half is executed, not pattern-matched. A regex over the
generated browser file proves the bytes were written; running the same vectors
through node and comparing the answers proves the two surfaces *agree*, which
is the entire point of the issue.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - fixed argv, no shell
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from opaihub.generated_lifecycle import (
    DEGRADED_INPUTS,
    LEGACY_STATE_MAP,
    LEGACY_STATUS_MAP,
    STATE_IDS,
    TERMINAL_STATE_IDS,
    transition_spec,
)
from opaihub.run_state import can_transition, is_terminal, transition

ROOT = Path(__file__).resolve().parents[1]
BROWSER_CONTRACT = ROOT / "opai" / "assets" / "web" / "generated-lifecycle.js"

_EXAMPLES = 10_000
_SETTINGS = settings(
    max_examples=_EXAMPLES,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    deadline=None,
)

states = st.sampled_from(sorted(STATE_IDS))


def _apply(sequence, *, start="queued"):
    """Fold an event sequence through the canonical machine."""
    current = start
    for nxt in sequence:
        current = transition(current, nxt, source="test").value
    return current


class EventStreamPropertyTests(unittest.TestCase):
    @_SETTINGS
    @given(sequence=st.lists(states, min_size=1, max_size=12))
    def test_a_duplicate_event_cannot_contradict_the_state_it_confirms(self, sequence):
        """P3: replaying an event must be indistinguishable from seeing it once.

        A renderer reconnecting, a bridge re-emitting, and a journal replay all
        deliver the same transition twice; if the second copy could move the
        run somewhere else, every one of those becomes a correctness bug.
        """
        once = _apply(sequence)
        twice = _apply([item for event in sequence for item in (event, event)])
        self.assertEqual(once, twice)

    @_SETTINGS
    @given(sequence=st.lists(states, min_size=1, max_size=10), stale=states)
    def test_a_stale_event_cannot_resurrect_a_terminal_run(self, sequence, stale):
        """P10: a late event from a disconnected client is not authority."""
        final = _apply(sequence)
        if not is_terminal(final):
            return
        self.assertEqual(_apply([stale], start=final), final)

    @_SETTINGS
    @given(sequence=st.lists(states, min_size=2, max_size=10))
    def test_reordering_can_change_the_result_but_never_breaks_terminality(
        self, sequence
    ):
        """P1: order matters, but no order escapes a terminal state.

        Deliberately not "reordering is a no-op" -- that would be false, and
        asserting it would force the machine to accept edges it must refuse.
        The invariant that must hold under every permutation is that once
        terminal, the run stays there.
        """
        final = _apply(sequence)
        reversed_final = _apply(list(reversed(sequence)))
        for outcome in (final, reversed_final):
            if is_terminal(outcome):
                self.assertEqual(_apply(list(sequence), start=outcome), outcome)

    @_SETTINGS
    @given(sequence=st.lists(states, min_size=1, max_size=12))
    def test_completion_is_only_ever_reached_through_a_guarded_edge(self, sequence):
        """P2: no generated path reaches `completed` without its guard.

        #612 requires that completion cannot bypass applicable #522
        verification. The lifecycle does not evaluate the evidence -- that is
        the verification subsystem's job -- but every edge that arrives at
        `completed` must *declare* a verification requirement, so there is
        somewhere for that evidence to be demanded.
        """
        current = "queued"
        for nxt in sequence:
            previous, current = current, transition(current, nxt, source="test").value
            if current == "completed" and previous != "completed":
                spec = transition_spec(previous, "completed")
                self.assertIsNotNone(spec, f"{previous} -> completed has no spec")
                self.assertIn(
                    spec["guards"]["verification"],
                    {"required", "required_if_applicable", "recorded_if_available"},
                    f"{previous} -> completed declares no verification guard",
                )

    @_SETTINGS
    @given(
        unknown=st.text(min_size=1, max_size=24), sequence=st.lists(states, max_size=6)
    )
    def test_an_unknown_future_state_never_becomes_success(self, unknown, sequence):
        """P6: a state a newer app wrote must degrade, never be guessed.

        The property is that the unknown event cannot *cause* success -- not
        that the run cannot already be successful. An earlier version asserted
        the latter and was wrong: for `sequence=['completed'], unknown='TIMEOUT'`
        the walk legitimately completed first, and the refused unknown edge
        then correctly preserved that terminal state. Asserting "never
        completed" would have demanded the machine corrupt a valid terminal
        run to satisfy a test.

        `TIMEOUT` also exposed a second flaw: it is absent from STATE_IDS but
        is a legacy alias resolving to the real `timeout` state, so filtering
        on STATE_IDS alone does not mean "unknown". Both maps are excluded.
        """
        lowered = unknown.strip().lower()
        if (
            lowered in STATE_IDS
            or lowered in LEGACY_STATUS_MAP
            or lowered in LEGACY_STATE_MAP
        ):
            return
        before = _apply(sequence)
        after = _apply([unknown], start=before)
        if before != "completed":
            self.assertNotEqual(
                after,
                "completed",
                f"unknown input {unknown!r} moved {before!r} to completed",
            )
        else:
            # Already completed. The unknown event may not be honoured, but the
            # schema does not say "refuse and keep": it declares where an
            # uninterpretable input lands, and that is a *typed degrade*, not a
            # guess. Reading the landing from the schema rather than naming it
            # here keeps this test honest if the declared behaviour changes --
            # it would then have to change deliberately, in the schema.
            degraded = DEGRADED_INPUTS["unknown_state"]["state"]
            self.assertIn(
                after,
                {"completed", degraded},
                f"unknown input {unknown!r} sent a completed run to {after!r}, "
                f"which is neither refusal nor the declared degrade {degraded!r}",
            )
            self.assertNotEqual(
                DEGRADED_INPUTS["unknown_state"]["compatibility"],
                "compatible",
                "an unknown state must never be marked compatible",
            )

    @_SETTINGS
    @given(sequence=st.lists(states, min_size=1, max_size=12))
    def test_the_same_sequence_always_yields_the_same_state(self, sequence):
        """P8: deterministic replay. Same input, same generated contract,
        same answer -- otherwise a journal cannot be trusted to rebuild a run."""
        self.assertEqual(_apply(sequence), _apply(sequence))

    def test_cancel_requested_is_never_terminal(self):
        """P7."""
        self.assertIn("cancel_requested", STATE_IDS)
        self.assertNotIn("cancel_requested", TERMINAL_STATE_IDS)

    def test_blocked_is_reachable_without_ever_attempting_execution(self):
        """P11: blocked-before-dispatch must not be recorded as a failure.

        The #621 work found two real statuses -- operation_unrecorded and
        cost_unreconciled -- where a guard refused to dispatch a paid call.
        Those are `blocked`, not `failed`: nothing was attempted. The graph has
        to make that expressible without passing through `running`.
        """
        # Reachable before anything runs: `queued`/`preparing` are pre-dispatch.
        self.assertTrue(can_transition("queued", "blocked"))
        self.assertTrue(can_transition("preparing", "blocked"))
        # Both terminal, but they are distinct outcomes -- a guard refusing to
        # dispatch must never be recorded as an attempt that failed.
        self.assertIn("blocked", TERMINAL_STATE_IDS)
        self.assertIn("failed", TERMINAL_STATE_IDS)
        self.assertNotEqual("blocked", "failed")
        # And the distinction survives into the CLI projection, which is where
        # a caller would otherwise be unable to tell them apart.
        from opaihub.generated_lifecycle import EXIT_CODES

        self.assertNotEqual(EXIT_CODES["blocked"], EXIT_CODES["failed"])


class CrossLanguageGoldenTests(unittest.TestCase):
    """P5: Python and the browser must answer identically.

    One batch through node rather than a subprocess per example: the point is
    agreement over the whole vector set, and 10,000 process spawns would make
    the suite unrunnable without proving anything extra.
    """

    def _browser_answers(self, vectors: list[list[str]]) -> list[dict]:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed on this machine")
        script = (
            f"require({json.dumps(str(BROWSER_CONTRACT))});"
            "var c = globalThis.OPaiLifecycle;"
            f"var vectors = {json.dumps(vectors)};"
            "var out = vectors.map(function (v) {"
            "  return {"
            "    canTransition: c.canTransition(v[0], v[1]),"
            "    fromTerminal: c.isTerminal(v[0]),"
            "    toTerminal: c.isTerminal(v[1])"
            "  };"
            "});"
            "process.stdout.write(JSON.stringify(out));"
        )
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [node, "-e", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_every_edge_in_the_graph_agrees_between_python_and_the_browser(self):
        """The complete cartesian product, not a sample: 13 x 13 = 169 edges."""
        vectors = [[a, b] for a in sorted(STATE_IDS) for b in sorted(STATE_IDS)]
        answers = self._browser_answers(vectors)
        self.assertEqual(len(answers), len(vectors))
        disagreements = []
        for (a, b), answer in zip(vectors, answers):
            if answer["canTransition"] != can_transition(a, b):
                disagreements.append(
                    f"{a} -> {b}: python={can_transition(a, b)} "
                    f"browser={answer['canTransition']}"
                )
            if answer["fromTerminal"] != is_terminal(a):
                disagreements.append(f"terminal({a}) disagrees")
        self.assertEqual(disagreements, [], "\n".join(disagreements))

    def test_the_repair_sequence_agrees_end_to_end(self):
        """The exact defect #612 opened with, executed on both surfaces.

        running -> verifying -> running (bounded repair) -> verifying ->
        completed. Python permitted the repair edge while the browser's
        hand-maintained graph rejected it, so a legitimate repair continued in
        the engine while the UI showed a stale or dead-end state.
        """
        walk = [
            ("running", "verifying"),
            ("verifying", "running"),
            ("running", "verifying"),
            ("verifying", "completed"),
        ]
        answers = self._browser_answers([list(edge) for edge in walk])
        for (a, b), answer in zip(walk, answers):
            with self.subTest(edge=f"{a}->{b}"):
                self.assertTrue(can_transition(a, b), f"python rejects {a}->{b}")
                self.assertTrue(answer["canTransition"], f"browser rejects {a}->{b}")

        # And the terminal end of it must not be walkable backwards.
        [back] = self._browser_answers([["completed", "running"]])
        self.assertFalse(can_transition("completed", "running"))
        self.assertFalse(back["canTransition"])


if __name__ == "__main__":
    unittest.main()
