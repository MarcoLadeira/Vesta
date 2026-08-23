"""Bounded, previewable diagnostic bundle for support requests (#551).

Composes already-redacted local evidence -- the tamper-evident audit trail
(:mod:`opaihub.audit`) and recent usage-ledger events (:mod:`opaihub.ledger`)
-- into one JSON document a user can inspect before sharing. Every source
this reads has already been through its own redaction pass at write time
(``record_audit_event``, ``record_event``), so this module never touches raw
provider output, file contents, environment variables, or credentials.

The bundle is a hard-capped snapshot, not an open-ended export: event counts
and total serialized size are both bounded, so a long-lived, heavily-used
project cannot produce a bundle that is awkward or unsafe to attach to a
support request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opai.release_identity import surface_identity_payload

from .audit import export_audit
from .ledger import read_events

MAX_AUDIT_EVENTS = 200
MAX_LEDGER_EVENTS = 200
MAX_BUNDLE_BYTES = 2_000_000


def _bundle_size(bundle: dict[str, Any]) -> int:
    return len(json.dumps(bundle, default=str).encode("utf-8"))


def _shrink_to_budget(bundle: dict[str, Any]) -> dict[str, Any]:
    """Drop the oldest events, in halves, until the bundle fits the cap.

    Most-recent-first: the tail of each event list is what's kept, since
    that's what's actually relevant to diagnosing what just happened.
    """
    while _bundle_size(bundle) > MAX_BUNDLE_BYTES:
        audit_events = bundle["audit"]["events"]
        ledger_events = bundle["ledger_events"]
        if not audit_events and not ledger_events:
            break
        if len(audit_events) >= len(ledger_events) and audit_events:
            keep = len(audit_events) // 2
            bundle["audit"]["events"] = audit_events[-keep:] if keep else []
            bundle["truncated"]["audit_events"] = True
        elif ledger_events:
            keep = len(ledger_events) // 2
            bundle["ledger_events"] = ledger_events[-keep:] if keep else []
            bundle["truncated"]["ledger_events"] = True
        else:
            break
    return bundle


def build_support_bundle(project_root: Path) -> dict[str, Any]:
    """Assemble a bounded diagnostic bundle. Read-only; nothing is written."""
    root = project_root.expanduser().resolve()
    audit = export_audit(root, sign=True)
    full_audit_events = list(audit.get("events") or [])
    audit_events = full_audit_events[-MAX_AUDIT_EVENTS:]
    ledger_events = read_events(root, limit=MAX_LEDGER_EVENTS)

    bundle: dict[str, Any] = {
        "report": "opai-support-bundle",
        "schema_version": 1,
        **surface_identity_payload(),
        "project_root": str(root),
        "audit": {**audit, "events": audit_events},
        "ledger_events": ledger_events,
        "truncated": {
            "audit_events": len(audit_events) < len(full_audit_events),
            # read_events(..., limit=N) already drops anything past N, so
            # hitting the cap exactly is the only local signal of truncation.
            "ledger_events": len(ledger_events) >= MAX_LEDGER_EVENTS,
        },
    }
    return _shrink_to_budget(bundle)
