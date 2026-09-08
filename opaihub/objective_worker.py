"""One process-local assignment, with independent consent and provider routing."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import threading

from .atomic_io import atomic_write_text
from .boundary_errors import safe_detail


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    request_path, response_path = map(Path, args)
    if request_path.stat().st_size > 100_000:
        raise ValueError("Worker request exceeds bounded context")
    packet = json.loads(request_path.read_text(encoding="utf-8"))
    from .execution_scope import assignment_cost_events
    from .gui_pipeline import handle_gui_message

    cancel = threading.Event()

    def parent_closed():
        stream = getattr(sys.stdin, "buffer", None)
        if stream is not None:
            stream.read()
            cancel.set()

    threading.Thread(
        target=parent_closed, name="objective-parent-watch", daemon=True
    ).start()

    def activity(event):
        if isinstance(event, dict):
            fields = {
                key: str(event[key])[:1000]
                for key in ("phase", "status", "title", "detail")
                if key in event
            }
            metadata = event.get("metadata") or {}
            for key in ("model", "provider"):
                if isinstance(metadata, dict) and metadata.get(key):
                    fields[key] = str(metadata[key])[:200]
            atomic_write_text(
                response_path.parent / "activity.json", json.dumps(fields)
            )

    try:
        result = handle_gui_message(
            Path(packet["worktree"]),
            packet["prompt"],
            model_id=packet.get("model_id"),
            mode=packet["mode"],
            allow_cloud=packet.get("allow_cloud", False),
            task_id=packet["task_id"],
            run_id=packet["run_id"],
            authority_root=Path(packet["authority_root"]),
            cancel=cancel,
            on_event=activity,
        )
        # Persist only attributable output, not the parent transcript or raw provider metadata.
        payload = {
            "status": result.get("status", "failed"),
            "answer": str(result.get("answer", ""))[:100_000],
            "error": result.get("error"),
            "completion_state": result.get("completion_state"),
            "stopped_reason": result.get("stopped_reason"),
            "completion_verdict": result.get("completion_verdict"),
            "objective_cost_events": result.get("objective_cost_events")
            or assignment_cost_events(Path(packet["authority_root"]), packet["run_id"]),
        }
    except Exception as exc:  # noqa: BLE001 - parent must retain failed attempt evidence
        payload = {
            "status": "failed",
            "error": safe_detail(exc, limit=500),
            "objective_cost_events": assignment_cost_events(
                Path(packet["authority_root"]), packet["run_id"]
            ),
        }
    atomic_write_text(response_path, json.dumps(payload, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
