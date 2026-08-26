"""#613: the legacy run record, assembled into the shape Stages 4-7 compare.

Stages 4, 5 and 7 all take a *legacy corpus* -- a mapping of run id to what the
old record says about that run -- and every one of them was written against a
corpus a caller supplies. Until now no caller supplied one, which left the
qualification, reader and retirement machinery correct, tested, and unreachable
from the running application. Doctor could only answer
``needs_legacy_comparison``, because nothing had assembled the other half.

``background_runs`` is that other half. It is the one legacy record in OPai
that is durable, enumerable, and keyed by run id: one JSON document per run
under ``.../agent/background/runs/``, rewritten in place on every transition.
That makes it exactly the population the journal now mirrors, and the two can
therefore be compared run for run.

The one judgement this module makes is what counts as a terminal verdict.
``run_state`` is authoritative (``status`` is the pre-#379 compatibility
vocabulary), so the canonical state is what gets compared, and a run whose
state is not in ``TERMINAL_STATES`` reports no verdict at all rather than a
guessed one. An unfinished run is a real and ordinary thing; inventing a
verdict for it would manufacture a disagreement with the journal, which is
the one thing a qualification corpus must never do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from . import background_runs
from .run_state import TERMINAL_STATES, RunState


def _terminal_verdict(run: Any) -> str:
    """The canonical terminal state of a run, or ``""`` if it has not reached one."""

    try:
        state = RunState(str(getattr(run, "run_state", "") or ""))
    except ValueError:
        # A record written by a newer OPai, or a corrupted one. Reporting no
        # verdict is honest: we cannot say this run finished, and claiming it
        # did would fabricate the very disagreement qualification looks for.
        return ""
    return state.value if state in TERMINAL_STATES else ""


def legacy_runs(project_root: Path) -> dict[str, dict[str, Any]]:
    """Every run the legacy record knows about, keyed by run id.

    Never raises. A corpus that blows up cannot be compared against, and the
    callers -- doctor, and the retirement gate -- are precisely the code that
    runs when something is already wrong.

    Note the failure shape: an unreadable corpus returns ``{}``, which reads
    identically to a project that genuinely has no background runs. That
    ambiguity is why ``journal_retirement`` refuses to retire on an empty
    corpus rather than treating it as "nothing left to compare".
    """

    try:
        runs = background_runs.list_runs(project_root)
    except Exception:  # noqa: BLE001 - an unreadable corpus is an empty one
        return {}

    corpus: dict[str, dict[str, Any]] = {}
    for run in runs:
        run_id = str(getattr(run, "run_id", "") or "").strip()
        if not run_id:
            continue
        corpus[run_id] = {
            "terminal_verdict": _terminal_verdict(run),
            "created_at": str(getattr(run, "created_at", "") or ""),
            "task_id": str(getattr(run, "workflow_id", "") or ""),
        }
    return corpus


__all__: Sequence[str] = ("legacy_runs",)
