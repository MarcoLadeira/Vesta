"""The GUI store speaks the canonical run-state language (#379, slice 2).

opai/assets/web/message-state.js is the GUI's run-state machine. It may refine
the active phases (authenticating/sending/streaming/...), but it must never
invent a *terminal* the backend doesn't know, and every state it carries must
map back to a canonical run state. This guard reads the JS source and asserts
that parity, so the two can't silently drift apart.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from unittest import mock

from _helpers import FakeLocalRunner, make_repo

from opaihub.run_state import TERMINAL_STATES, RunState, canonical_for, is_terminal

_MESSAGE_STATE_JS = (
    Path(__file__).resolve().parents[1]
    / "opai"
    / "assets"
    / "web"
    / "message-state.js"
)


def _js_block(name: str) -> str:
    text = _MESSAGE_STATE_JS.read_text(encoding="utf-8")
    match = re.search(rf"var {name} = \{{(.*?)\}};", text, re.DOTALL)
    assert match, f"message-state.js must define {name}"
    return match.group(1)


def _js_keys(name: str) -> set[str]:
    return set(re.findall(r"(\w+):", _js_block(name)))


def test_js_terminal_states_match_the_canonical_terminals_exactly() -> None:
    assert _js_keys("TERMINAL") == {state.value for state in TERMINAL_STATES}


def test_js_verdict_state_map_covers_exactly_the_canonical_terminals() -> None:
    assert _js_keys("VERDICT_STATE") == {state.value for state in TERMINAL_STATES}


def test_every_js_store_state_maps_to_a_canonical_run_state() -> None:
    # ALLOWED's keys are the full state vocabulary of the GUI store. Each must
    # resolve to a canonical run state (directly or as a presentation refinement)
    # — the store never invents a lifecycle state the backend doesn't own.
    js_states = _js_keys("ALLOWED")
    assert js_states, "message-state.js must define an ALLOWED transition table"
    for state in js_states:
        resolved = canonical_for(state)  # raises if the state is unknown
        assert isinstance(resolved, RunState)


def test_js_carries_no_terminal_outside_the_canonical_set() -> None:
    # A JS terminal the backend can't produce would be an untrackable dead-end.
    js_terminals = _js_keys("TERMINAL")
    for terminal in js_terminals:
        assert RunState(terminal) in TERMINAL_STATES


def test_pipeline_emits_the_canonical_run_state_alongside_the_verdict() -> None:
    # #379 slice 2: the engine emits the canonical terminal run state, so a
    # surface reads one lifecycle field instead of inferring it from a status.
    selected = FakeLocalRunner(model="q", answer="The router picks a tier.")

    def run_ask(root, task, **kwargs):
        return {"status": "answered_locally", "answer": "The router picks a tier."}

    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp))
        with (
            mock.patch("opaihub.local_runner.runner_for_model", return_value=selected),
            mock.patch("opaihub.ask.run_ask", side_effect=run_ask),
        ):
            from opaihub.gui_pipeline import handle_gui_message

            result = handle_gui_message(
                root, "summarize the router", model_id="ollama:q", mode="ask"
            )

    assert result["run_state"] == result["completion_verdict"]["verdict"]
    assert is_terminal(result["run_state"])
