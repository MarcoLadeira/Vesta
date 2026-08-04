"""Every run status an execution path emits must be canonically interpretable (#379).

#379's core problem is *adoption*, not the model: a canonical lifecycle exists,
but legacy execution paths still construct raw ``{"status": "..."}`` result
dicts with string literals, and nothing checked that the canonical model could
interpret them. An unmapped status does not fail loudly — it degrades to
``needs_attention``/``incompatible``, which is how "a task appears stopped in
one surface and active in another" happens.

A 2026-08-04 inventory of the execution paths found **22 of 32** emitted
statuses unknown to ``LEGACY_STATUS_MAP``. The most consequential was
``confirmation_required`` — what ``ask.py``/``app_state.py`` actually emit when
a free-tier call is waiting for the user's OK. Its near-twin
``needs_confirmation`` *was* mapped to the non-terminal ``awaiting_input``,
while ``confirmation_required`` silently degraded to ``needs_attention``: a run
genuinely waiting for the user was classified as incompatible/degraded instead
of resumable.

This module is the tripwire for that class of defect. It complements
``test_state_vocabulary_drift.py``, which pins *enum/constant* vocabularies;
this one covers the raw status **string literals** those execution paths emit,
which no enum guards.

Scope is deliberately narrow and honest: the run-result execution paths whose
statuses reach GUI/CLI surfaces. Domain-specific outcome vocabularies that are
not run-lifecycle states (build-loop ``no_edits``/``dry_run``, proxy
``fail_open``, account ``signed_in``, routing ``free_first``) are listed as
explicitly out of scope rather than force-mapped — the report's rule is that
unknown conditions stay unknown rather than being silently coerced.
"""

from __future__ import annotations

import re
from pathlib import Path

from opaihub.generated_lifecycle import LEGACY_STATUS_MAP, STATE_IDS

ROOT = Path(__file__).resolve().parents[1]

# Execution paths that build run-result dicts consumed by GUI/CLI surfaces.
EXECUTION_PATHS = (
    "opaihub/ask.py",
    "opai/app_state.py",
    "opaihub/gui_pipeline.py",
    "opaihub/accounts.py",
)

# Statuses these paths emit that are deliberately NOT run-lifecycle states.
# Each is a domain outcome with its own meaning; mapping it to a lifecycle
# state would assert something the code does not mean. Keep this list short
# and justified — a new entry here is a claim that the status is not a run
# state, not a way to silence the test.
NOT_RUN_LIFECYCLE = frozenset(
    {
        "signed_in",  # accounts: an auth result, not a run outcome
        "exported",  # app_state: an export operation completed
        "free_first",  # app_state: a routing decision, not a terminal state
        "duplicate_request",  # gui_pipeline: dedup of an inflight submit
        "unknown",  # app_state: genuinely unknown — must stay unknown
    }
)

_STATUS_LITERAL = re.compile(r'"status":\s*"([a-z_]+)"')


def _emitted_statuses() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for relative in EXECUTION_PATHS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for match in _STATUS_LITERAL.finditer(text):
            found.setdefault(match.group(1), set()).add(relative)
    return found


def test_every_emitted_run_status_is_canonically_interpretable() -> None:
    known = set(LEGACY_STATUS_MAP) | set(STATE_IDS)
    unmapped = {
        status: sorted(files)
        for status, files in _emitted_statuses().items()
        if status not in known and status not in NOT_RUN_LIFECYCLE
    }
    assert not unmapped, (
        "These run statuses are emitted by execution paths but cannot be "
        "interpreted by the canonical lifecycle, so they degrade to "
        "needs_attention/incompatible instead of their real state:\n"
        + "\n".join(f"  {s!r} <- {', '.join(f)}" for s, f in sorted(unmapped.items()))
        + "\n\nFix by adding the status to opaihub/lifecycle_schema.json (then "
        "run scripts/generate_lifecycle.py), or — if it genuinely is not a run "
        "lifecycle state — by adding it to NOT_RUN_LIFECYCLE with a reason."
    )


def test_waiting_for_the_user_is_resumable_not_degraded() -> None:
    # The specific defect this module was written for: both spellings of
    # "waiting for the user's confirmation" must resolve to the same
    # non-terminal state. awaiting_input resumes the same work; needs_attention
    # is the degraded incompatible bucket.
    assert LEGACY_STATUS_MAP["confirmation_required"] == "awaiting_input"
    assert LEGACY_STATUS_MAP["needs_confirmation"] == "awaiting_input"


def test_known_run_statuses_resolve_to_their_real_meaning() -> None:
    # Regression pins for the 2026-08-04 additions, so a later schema edit
    # cannot quietly re-point them at a wrong state.
    assert LEGACY_STATUS_MAP["capability_mismatch"] == "blocked"
    assert LEGACY_STATUS_MAP["blocked_panic"] == "blocked"
    assert LEGACY_STATUS_MAP["account_not_connected"] == "blocked"
    assert LEGACY_STATUS_MAP["runner_error"] == "failed"
    assert LEGACY_STATUS_MAP["model_unavailable"] == "failed"
    assert LEGACY_STATUS_MAP["no_local_model"] == "failed"
    assert LEGACY_STATUS_MAP["timed_out"] == "timeout"


def test_dead_ends_are_not_dressed_up_as_questions() -> None:
    # run_state.AWAITING_INPUT_STATUSES is *derived* from LEGACY_STATUS_MAP, so
    # a careless mapping here silently makes a dead end claim to be resumable —
    # the exact dishonesty `needs_model` is documented to avoid. These statuses
    # mean "this run cannot continue as-is"; the remedy is a new run, so none of
    # them may report that the user's answer would resume this one.
    from opaihub.run_state import is_awaiting_input

    for status in (
        "capability_mismatch",
        "model_unavailable",
        "no_local_model",
        "runner_error",
        "account_not_connected",
        "blocked_panic",
        "timed_out",
    ):
        assert not is_awaiting_input(status), (
            f"{status!r} is mapped to a state that claims the user's answer "
            "resumes this run, but it is a dead end — pick blocked/failed."
        )
    # ...while the consent stop genuinely is resumable.
    assert is_awaiting_input("confirmation_required")


def test_no_mapped_status_points_at_an_unknown_state() -> None:
    # A mapping is only useful if its target is a real lifecycle state.
    invalid = {
        status: state
        for status, state in LEGACY_STATUS_MAP.items()
        if state not in set(STATE_IDS)
    }
    assert not invalid, f"legacy statuses mapped to non-existent states: {invalid}"


def test_out_of_scope_list_stays_accurate() -> None:
    # Guard the guard: if a status leaves the codebase, its NOT_RUN_LIFECYCLE
    # entry should go too, so the exemption list cannot rot into a blanket
    # suppression of statuses nobody emits any more.
    emitted = set(_emitted_statuses())
    stale = sorted(NOT_RUN_LIFECYCLE - emitted)
    assert not stale, (
        f"NOT_RUN_LIFECYCLE lists statuses no execution path emits: {stale}. "
        "Remove them so the exemption list keeps meaning something."
    )
