"""The GUI store speaks the canonical run-state language (#379, slice 2).

vesta/assets/web/message-state.js is the GUI's run-state machine. It may refine
the active phases (authenticating/sending/streaming/...), but it must never
invent a *terminal* the backend doesn't know, and every state it carries must
map back to a canonical run state. This guard reads the JS source and asserts
that parity, so the two can't silently drift apart.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from tests._helpers import FakeLocalRunner, make_repo

from vestahub.run_state import TERMINAL_STATES, RunState, canonical_for, is_terminal

_MESSAGE_STATE_JS = (
    Path(__file__).resolve().parents[1] / "vesta" / "assets" / "web" / "message-state.js"
)
_GENERATED_LIFECYCLE_JS = _MESSAGE_STATE_JS.with_name("generated-lifecycle.js")


def _browser_eval(expression: str, *arguments: str):
    script = f"""
require(process.argv[1]);
const reducer = require(process.argv[2]);
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        [
            "node",
            "-e",
            script,
            str(_GENERATED_LIFECYCLE_JS),
            str(_MESSAGE_STATE_JS),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _browser_reduce(current: str, target: str) -> str:
    return str(
        _browser_eval(
            'reducer.transition({requestId: "r1", status: process.argv[3]}, process.argv[4]).status',
            current,
            target,
        )
    )


def test_js_terminal_states_match_the_canonical_terminals_exactly() -> None:
    browser = set(_browser_eval("reducer.lifecycle.terminalStateIds"))
    assert browser == {state.value for state in TERMINAL_STATES}


def test_js_verdict_state_map_covers_exactly_the_canonical_terminals() -> None:
    for terminal in TERMINAL_STATES:
        projected = _browser_eval(
            "reducer.fromBackendStatus('failed', process.argv[3])", terminal.value
        )
        assert projected == terminal.value


def test_every_js_store_state_maps_to_a_canonical_run_state() -> None:
    # ALLOWED's keys are the full state vocabulary of the GUI store. Each must
    # resolve to a canonical run state (directly or as a presentation refinement)
    # — the store never invents a lifecycle state the backend doesn't own.
    browser_aliases = _browser_eval("reducer.lifecycle.legacyMappings.states")
    assert browser_aliases
    for state in browser_aliases:
        resolved = canonical_for(state)  # raises if the state is unknown
        assert isinstance(resolved, RunState)


def test_js_carries_no_terminal_outside_the_canonical_set() -> None:
    # A JS terminal the backend can't produce would be an untrackable dead-end.
    js_terminals = set(_browser_eval("reducer.lifecycle.terminalStateIds"))
    for terminal in js_terminals:
        assert RunState(terminal) in TERMINAL_STATES


def test_js_and_python_agree_on_which_statuses_are_awaiting_input() -> None:
    # #295 invariant 11 (cross-surface reconciliation). If the GUI's list drifts
    # from the engine's, the same turn is "waiting" on one surface and
    # "blocked" on the other — the exact class of bug this state was added to
    # kill, reintroduced one status at a time.
    from vestahub.run_state import AWAITING_INPUT_STATUSES

    for status in AWAITING_INPUT_STATUSES:
        assert (
            _browser_eval("reducer.fromBackendStatus(process.argv[3])", status)
            == RunState.AWAITING_INPUT.value
        )


def test_the_js_store_does_not_treat_waiting_as_an_ending() -> None:
    # canApply() refuses updates to a terminal message, so a waiting run listed
    # as terminal would be unaddressable by the answer that resumes it.
    assert not _browser_eval("reducer.lifecycle.isTerminal('awaiting_input')")
    assert _browser_eval(
        "reducer.canApply({requestId: 'r1', status: 'awaiting_input'}, 'r1')"
    )


def test_repair_vector_is_legal_in_python_and_browser() -> None:
    from vestahub.run_state import can_transition

    assert can_transition("verifying", "running")
    assert _browser_reduce("verifying", "running") == "running"


def test_pipeline_emits_the_canonical_run_state_alongside_the_verdict() -> None:
    # #379 slice 2: the engine emits the canonical terminal run state, so a
    # surface reads one lifecycle field instead of inferring it from a status.
    selected = FakeLocalRunner(model="q", answer="The router picks a tier.")

    def run_ask(root, task, **kwargs):
        return {"status": "answered_locally", "answer": "The router picks a tier."}

    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp))
        with (
            mock.patch("vestahub.local_runner.runner_for_model", return_value=selected),
            mock.patch("vestahub.ask.run_ask", side_effect=run_ask),
        ):
            from vestahub.gui_pipeline import handle_gui_message

            result = handle_gui_message(
                root, "summarize the router", model_id="ollama:q", mode="ask"
            )

    assert result["run_state"] == result["completion_verdict"]["verdict"]
    assert is_terminal(result["run_state"])
