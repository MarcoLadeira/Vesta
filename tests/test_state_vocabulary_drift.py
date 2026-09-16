"""A tripwire against silent growth of unreconciled state vocabularies (#379).

#379's own acceptance criteria include: "Static/parity tests prevent new
surface-specific run-state vocabularies." Collapsing every one of them into
`RunState` is multi-week, cross-cutting migration work — out of scope for one
pass. What is achievable now, and valuable on its own, is stopping the known
problem from silently getting *worse* while that migration proceeds.

A 2026-07-19 audit named 7+ parallel vocabularies. Re-measuring on 2026-08-03
found three of them had already grown without anyone deciding to: `CompletionState`
8 -> 13 members, `ledger.OUTCOME_CATEGORIES` 4 -> 5, `activity.TYPES` 24 -> 27.
Nobody chose that growth; it just happened, one PR at a time, because nothing
noticed. This file is the notice.

Two categories, treated differently:

- **Reconciled** vocabularies (`RuntimePhase`, `CancelPhase`, the GUI store's
  `message-state.js`) already have their own `canonical_for_*()`-style total
  mapping plus a dedicated parity test (`test_runtime_phase_parity.py`,
  `test_cancellation_lifecycle.py`, `test_run_state_parity.py`). They may grow
  freely — a new member there *must* already declare what it means canonically,
  or its own parity test fails. They are not re-tested here.
- **Unreconciled** vocabularies below have no such mapping yet. Each is
  snapshotted to its exact current member set. A failure here means one of
  two things happened, and the fix depends on which: (a) the vocabulary
  genuinely needed a new member — update the snapshot below, deliberately,
  in the same change that added it; or (b) the new member is really a
  `RunState` concept wearing a local name — prefer mapping it to `RunState`
  instead of growing the local vocabulary further.

Adding a brand new vocabulary (a class or constant collection that isn't one
of the ones below and isn't reconciled) won't be caught automatically — that
would need fragile whole-repo heuristics this file deliberately avoids. The
enforceable contract is narrower and honest: *the vocabularies we already
know about cannot silently grow.* Register a genuinely new one here when you
add it, the same way this file was populated.
"""

from __future__ import annotations

from vestahub.checkpoints import COMPLETION_STATES
from vestahub.completion import CompletionState, CompletionVerdict
from vestahub.execution_guard import GuardOutcome
from vestahub.ledger import OUTCOME_CATEGORIES
from vestahub.provider_protocol import CapabilityStatus
from vestahub.session_registry import CANCELLED, DONE, FAILED, RUNNING, SUPERSEDED
from vestahub.verification_execution import CheckStatus, VerificationVerdict

from vesta.activity import CHANNELS, STATUSES, TYPES
from vesta.gui_recents import _PLAN_STATUSES, _THREAD_STATUSES


def _values(enum_cls: type) -> frozenset[str]:
    return frozenset(member.value for member in enum_cls)


def test_completion_state_has_not_grown_without_a_deliberate_update() -> None:
    assert _values(CompletionState) == frozenset(
        {
            "completed",
            "partial",
            "blocked",
            "timeout",
            "needs_attention",
            "awaiting_input",
            "cancelled",
            "needs_user_input",
            "needs_consent",
            "retryable_provider_error",
            "provider_blocked",
            "stuck_no_progress",
            "failed",
        }
    )


def test_completion_verdict_has_not_grown_without_a_deliberate_update() -> None:
    # Already 1:1 with RunState's terminals (run_state.run_state_for_verdict
    # maps it directly) -- the cleanest case, but still a separate class.
    assert _values(CompletionVerdict) == frozenset(
        {
            "completed",
            "partial",
            "blocked",
            "failed",
            "cancelled",
            "timeout",
            "needs_attention",
        }
    )


def test_checkpoint_completion_states_have_not_grown_without_a_deliberate_update() -> (
    None
):
    assert frozenset(COMPLETION_STATES) == frozenset(
        {
            "answered",
            "read_only",
            "blocked",
            "cancelled",
            "cancelled_before_edit",
            "partial",
            "timeout",
            "failed",
            "interrupted",
        }
    )


def test_ledger_outcome_categories_have_not_grown_without_a_deliberate_update() -> None:
    assert frozenset(OUTCOME_CATEGORIES) == frozenset(
        {"completed", "partial", "failed", "blocked", "cancelled"}
    )


def test_session_registry_states_have_not_grown_without_a_deliberate_update() -> None:
    assert frozenset({RUNNING, SUPERSEDED, DONE, FAILED, CANCELLED}) == frozenset(
        {"running", "superseded", "done", "failed", "cancelled"}
    )


def test_guard_outcome_has_not_grown_without_a_deliberate_update() -> None:
    assert _values(GuardOutcome) == frozenset(
        {"allow", "cancel", "needs_consent", "blocked"}
    )


def test_capability_status_has_not_grown_without_a_deliberate_update() -> None:
    assert _values(CapabilityStatus) == frozenset(
        {"supported", "partial", "unsupported", "unknown"}
    )


def test_check_status_has_not_grown_without_a_deliberate_update() -> None:
    assert _values(CheckStatus) == frozenset(
        {
            "passed",
            "failed",
            "timeout",
            "cancelled",
            "unavailable",
            "blocked",
            "skipped",
            "waived",
            "artifact_lost",
        }
    )


def test_verification_verdict_has_not_grown_without_a_deliberate_update() -> None:
    assert _values(VerificationVerdict) == frozenset(
        {
            "verified",
            "partially_verified",
            "blocked",
            "failed",
            "cancelled",
            "timeout",
            "unverified",
        }
    )


def test_activity_schema_v2_has_not_grown_without_a_deliberate_update() -> None:
    assert frozenset(STATUSES) == frozenset(
        {"pending", "running", "success", "warning", "error", "cancelled"}
    )
    assert frozenset(CHANNELS) == frozenset({"feed", "status"})
    assert frozenset(TYPES) == frozenset(
        {
            "request_prepare",
            "context_read",
            "model_selected",
            "provider_checking",
            "provider_authenticated",
            "provider_auth_failed",
            "provider_request",
            "request_sending",
            "waiting_first_token",
            "streaming",
            "tool_call",
            "file_read",
            "file_edit",
            "command_run",
            "command_complete",
            "ci_watch",
            "validation",
            "retry",
            "retrying",
            "verifying",
            "completion",
            "completion_verdict",
            "stopped",
            "completed",
            "failed",
            "cancelled",
            "error",
        }
    )


def test_gui_recents_thread_and_plan_statuses_have_not_grown_without_a_deliberate_update() -> (
    None
):
    assert frozenset(_THREAD_STATUSES) == frozenset(
        {
            "complete",
            "partial",
            "blocked",
            "timeout",
            "pending",
            "failed",
            "cancelled",
            "needs_attention",
            "interrupted",
        }
    )
    assert frozenset(_PLAN_STATUSES) == frozenset(
        {"pending", "in_progress", "completed", "blocked"}
    )
