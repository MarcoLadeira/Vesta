"""Local-only UX / product-health metrics (#395).

Privacy-first by construction: every number here is computed from the
append-only *local* ledger — the same ``completion_verdict`` events Vesta already
records — and this module never opens a socket. The user's machine is the only
place these metrics exist; sharing them is a separate, explicit, redacted export
action, never a background upload.

This is the metrics engine for the epic's success criteria (#377): the
completion-verdict distribution and the cancel/timeout rates that say whether the
command centre actually works. The per-surface UX event taxonomy (control_used,
state_seen, ...) builds on this same discipline and is tracked separately.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .completion import CompletionVerdict
from .ledger import UNKNOWN, read_events

UX_METRICS_SCHEMA_VERSION = 1

EVENT_COMPLETION_VERDICT = "completion_verdict"

# The verdict vocabulary, in a stable reporting order.
_VERDICTS: tuple[str, ...] = (
    CompletionVerdict.COMPLETED.value,
    CompletionVerdict.PARTIAL.value,
    CompletionVerdict.BLOCKED.value,
    CompletionVerdict.FAILED.value,
    CompletionVerdict.CANCELLED.value,
    CompletionVerdict.TIMEOUT.value,
)


def _rate(count: int, total: int) -> float | str:
    """A share of runs, or ``unknown`` when there is nothing to divide by — a
    zero-run project reports honest unknowns, never a fabricated 0%."""

    return round(count / total, 4) if total else UNKNOWN


def summarize_ux_metrics(project_root: Path) -> dict[str, Any]:
    """Reconcile local ledger events into product-health metrics. Read-only.

    Reports the completion-verdict distribution and the cancel/timeout/partial
    rates from every recorded terminal verdict. No value is imputed: a project
    with no runs reports ``runs=0`` and ``unknown`` rates.
    """

    root = project_root.expanduser().resolve()
    events = read_events(root)
    verdict_events = [
        event for event in events if event.get("event_type") == EVENT_COMPLETION_VERDICT
    ]

    distribution: dict[str, int] = {verdict: 0 for verdict in _VERDICTS}
    unknown_verdicts = 0
    for event in verdict_events:
        verdict = str(event.get("verdict") or "").strip().lower()
        if verdict in distribution:
            distribution[verdict] += 1
        else:
            unknown_verdicts += 1

    runs = sum(distribution.values())
    return {
        "schema_version": UX_METRICS_SCHEMA_VERSION,
        "project": str(root),
        "runs": runs,
        "verdict_distribution": distribution,
        "unclassified_verdicts": unknown_verdicts,
        "rates": {
            "completion": _rate(distribution["completed"], runs),
            "partial": _rate(distribution["partial"], runs),
            "blocked": _rate(distribution["blocked"], runs),
            "failure": _rate(distribution["failed"], runs),
            "cancel": _rate(distribution["cancelled"], runs),
            "timeout": _rate(distribution["timeout"], runs),
        },
        "source": "local completion_verdict events (no telemetry leaves the machine)",
    }


def render_ux_metrics_markdown(metrics: dict[str, Any]) -> str:
    """A compact, script-friendly report of the local product-health metrics."""

    runs = int(metrics.get("runs") or 0)
    lines = ["# Vesta product health (local only)", ""]
    if not runs:
        lines.append("No runs recorded yet — run a task to start your metrics.")
        return "\n".join(lines) + "\n"
    distribution = metrics.get("verdict_distribution") or {}
    rates = metrics.get("rates") or {}
    lines.append(f"Runs: {runs}")
    lines.append("")
    lines.append("| Verdict | Count | Share |")
    lines.append("| --- | ---: | ---: |")
    rate_key = {
        "completed": "completion",
        "partial": "partial",
        "blocked": "blocked",
        "failed": "failure",
        "cancelled": "cancel",
        "timeout": "timeout",
    }
    for verdict in _VERDICTS:
        count = int(distribution.get(verdict) or 0)
        share = rates.get(rate_key[verdict])
        share_text = f"{share * 100:.1f}%" if isinstance(share, (int, float)) else "—"
        lines.append(f"| {verdict} | {count} | {share_text} |")
    lines.append("")
    lines.append(f"_{metrics.get('source', '')}_")
    return "\n".join(lines) + "\n"
