"""Cancellation as a durable, observable lifecycle, not a boolean (#380).

`RunState.CANCEL_REQUESTED` (#295/#379) is the canonical, cross-surface truth
that a run is stopping — every surface already speaks that one word. What it
does not capture is *how far the stop has actually gotten*: whether anything
has even noticed yet, whether OPai is waiting for in-flight work to exit on
its own, or whether it had to be killed outright. Collapsing all of that into
one boolean is exactly how "cancel requested" stops meaning "cancelled" — a
provider call or a child process can keep running for an arbitrary time after
the flag flips, with nothing recording that gap or how long it was.

``CancelPhase`` is a closed, ordered refinement of ``CANCEL_REQUESTED`` — the
same relationship ``RuntimePhase`` already has to the canonical run state
(see ``run_state.canonical_for_runtime_phase``). It is never a second,
competing lifecycle:

    requested -> acknowledged -> draining -> force_terminating -> terminated

``requested``
    The stop was asked for (a user click, Ctrl+C, an API call). Nothing has
    reacted yet.
``acknowledged``
    Something that was about to do more work saw the request and refused to
    start anything new. If nothing was in flight, this is also where the
    tracker can jump straight to ``terminated`` — there is nothing to drain.
``draining``
    Work already in flight is being asked to stop itself (the process's own
    ``terminate()``), within a bounded grace window.
``force_terminating``
    The grace window expired; the whole process tree is being killed outright
    (:func:`opaihub.process_tree.terminate_tree`).
``terminated``
    Confirmed stopped. Immutable, like every terminal state in this codebase.

Durable and replayable on purpose: ``opaihub.run_journal`` (#517) already
proved out append-only, monotonically-sequenced, crash-safe evidence for
exactly this shape of problem. Backing the tracker with it — rather than a
bare in-memory flag — is what turns "cancellation acknowledgement latency"
and "hard-stop latency" (#380's own named metrics) into numbers computed from
recorded timestamps instead of estimates, and lets a second, independent
caller (after #517's workflow-run wiring) prove the journal is genuinely
general-purpose rather than single-use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from . import run_journal
from .state import state_dir


class CancelPhase(str, Enum):
    REQUESTED = "requested"
    ACKNOWLEDGED = "acknowledged"
    DRAINING = "draining"
    FORCE_TERMINATING = "force_terminating"
    TERMINATED = "terminated"


_ORDER: tuple[CancelPhase, ...] = (
    CancelPhase.REQUESTED,
    CancelPhase.ACKNOWLEDGED,
    CancelPhase.DRAINING,
    CancelPhase.FORCE_TERMINATING,
    CancelPhase.TERMINATED,
)
# Position in the lifecycle, for the one thing every caller needs to know:
# is this phase at-or-past that one. Forward moves (including skipping
# straight to a later phase — acknowledged -> terminated when nothing was in
# flight to drain) are always allowed; nothing in this module ever needs to
# refuse one, so there is no separate legality table to keep in sync with it.
_INDEX = {phase: index for index, phase in enumerate(_ORDER)}


def is_terminal(phase: CancelPhase) -> bool:
    return phase is CancelPhase.TERMINATED


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_projection() -> dict[str, Any]:
    return {"phase": None, "history": []}


def _reduce(projection: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": event["phase"],
        "history": [
            *projection["history"],
            {
                "phase": event["phase"],
                "at": event["at"],
                "reason_code": event["reason_code"],
            },
        ],
    }


def _validate(event: dict[str, Any]) -> bool:
    try:
        CancelPhase(str(event.get("phase") or ""))
    except ValueError:
        return False
    return bool(event.get("at")) and isinstance(event.get("reason_code"), str)


def _safe_scope_id(value: str) -> str:
    clean = "".join(ch for ch in str(value) if ch.isalnum() or ch in "-_.")[:128]
    if not clean or clean != str(value):
        raise ValueError(f"unsafe cancellation scope id: {value!r}")
    return clean


def cancellation_journal_path(project_root: Path, scope_id: str) -> Path:
    return (
        state_dir(project_root)
        / "health"
        / "cancellation"
        / f"{_safe_scope_id(scope_id)}.journal.jsonl"
    )


@dataclass(frozen=True)
class CancelMetrics:
    """The latencies #380 names explicitly, computed from durable evidence."""

    requested_at: str | None
    acknowledged_at: str | None
    terminated_at: str | None
    forced: bool
    acknowledgement_latency_seconds: float | None
    hard_stop_latency_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_at": self.requested_at,
            "acknowledged_at": self.acknowledged_at,
            "terminated_at": self.terminated_at,
            "forced": self.forced,
            "acknowledgement_latency_seconds": self.acknowledgement_latency_seconds,
            "hard_stop_latency_seconds": self.hard_stop_latency_seconds,
        }


def _parse(at: str | None) -> datetime | None:
    if not at:
        return None
    try:
        return datetime.fromisoformat(at)
    except ValueError:
        return None


def _elapsed(start: str | None, end: str | None) -> float | None:
    parsed_start, parsed_end = _parse(start), _parse(end)
    if parsed_start is None or parsed_end is None:
        return None
    return max(0.0, (parsed_end - parsed_start).total_seconds())


