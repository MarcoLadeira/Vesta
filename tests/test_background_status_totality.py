"""A background run must never crash its worker thread on a legitimate state (#612).

The report's own worked example is a status-vocabulary disagreement between
Python and the browser. Measuring the equivalent question for the
background-run compatibility layer found a sharper version of the same class
of bug: ``_LEGACY_STATUS_FOR_RUN_STATE`` — canonical ``RunState`` to the
pre-#379 compatibility word — was missing ``VERIFYING`` and
``NEEDS_ATTENTION``. ``NEEDS_ATTENTION`` is reachable: any workflow executor
that reports it as its completion verdict (a real, already-wired terminal
state — see ``vestahub/completion.py``) hits

    changes.pop("legacy_status", _LEGACY_STATUS_FOR_RUN_STATE[next_state])

which evaluates the default eagerly and raises ``KeyError`` — *outside*
``_execute``'s own exception guard, which wraps only the executor call. The
worker thread dies uncaught, and the run is left recorded as ``running``
forever: no error surfaced, no retry possible, nothing to click.

Reproduced empirically before any fix landed here:

    >>> from vestahub.background_runs import BackgroundRunner, enqueue_automation, load_run
    >>> run = enqueue_automation(root, "bug_fix", "do a thing")
    >>> executor = lambda *a: {"run_state": "needs_attention", "status": "needs_attention"}
    >>> BackgroundRunner(root, executor=executor).start(run.run_id).join(timeout=10)
    KeyError: <RunState.NEEDS_ATTENTION: 'needs_attention'>

The tests below pin two independent halves of the fix: the map is now total
(so this never happens with a *known* state), and the lookup that used to
crash is now structurally incapable of it (so a *future* state added without
updating the map degrades instead of taking down the worker thread).

Follow-up (#612 AC1): completing the map by hand fixed the instance but not
the *class* — the map was still hand-maintained beside the enum it had to
track. It is now generated from ``vestahub/lifecycle_schema.json``, and the
generator refuses to emit an incomplete or terminality-inconsistent
projection at all, so the same mistake is a build failure behind CI's
``--check`` drift gate rather than a runtime crash. ``GeneratedProjectionTests``
below pins that contract.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestahub.background_runs import (
    _LEGACY_STATUS_FOR_RUN_STATE,
    _legacy_status_for,
    BackgroundRunner,
    enqueue_automation,
    load_run,
)
from vestahub.run_state import RunState


class TotalityTests(unittest.TestCase):
    """The map must cover every canonical state, not just the ones anyone
    remembered to add."""

    def test_every_run_state_has_a_legacy_word(self) -> None:
        missing = [
            state for state in RunState if state not in _LEGACY_STATUS_FOR_RUN_STATE
        ]
        self.assertEqual(missing, [], f"no legacy mapping for: {missing}")

    def test_the_fallback_only_ever_produces_a_closed_vocabulary_word(self) -> None:
        # _legacy_status_for's `.get(..., "blocked")` fallback exists for a
        # *future* state added without updating the map above. Confirm it
        # degrades to a real, already-understood legacy word rather than
        # inventing a new one older consumers cannot interpret.
        from vestahub.background_runs import RUN_STATUSES

        for state in RunState:
            self.assertIn(_legacy_status_for(state), RUN_STATUSES, state)


class CrashRegressionTests(unittest.TestCase):
    """The reproduction: a real executor payload, through the public API."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_needs_attention_verdict_finishes_the_run_instead_of_crashing(
        self,
    ) -> None:
        run = enqueue_automation(self.root, "bug_fix", "do a thing")

        def executor(project_root, run, cancel_event):
            # A real shape: a workflow that could not verify its own result
            # and says so, rather than claiming success. NEEDS_ATTENTION is a
            # first-class terminal state elsewhere in the system.
            return {"run_state": "needs_attention", "status": "needs_attention"}

        runner = BackgroundRunner(self.root, executor=executor)
        thread = runner.start(run.run_id)
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive(), "the worker thread must not hang")
        final = load_run(self.root, run.run_id)
        self.assertEqual(
            final.run_state,
            RunState.NEEDS_ATTENTION.value,
            "the run must reach the state the executor actually reported, "
            "not be stranded in 'running' by a crashed worker",
        )
        self.assertEqual(final.status, "blocked")

    def test_a_gap_in_the_map_no_longer_crashes_the_lookup(self) -> None:
        # The structural half of the fix, isolated from the data half: even
        # with an *incomplete* map — standing in for a future RunState added
        # without updating the table above — `_legacy_status_for` must
        # degrade to a safe word instead of raising. This is what makes the
        # crash class impossible going forward, not just this one instance
        # of it. Has teeth against reverting the `.get(...)` accessor alone:
        # a bare `dict[state]` subscript raises KeyError on the trimmed map
        # below, exactly as the unpatched dict did before this PR (see the
        # module docstring for the reproduced traceback).
        import unittest.mock as mock

        trimmed = {
            state: word
            for state, word in _LEGACY_STATUS_FOR_RUN_STATE.items()
            if state is not RunState.NEEDS_ATTENTION
        }
        with mock.patch(
            "vestahub.background_runs._LEGACY_STATUS_FOR_RUN_STATE", trimmed
        ):
            result = _legacy_status_for(RunState.NEEDS_ATTENTION)
        self.assertEqual(result, "blocked")


