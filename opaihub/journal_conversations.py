"""The legacy record of a GUI turn's outcome, and what the journal says.

#818's closing evidence must show **zero cross-surface terminal disagreement**.
Migration steps 2 and 4 ask for parity assertions between the legacy
projections and the canonical journal, and both have sat at "not started" with
one stated reason: ``journal_background.legacy_runs`` reads background
automation JSON, the journal's runs come overwhelmingly from GUI turns, and
two populations that never overlap cannot be compared no matter how long
anybody waits.

That reason is real, and it is a fact about *which pair* was being compared.
A GUI turn has a legacy outcome record too -- it just is not a run file. It is
the assistant message in the saved conversation, which carries a ``status``
of ``complete``, ``partial`` or ``needs_attention``.

Comparing those two populations is how the false-completion defect was found:

    journal              21 completed, 6 cancelled
    saved conversations  16 complete, 5 partial, 5 needs_attention

Sixteen plus five is twenty-one. The journal was filing every partial turn as
a success. This module makes that comparison a standing check rather than a
thing somebody once noticed.

**On joining.** A precise per-run join needs ``tasks.origin_session``, which
this branch started writing and which older runs do not carry. So the report
distinguishes what it can join from what it cannot, and never presents an
aggregate as if it were a join. An aggregate that happens to line up is a
lead, not a proof -- it is what pointed at the defect, and the defect was then
confirmed by reproducing it, not by the arithmetic.
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
}


def _conversations_dir(project_root: Path) -> Path:
    return Path(project_root) / ".opaihub" / "gui" / "conversations"


def conversation_outcomes(project_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Every finished assistant turn the saved conversations record.

    Never raises: this is read by doctor, which runs when something is already
    wrong. An unreadable conversation contributes nothing rather than taking
    the report down with it -- but see :func:`turn_parity`, which counts what
    it could not read instead of quietly shrinking the population.
    """

    directory = _conversations_dir(project_root)
    found: dict[str, list[dict[str, Any]]] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        conversation_id = str(payload.get("id") or path.stem)
        turns = [
            {
                "status": str(message.get("status") or ""),
                "at": str(message.get("timestamp") or ""),
            }
            for message in (payload.get("messages") or [])
            if isinstance(message, dict) and message.get("role") == "assistant"
        ]
        if turns:
            found[conversation_id] = turns
    return found


def _unreadable_conversations(project_root: Path) -> int:
    directory = _conversations_dir(project_root)
    if not directory.is_dir():
        return 0
    bad = 0
    for path in directory.glob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bad += 1
    return bad


def turn_parity(project_root: Path) -> dict[str, Any]:
    """Do the journal and the saved conversations agree about how turns ended?

    Two answers, kept apart on purpose.

    ``joined`` compares run-by-run, for runs whose task carries an
    ``origin_session`` naming a conversation. That is a real parity assertion
    and its disagreements are real contradictions.

    ``aggregate`` compares the two populations' outcome distributions. It is a
    *lead*, not a proof: two populations can share a shape without sharing
    members. It is reported because it is what surfaced the false-completion
    defect, and labelled as what it is so nobody mistakes it for the join.
    """

    report: dict[str, Any] = {
        "available": False,
        "reason": "",
        "joined": {"runs": 0, "agreements": 0, "disagreements": []},
        "unjoinable_runs": 0,
        "unreadable_conversations": _unreadable_conversations(project_root),
        "aggregate": {"journal": {}, "conversations": {}},
    }

    conversations = conversation_outcomes(project_root)
    try:
        store = journal_store.open_store(project_root)
    except Exception as exc:  # noqa: BLE001 - a report must not raise
        report["reason"] = type(exc).__name__
        return report
    try:
        rows = store.execute(
            "SELECT r.run_id AS run_id, r.terminal_verdict AS verdict,"
            " r.created_at AS created_at, t.origin_session AS session"
            " FROM runs r JOIN tasks t ON t.task_id = r.task_id"
            " WHERE t.origin_surface = 'gui'"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        report["reason"] = type(exc).__name__
        return report
    finally:
        store.close()

    journal_counts: Counter[str] = Counter()
    conversation_counts: Counter[str] = Counter()
    disagreements: list[dict[str, Any]] = []
    joined = agreements = unjoinable = 0

    for row in rows:
        verdict = str(row["verdict"] or "")
        if verdict:
            journal_counts[verdict] += 1
        session = str(row["session"] or "").strip()
        if not session or session not in conversations:
            unjoinable += 1
            continue
        # One run, one conversation. Matching a run to the *specific* turn
        # inside it needs a per-turn run id the conversation does not keep, so
        # a conversation whose turns all ended the same way can be compared and
        # a mixed one cannot -- reported rather than guessed at.
        endings = {
            STATUS_TO_VERDICT.get(turn["status"], turn["status"])
            for turn in conversations[session]
        }
        joined += 1
        if len(endings) != 1:
            disagreements.append(
                {
                    "run_id": str(row["run_id"]),
                    "session": session,
                    "journal": verdict,
                    "conversation": sorted(endings),
                    "note": "conversation has mixed endings; cannot attribute",
                }
            )
            continue
        only = next(iter(endings))
        if only == verdict:
            agreements += 1
        else:
            disagreements.append(
                {
                    "run_id": str(row["run_id"]),
                    "session": session,
                    "journal": verdict,
                    "conversation": only,
                }
            )

    for turns in conversations.values():
        for turn in turns:
            mapped = STATUS_TO_VERDICT.get(turn["status"], turn["status"])
            if mapped:
                conversation_counts[mapped] += 1

    report["available"] = True
    report["joined"] = {
        "runs": joined,
        "agreements": agreements,
        # Bounded, like every other report here: this is for acting on.
        "disagreements": disagreements[:50],
        "disagreement_count": len(disagreements),
    }
    report["unjoinable_runs"] = unjoinable
    report["aggregate"] = {
        "journal": dict(journal_counts),
        "conversations": dict(conversation_counts),
    }
    return report


__all__ = (
    "STATUS_TO_VERDICT",
    "conversation_outcomes",
    "turn_parity",
)
