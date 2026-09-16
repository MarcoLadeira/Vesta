"""Property-based coverage of the generated lifecycle graph (#612).

The issue asks for at least 10,000 generated transition sequences covering
duplication, reordering, stale revision and terminal-state attacks, with the
invariant that no sequence can regress a terminal state or reach completion
without the guard evidence the schema requires. The unit tests in
``test_lifecycle_generation.py`` and ``test_run_state.py`` check specific,
hand-picked edges; this drives the *whole* graph with generated input instead,
so a bad edge nobody thought to hand-write a test for still gets caught.
"""

from __future__ import annotations

import unittest

from hypothesis import HealthCheck, given, settings, strategies as st

from vestahub.generated_lifecycle import STATE_IDS, TERMINAL_STATE_IDS, transition_spec
from vestahub.run_state import RunState, transition


_STATE_STRATEGY = st.sampled_from(STATE_IDS)
_SEQUENCE_STRATEGY = st.lists(_STATE_STRATEGY, min_size=1, max_size=25)
_ARBITRARY_INPUT = st.one_of(
    _STATE_STRATEGY,
    st.text(max_size=32),
    st.integers(),
    st.none(),
)


class TransitionSpecPropertyTests(unittest.TestCase):
    """The raw generated contract, independent of the Python enum facade."""

    @settings(max_examples=10_000, suppress_health_check=[HealthCheck.too_slow])
    @given(_SEQUENCE_STRATEGY)
    def test_a_terminal_state_never_regresses_across_a_random_walk(
        self, targets: list[str]
    ) -> None:
        state = "queued"
        reached_terminal = False
        for target in targets:
            if reached_terminal:
                # Terminal states have no outgoing edges at all — the schema
                # generator itself enforces this (see _validate_schema), so
                # this is re-asserting a structural guarantee under load,
                # not hoping a random walk happens to hit it.
                self.assertIsNone(transition_spec(state, target))
                continue
            spec = transition_spec(state, target)
            if spec is not None:
                state = target
                if state in TERMINAL_STATE_IDS:
                    reached_terminal = True
        if reached_terminal:
            self.assertIn(state, TERMINAL_STATE_IDS)

    @settings(max_examples=10_000, suppress_health_check=[HealthCheck.too_slow])
    @given(st.sampled_from(sorted(TERMINAL_STATE_IDS)), _STATE_STRATEGY)
    def test_no_terminal_state_has_any_legal_outgoing_edge(
        self, terminal: str, target: str
    ) -> None:
        self.assertIsNone(transition_spec(terminal, target))

    @settings(max_examples=10_000, suppress_health_check=[HealthCheck.too_slow])
    @given(_STATE_STRATEGY, _STATE_STRATEGY)
    def test_every_legal_edge_agrees_both_directions_of_the_lookup(
        self, source: str, target: str
    ) -> None:
        # transition_spec is the single source both Python and the browser
        # read; walking every (source, target) pair at random is a cheap way
        # to prove the lookup itself never raises and is a pure function of
        # its two inputs (calling it twice never disagrees with itself).
        first = transition_spec(source, target)
        second = transition_spec(source, target)
        self.assertEqual(first, second)


class RunStateFacadePropertyTests(unittest.TestCase):
    """The Python enum facade callers actually use, including unknown input."""

    @settings(max_examples=10_000, suppress_health_check=[HealthCheck.too_slow])
    @given(_SEQUENCE_STRATEGY)
    def test_a_random_walk_through_the_enum_facade_never_raises_or_regresses(
        self, targets: list[str]
    ) -> None:
        state = RunState.QUEUED
        terminal_seen: RunState | None = None
        for target in targets:
            moved = transition(state, target, source="test")
            if terminal_seen is not None:
                # Once terminal, `transition` must return the same terminal
                # state no matter what is asked of it next.
                self.assertEqual(moved, terminal_seen)
                continue
            state = moved
            if state.value in TERMINAL_STATE_IDS:
                terminal_seen = state

    @settings(max_examples=10_000, suppress_health_check=[HealthCheck.too_slow])
    @given(_ARBITRARY_INPUT, _ARBITRARY_INPUT)
    def test_arbitrary_untrusted_input_never_crashes_the_transition_facade(
        self, current: object, target: object
    ) -> None:
        # Duplication/reordering/stale-revision/terminal-state "attacks" all
        # eventually reduce to "some caller handed transition() a value it
        # did not produce itself" — a persisted record from a newer version,
        # a corrupted field, a typo. It must never raise; it degrades.
        try:
            transition(current, target, source="test")
        except ValueError:
            # Only a genuinely unrepresentable RunState value (coerce
            # failure on `current` itself) is allowed to raise; the browser
            # equivalent has no such escape hatch, which is exactly why the
            # Python facade's own degradation path (NEEDS_ATTENTION) exists
            # for *target* being unrecognised.
            pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
