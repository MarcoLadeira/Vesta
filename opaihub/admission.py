"""Admission: prove a request was received exactly once (#295 gate 3).

The epic's first lifecycle stage is explicit — *"prove that the request was
received exactly once and is eligible to enter the runtime"* — and requires
deduplicating repeated submissions using an admission/idempotency key. Gate 3
states the target as **zero duplicate active runs for the same key**.

Nothing did that. ``handle_gui_message`` minted a fresh random ``turn_id`` on
every call, so the single-flight registry (which keys on that id) could never
recognise two submissions as the same logical request. A double-click, a
renderer that replays a pending send after reconnecting, or a retry issued
before the first response arrived each started a **second full run**: two
provider calls, two charges, two sets of edits racing over the same files.

## What counts as the same request

The key is derived from what makes two submissions the same *intent*: the
repository, the exact task text, the model and the mode. Anything that would
change what OPai does changes the key.

## Why an active-run window, not a permanent ledger

Deduplicating forever would break normal use. Asking "run the tests" twice in a
row is a legitimate second request, not a duplicate, and #295 is explicit that
consistency must not come at the cost of limiting user interaction. So the rule
is scoped exactly as the gate words it: a submission is a duplicate only while
an **equivalent run is still active**. Once the first run reaches a terminal
state the same text is admitted normally.

That distinction is the whole design. It catches every mechanical duplicate
(they arrive while the first run is still going) and blocks no deliberate one.

## Never a dead end

A duplicate is not an error and is never dropped. Admission returns the
*existing* run's id so the calling surface can attach to the run already in
flight and show the user their answer arriving — which is what they wanted.
Rejecting with "already running" would be a second way to lose a message, and
gate 2 exists to make that impossible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: An admission decision.
ACCEPTED = "accepted"  # a new run may start
DUPLICATE = "duplicate"  # an equivalent run is already active; attach to it

#: Task text longer than this is hashed in full but only this much is compared
#: verbatim. Bounds the work done on a pathological paste without weakening the
#: key — the digest still covers every character.
_MAX_TASK_CHARS = 100_000


@dataclass(frozen=True)
class Admission:
    """The outcome of admitting one request.

    ``request_id`` is always the run the caller should watch: the newly created
    one when accepted, the already-running one when this was a duplicate. A
    caller that ignores ``decision`` and just uses ``request_id`` still behaves
    correctly, which is the point — the safe path is the default path.
    """

    decision: str
    key: str
    request_id: str
    existing_request_id: str | None = None

    @property
    def accepted(self) -> bool:
        return self.decision == ACCEPTED

    @property
    def duplicate(self) -> bool:
        return self.decision == DUPLICATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "admission_key": self.key,
            "request_id": self.request_id,
            "existing_request_id": self.existing_request_id,
        }


def _normalize_root(project_root: Any) -> str:
    """A stable spelling of the repository path.

    Resolved so ``.``, a relative path and a symlinked spelling of the same
    checkout produce one key — two surfaces disagreeing about how to write the
    same path must not become two runs.
    """
    if project_root is None:
        return ""
    try:
        return str(Path(project_root).expanduser().resolve())
    except (OSError, ValueError, RuntimeError):
        return str(project_root)


def _normalize_task(task: Any) -> str:
    """Compare task text by content, ignoring only incidental whitespace.

    Leading/trailing whitespace differs between a paste and a retype of the
    same instruction; interior text is left exactly alone, because a change
    anywhere inside it is a different instruction.
    """
    text = "" if task is None else str(task)
    return text.strip()[:_MAX_TASK_CHARS]


def admission_key(
    *,
    project_root: Any,
    task: Any,
    model: Any = None,
    mode: Any = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Derive the stable admission key for one submission.

    Every input that changes *what OPai would do* is in the key. Nothing that
    varies per attempt (timestamps, random ids, event ids) is, or a retry would
    key differently from the submission it repeats and the dedup would never
    fire — which is exactly the bug this exists to fix.
    """
    material = {
        "root": _normalize_root(project_root),
        "task": _normalize_task(task),
        "model": str(model or ""),
        "mode": str(mode or ""),
    }
    if extra:
        material["extra"] = {str(k): str(v) for k, v in sorted(extra.items())}
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"adm_{digest[:32]}"


def admit(
    registry: Any,
    *,
    key: str,
    request_id: str,
    provider: str = "pipeline",
    cancel: Any = None,
    pid: int | None = None,
) -> Admission:
    """Admit ``request_id`` under ``key``, or report the run already in flight.

    The check and the start are **one atomic step inside the registry**, under
    its lock. Doing it here as check-then-act would leave a window in which two
    threads both observe "nothing running" and both start — and a double-click
    is precisely two near-simultaneous submissions, so that window is the case
    this function exists to catch, not an unlikely edge.
    """
    existing_id = registry.claim(key, request_id, provider, cancel=cancel, pid=pid)
    if existing_id is not None:
        return Admission(
            decision=DUPLICATE,
            key=key,
            request_id=existing_id,
            existing_request_id=existing_id,
        )
    return Admission(decision=ACCEPTED, key=key, request_id=str(request_id))
