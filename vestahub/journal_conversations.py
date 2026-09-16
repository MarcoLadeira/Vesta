"""The legacy record of a turn's outcome, and what the journal says.

#818's closing evidence must show **zero cross-surface terminal disagreement**.
Migration steps 2 and 4 ask for parity assertions between the legacy
projections and the canonical journal, and both sat at "not started" with one
stated reason: ``journal_background.legacy_runs`` reads background automation
JSON, the journal's runs come overwhelmingly from chat turns, and two
populations that never overlap cannot be compared no matter how long anybody
waits.

A chat turn has a legacy outcome record too -- it just is not a run file. It
is the assistant message in the saved conversation, which carries a
``status``. Comparing those two records is how the false-completion defect was
found:

    journal              21 completed, 6 cancelled
    saved conversations  16 complete, 5 partial, 5 needs_attention

Sixteen plus five is twenty-one: the journal was filing every partial turn as
a success. This module makes that comparison a standing check.

**On joining.** Each assistant message now carries the ``run_id`` of the
journal run that produced it, so the check is a real join, turn by turn. The
first version had no such key and matched a whole conversation against each
run -- which reported any chat with one complete and one partial turn as "2 of
2 runs disagree" (#818 review finding 4). Turns saved before the key existed
cannot be joined; they are counted as exactly that, never as disagreements.

**On vocabulary.** The two records do not name endings the same way: a turn
that stopped to ask is ``awaiting_input`` in the journal and saved under its
completion verdict (``blocked``) in the conversation. What the check compares
is the question #818 asks of both -- did this turn finish the work, was it
cancelled, or neither -- so a difference of wording is not reported as a
contradiction, and a difference of substance always is.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import journal_store

#: What an assistant message's status means in the journal's vocabulary. The
#: left side is what `gui_recents` persists; the right is what
#: `gui_pipeline._journal_ending` records for the same ending.
STATUS_TO_VERDICT = {
    "complete": "completed",
    "partial": "partial",
    "needs_attention": "needs_attention",
    "cancelled": "cancelled",
    "error": "failed",
    "failed": "failed",
    "blocked": "blocked",
    "timeout": "timeout",
}

#: Saved statuses that are not an ending: the turn had not finished when the
#: conversation was written, or the app closed under it.
_UNFINISHED_STATUSES = frozenset({"", "pending", "running", "interrupted"})

COMPLETED = "completed"
CANCELLED = "cancelled"
NOT_COMPLETED = "not_completed"


def outcome(verdict: str) -> str:
    """The question both records can answer: finished, cancelled, or neither."""

    value = STATUS_TO_VERDICT.get(verdict, verdict)
    if value == "completed":
        return COMPLETED
    if value == "cancelled":
        return CANCELLED
    return NOT_COMPLETED


def _conversations_dir(project_root: Path) -> Path:
    return Path(project_root) / ".vestahub" / "gui" / "conversations"


def _read_conversations(
    project_root: Path,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Every saved assistant turn, and how many files could not be read.

    One pass. The first version parsed every conversation twice -- once for
    the turns and once more to count the unreadable ones.
    """

    directory = _conversations_dir(project_root)
    found: dict[str, list[dict[str, Any]]] = {}
    unreadable = 0
    if not directory.is_dir():
        return found, unreadable
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(payload, dict):
            unreadable += 1
            continue
        conversation_id = str(payload.get("id") or path.stem)
        turns = [
            {
                "status": str(message.get("status") or "").strip().lower(),
                "at": str(message.get("timestamp") or ""),
                "run_id": str(message.get("run_id") or "").strip(),
            }
            for message in (payload.get("messages") or [])
            if isinstance(message, dict) and message.get("role") == "assistant"
        ]
        if turns:
            found[conversation_id] = turns
    return found, unreadable