class CancellationTracker:
    """Durable phase evidence for one cancellable scope (a run, a turn, ...).

    Every method is idempotent past the point it targets: two callers racing
    to cancel the same scope (a GUI Stop click and a CLI Ctrl+C, or a user
    mashing Stop twice) must never raise, duplicate journal evidence, or move
    a phase backward — they converge on whichever phase is already reached.
    """

    def __init__(
        self,
        project_root: Path,
        scope_id: str,
        *,
        journal_run_id: str = "",
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.scope_id = _safe_scope_id(scope_id)
        self._path = cancellation_journal_path(self.project_root, self.scope_id)
        # #818: which run in the canonical journal this scope is cancelling, if
        # any. Passed explicitly rather than derived from `scope_id`, because
        # scope ids are namespaced per caller ("background-<run>",
        # "account-<operation>", "aci-<uuid>") and only some of them name a
        # journalled run at all. Guessing would either write events under a
        # foreign key that does not exist or, worse, under one that does and
        # belongs to something else.
        self.journal_run_id = str(journal_run_id or "").strip()

    def phase(self) -> CancelPhase | None:
        projection = run_journal.load(
            self._path, reduce=_reduce, empty=_empty_projection, validate=_validate
        ).projection
        raw = projection.get("phase")
        return CancelPhase(raw) if raw else None

    def history(self) -> tuple[dict[str, Any], ...]:
        projection = run_journal.load(
            self._path, reduce=_reduce, empty=_empty_projection, validate=_validate
        ).projection
        return tuple(projection.get("history") or ())

    def _advance(self, target: CancelPhase, *, reason_code: str) -> CancelPhase:
        """Move to ``target`` iff it is still ahead of whatever is durably
        current — decided and written under one lock (:func:`run_journal.append_if`),
        so two callers racing to advance the same scope can never both write:
        exactly the "simultaneous GUI/CLI cancellation" case #380 names.
        """

        def decide(projection: dict[str, Any]) -> dict[str, Any] | None:
            raw = projection.get("phase")
            current = CancelPhase(raw) if raw else None
            if current is not None and _INDEX[target] <= _INDEX[current]:
                return None  # already here or past it: idempotent no-op
            return {"phase": target.value, "at": _now_iso(), "reason_code": reason_code}

        result = run_journal.append_if(
            self._path,
            decide,
            reduce=_reduce,
            empty=_empty_projection,
            validate=_validate,
        )
        if result is None:
            current = self.phase()
            if current is None:
                # Unreachable: `decide` only declines when the projection
                # already had a phase. Guarded explicitly, not with
                # `assert` (stripped under `-O`), so a violation surfaces
                # as this message rather than a bare AttributeError below.
                raise RuntimeError("cancellation phase decline with no recorded phase")
            return current
        _record, projection = result
        phase = CancelPhase(projection["phase"])
        self._mirror(phase, reason_code)
        return phase

    def _mirror(self, phase: CancelPhase, reason_code: str) -> None:
        """Copy an accepted phase into the canonical journal. Never raises.

        Deliberately outside ``append_if``'s lock. The decision is already made
        and durable by the time this runs, so holding the cancellation lock
        across a second store's write would add latency to a stop -- the one
        operation where latency is the whole complaint (#380 measures it).
        """

        if not self.journal_run_id:
            return
        try:
            from . import journal_runtime

            journal_runtime.record_cancellation_phase(
                self.project_root,
                run_id=self.journal_run_id,
                phase=phase.value,
                reason_code=reason_code,
                now=_now_iso(),
            )
        except Exception:  # noqa: BLE001 - a mirror never fails a real stop
            return

    def request(self, *, reason_code: str = "user_requested") -> CancelPhase:
        """Record that a stop was asked for. Safe to call more than once."""
        current = self.phase()
        if current is not None:
            return current
        return self._advance(CancelPhase.REQUESTED, reason_code=reason_code)

    def acknowledge(self, *, reason_code: str = "observed") -> CancelPhase:
        """Something about to do more work saw the request and stood down."""
        if self.phase() is None:
            self.request(reason_code=reason_code)
        return self._advance(CancelPhase.ACKNOWLEDGED, reason_code=reason_code)

    def begin_draining(
        self, *, reason_code: str = "stopping_in_flight_work"
    ) -> CancelPhase:
        if self.phase() is None:
            self.acknowledge(reason_code=reason_code)
        return self._advance(CancelPhase.DRAINING, reason_code=reason_code)

    def force_terminate(
        self, *, reason_code: str = "grace_window_expired"
    ) -> CancelPhase:
        if self.phase() is None:
            self.begin_draining(reason_code=reason_code)
        return self._advance(CancelPhase.FORCE_TERMINATING, reason_code=reason_code)

    def mark_terminated(self, *, reason_code: str = "confirmed_stopped") -> CancelPhase:
        if self.phase() is None:
            self.acknowledge(reason_code=reason_code)
        return self._advance(CancelPhase.TERMINATED, reason_code=reason_code)

    def metrics(self) -> CancelMetrics:
        history = self.history()
        at_for: dict[CancelPhase, str] = {}
        forced = False
        for entry in history:
            try:
                phase = CancelPhase(entry["phase"])
            except ValueError:
                continue
            at_for.setdefault(phase, entry["at"])
            if phase is CancelPhase.FORCE_TERMINATING:
                forced = True
        requested_at = at_for.get(CancelPhase.REQUESTED)
        acknowledged_at = at_for.get(CancelPhase.ACKNOWLEDGED)
        terminated_at = at_for.get(CancelPhase.TERMINATED)
        return CancelMetrics(
            requested_at=requested_at,
            acknowledged_at=acknowledged_at,
            terminated_at=terminated_at,
            forced=forced,
            acknowledgement_latency_seconds=_elapsed(requested_at, acknowledged_at),
            hard_stop_latency_seconds=_elapsed(requested_at, terminated_at),
        )

    def evidence(self) -> dict[str, Any]:
        """Return the bounded, replay-derived teardown evidence for this scope."""

        phase = self.phase()
        return {
            "scope_id": self.scope_id,
            "phase": phase.value if phase is not None else None,
            "history": list(self.history()),
            "metrics": self.metrics().to_dict(),
        }