class GeneratedProjectionTests(unittest.TestCase):
    """#612 AC1: this projection is generated, not hand-maintained.

    The runtime tables must *be* the generated ones — not a copy that happens
    to agree today. Rebinding them by hand is what allowed the drift these
    tests exist to prevent.
    """

    def test_runtime_tables_are_the_generated_ones(self) -> None:
        from vestahub import background_runs
        from vestahub.generated_lifecycle import (
            BACKGROUND_REASON_FOR_STATUS,
            BACKGROUND_STATE_FOR_STATUS,
            BACKGROUND_STATUS_FOR_STATE,
            BACKGROUND_STATUSES,
            BACKGROUND_TERMINAL_STATUSES,
        )

        self.assertEqual(background_runs.RUN_STATUSES, set(BACKGROUND_STATUSES))
        self.assertEqual(
            background_runs.TERMINAL_STATUSES, set(BACKGROUND_TERMINAL_STATUSES)
        )
        self.assertEqual(
            {state.value: word for state, word in _LEGACY_STATUS_FOR_RUN_STATE.items()},
            dict(BACKGROUND_STATUS_FOR_STATE),
        )
        self.assertEqual(
            {
                word: state.value
                for word, state in background_runs._RUN_STATE_FOR_LEGACY_STATUS.items()
            },
            dict(BACKGROUND_STATE_FOR_STATUS),
        )
        self.assertEqual(
            background_runs._DEFAULT_REASON_FOR_LEGACY_STATUS,
            dict(BACKGROUND_REASON_FOR_STATUS),
        )

    def test_a_terminal_state_never_projects_to_a_live_word_or_vice_versa(self) -> None:
        """The invariant that caught a real pre-existing bug.

        ``awaiting_input`` (canonically non-terminal — the run resumes when
        the user answers) mapped to ``"blocked"``, which the background
        vocabulary treats as terminal: a live, resumable run reported to
        pre-#379 readers as ended. Latent, because background runs convert
        AWAITING_INPUT to canonical BLOCKED themselves before persisting —
        but a latent lie is still a lie, and the generator now refuses it.
        """
        from vestahub.background_runs import TERMINAL_STATUSES
        from vestahub.run_state import TERMINAL_STATES

        for state, word in _LEGACY_STATUS_FOR_RUN_STATE.items():
            with self.subTest(state=state.value, word=word):
                self.assertEqual(
                    state in TERMINAL_STATES,
                    word in TERMINAL_STATUSES,
                    f"{state.value} and its projection {word!r} disagree "
                    "about whether the run has ended",
                )

    def test_awaiting_input_projects_to_a_live_word(self) -> None:
        # The specific instance the invariant above caught, pinned by name so
        # a future edit back to "blocked" fails loudly rather than silently.
        self.assertEqual(
            _LEGACY_STATUS_FOR_RUN_STATE[RunState.AWAITING_INPUT], "running"
        )


if __name__ == "__main__":
    unittest.main()