def conversation_outcomes(project_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Every assistant turn the saved conversations record.

    Never raises: this is read by doctor, which runs when something is already
    wrong. An unreadable conversation contributes nothing -- but see
    :func:`turn_parity`, which counts what it could not read instead of
    quietly shrinking the population.
    """

    return _read_conversations(project_root)[0]


def turn_parity(project_root: Path) -> dict[str, Any]:
    """Do the journal and the saved conversations agree about how turns ended?

    ``joined`` compares turn by turn, through the ``run_id`` each saved turn
    carries. Its disagreements are real contradictions: one record says the
    turn finished the work and the other does not, or one says it was
    cancelled and the other does not.

    Not compared, and counted instead:

    * ``unjoinable_turns`` -- saved before turns carried a run id;
    * ``in_progress`` -- one side has not recorded an ending yet;
    * ``turns_without_a_run`` -- a saved turn whose run the journal never
      admitted (a journal write that failed, which is worth knowing about but
      is not a disagreement about an ending).

    ``aggregate`` compares the two populations' outcome distributions. It is a
    *lead*, not a proof, and labelled as one.
    """

    report: dict[str, Any] = {
        "available": False,
        "reason": "",
        "joined": {
            "runs": 0,
            "agreements": 0,
            "disagreements": [],
            "disagreement_count": 0,
        },
        "unjoinable_turns": 0,
        "in_progress": 0,
        "turns_without_a_run": 0,
        "unreadable_conversations": 0,
        "aggregate": {"journal": {}, "conversations": {}},
    }

    conversations, unreadable = _read_conversations(project_root)
    report["unreadable_conversations"] = unreadable
    try:
        store = journal_store.open_store(project_root)
    except Exception as exc:  # noqa: BLE001 - a report must not raise
        report["reason"] = journal_store.describe_open_failure(exc)
        return report
    try:
        # Every run, whatever its surface: the join is by run id, and a GUI
        # build turn is saved in the conversation too.
        rows = store.execute(
            "SELECT r.run_id AS run_id, r.terminal_verdict AS verdict,"
            " t.origin_surface AS surface"
            " FROM runs r LEFT JOIN tasks t ON t.task_id = r.task_id"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        report["reason"] = type(exc).__name__
        return report
    finally:
        store.close()

    journal = {str(row["run_id"]): str(row["verdict"] or "") for row in rows}
    # The lead compares chat turns with chat turns.
    journal_counts: Counter[str] = Counter(
        str(row["verdict"])
        for row in rows
        if row["verdict"] and str(row["surface"] or "") in {"gui", "cli"}
    )
    conversation_counts: Counter[str] = Counter()
    disagreements: list[dict[str, Any]] = []
    joined = agreements = unjoinable = in_progress = without_a_run = 0

    for conversation_id, turns in conversations.items():
        for turn in turns:
            saved = turn["status"]
            if saved not in _UNFINISHED_STATUSES:
                conversation_counts[STATUS_TO_VERDICT.get(saved, saved)] += 1
            run_id = turn["run_id"]
            if not run_id:
                unjoinable += 1
                continue
            if run_id not in journal:
                without_a_run += 1
                continue
            recorded = journal[run_id]
            if not recorded or saved in _UNFINISHED_STATUSES:
                in_progress += 1
                continue
            joined += 1
            if outcome(recorded) == outcome(saved):
                agreements += 1
                continue
            disagreements.append(
                {
                    "run_id": run_id,
                    "conversation": conversation_id,
                    "journal": recorded,
                    "saved": saved,
                }
            )

    report["available"] = True
    report["joined"] = {
        "runs": joined,
        "agreements": agreements,
        # Bounded, like every other report here: this is for acting on.
        "disagreements": disagreements[:50],
        "disagreement_count": len(disagreements),
    }
    report["unjoinable_turns"] = unjoinable
    report["in_progress"] = in_progress
    report["turns_without_a_run"] = without_a_run
    report["aggregate"] = {
        "journal": dict(journal_counts),
        "conversations": dict(conversation_counts),
    }
    return report


__all__ = (
    "STATUS_TO_VERDICT",
    "conversation_outcomes",
    "outcome",
    "turn_parity",
)
