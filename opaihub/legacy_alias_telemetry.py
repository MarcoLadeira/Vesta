"""Which compatibility aliases are still actually used (#612 AC9).

The acceptance criterion is: "Legacy mappings have explicit usage telemetry
and a removal criterion."

**This is a refinement of existing machinery, not a replacement.**
``opaihub/legacy_status.py`` already counts legacy reads and writes and
already publishes the gate they must clear —
``REMOVAL_GATE = "zero authoritative legacy reads and writes for one
supported release"`` — through :func:`~opaihub.legacy_status.legacy_status_usage`.
That surface stays the single place to ask; this module only gives it
something it could not previously answer.

The gap it fills is granularity. Aggregate counters tell you when *all*
legacy usage has stopped, so the 50 compatibility aliases
(43 status words, 7 presentation states) can only ever be retired as one
all-or-nothing batch — and one stubborn alias keeps the other 49 alive
indefinitely. #612's functional requirement 7 asks for a *deletion plan*,
which needs per-alias evidence: **which specific aliases has this
installation actually resolved?**

## The removal criterion, refined per alias

An individual alias is a removal candidate when it has been observed
**zero** times across a representative window — a full test run plus normal
usage — *and* the existing ``REMOVAL_GATE`` reasoning holds for it: no
supported older release is known to still emit it.

:func:`removal_candidates` computes the mechanical half. The second half is
a human judgement this module deliberately does not pretend to make.
Deleting a mapping is a compatibility break and should stay a decision
someone signs off on.

## Deliberate limitations, stated rather than discovered later

* **In-process only.** Counters live in memory and reset when the process
  does. There is no file, no ledger event, no network — an alias resolution
  happens on hot read paths, and paying I/O per resolution to measure
  something nobody reads per-run would be a bad trade. The intended use is
  "run the suite, or a session, then ask" — not longitudinal collection.
* **Names only.** An alias is a fixed vocabulary word from the schema. No
  task text, run id, path or user data is recorded, so this is safe to
  surface in diagnostics without a privacy review.
* **Absence is weak evidence.** Never observing an alias in one process
  proves only that *this* window did not reach it. That is exactly why the
  criterion above requires a representative window and a human check.
"""

from __future__ import annotations

import threading
from typing import Any

# Alias families, matching the schema's own `legacy_mappings` sections.
STATUS = "status"
STATE = "state"
KINDS = (STATUS, STATE)

_LOCK = threading.Lock()
_OBSERVED: dict[str, dict[str, int]] = {kind: {} for kind in KINDS}


def observe(kind: str, alias: str) -> None:
    """Record that a compatibility ``alias`` was resolved.

    Called from the resolution chokepoints. Cheap by construction — a dict
    increment under a lock — and never raises: telemetry must not be able to
    break a lifecycle read.
    """

    if kind not in _OBSERVED:
        return
    key = str(alias or "").strip().lower()
    if not key:
        return
    with _LOCK:
        bucket = _OBSERVED[kind]
        bucket[key] = bucket.get(key, 0) + 1


def usage_snapshot() -> dict[str, dict[str, int]]:
    """Observed alias -> resolution count, per family. A copy, not the store."""

    with _LOCK:
        return {kind: dict(counts) for kind, counts in _OBSERVED.items()}


def reset() -> None:
    """Clear all counters. For tests and for starting a measurement window."""

    with _LOCK:
        for counts in _OBSERVED.values():
            counts.clear()


def _declared_aliases() -> dict[str, set[str]]:
    from .generated_lifecycle import LEGACY_STATE_MAP, LEGACY_STATUS_MAP

    return {
        STATUS: {str(alias).lower() for alias in LEGACY_STATUS_MAP},
        STATE: {str(alias).lower() for alias in LEGACY_STATE_MAP},
    }


def removal_candidates() -> dict[str, list[str]]:
    """Declared aliases this process never resolved, per family.

    The *first* half of the removal criterion in this module's docstring.
    An entry here is a candidate to investigate, not a mapping to delete —
    see the limitations above on why absence is weak evidence.
    """

    observed = usage_snapshot()
    declared = _declared_aliases()
    return {
        kind: sorted(declared[kind] - set(observed.get(kind, {}))) for kind in KINDS
    }


def coverage_report() -> dict[str, Any]:
    """A diagnostic-ready summary of compatibility-alias usage."""

    observed = usage_snapshot()
    declared = _declared_aliases()
    candidates = removal_candidates()
    families = {}
    for kind in KINDS:
        total = len(declared[kind])
        seen = len(observed.get(kind, {}))
        families[kind] = {
            "declared": total,
            "observed": seen,
            "unobserved": len(candidates[kind]),
            "resolutions": sum(observed.get(kind, {}).values()),
            "removal_candidates": candidates[kind],
        }
    from .legacy_status import REMOVAL_GATE

    return {
        "report": "opai-legacy-alias-usage",
        "scope": "current process only; counters reset on restart",
        # Deliberately quotes the existing gate rather than inventing a second,
        # subtly different rule — two competing removal criteria would be the
        # same class of drift this epic exists to remove.
        "removal_gate": REMOVAL_GATE,
        "criterion": (
            "Per alias, refining the shared removal gate: a candidate is an "
            "alias observed zero times across a representative window (a full "
            "test run plus normal usage) AND for which no supported older "
            "release is known to still emit it. The second condition is a "
            "human judgement this report does not make."
        ),
        "families": families,
    }
