"""CLI streaming ask — the terminal front-end over the same core as the GUI.

``opai ask --model claude:opus "task"`` runs through
``opaihub.gui_pipeline.handle_gui_message`` — the exact pipeline the desktop GUI
uses — with live activity lines, streamed answer text, a real Ctrl+C cancel
(sets the cancel Event, which kills the provider subprocess), and an honest
cost/savings footer from the same receipt the GUI shows. One core, two
surfaces.

Kept import-light and injectable (``account_runner``/``printer``/``clock``) so
tests drive it with fakes — no real CLI, no network, no spend.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from opai.activity import stage_message
from opaihub.completion import verdict_label
from opaihub.boundary_errors import safe_detail

# Activity glyphs (stdout is forced to UTF-8 by opai.cli.main).
_GLYPH = {
    "pending": "◌",
    "running": "◐",
    "success": "✓",
    "warning": "!",
    "error": "✗",
    "cancelled": "⊘",
}

ANSWERED = {"answered", "cache_hit", "answered_by_account", "answered_locally"}


def normalize_model_choice(raw: str | None) -> str:
    """Map friendly CLI shorthand to pipeline model ids.

    ``auto`` and local ids pass through; ``claude[:alias]`` / ``codex[:model]``
    / ``copilot[:model]`` gain the ``account:`` prefix. Account model ids and
    aliases are canonicalized through the registry (#170), so friendly or dated
    names — ``claude:opus-4.8``, ``codex:spark`` — resolve instead of failing
    later as a "model not found" that looks like an auth error. Unknown models
    pass through unchanged (the provider may accept a name we don't list yet).
    """
    from opai.model_registry import resolve_id

    value = (raw or "auto").strip()
    if not value or value == "auto":
        return "auto"
    if value.startswith("account:"):
        parts = value.split(":", 2)
        if len(parts) == 3:
            canonical = resolve_id(parts[1], parts[2])
            if canonical:
                return f"account:{parts[1]}:{canonical}"
        return value
    head, _, rest = value.partition(":")
    if head.lower() in {"claude", "codex", "copilot"}:
        if rest:
            canonical = resolve_id(head.lower(), rest)
            if canonical:
                return f"account:{head.lower()}:{canonical}"
        return f"account:{value}"
    return value


def _footer_bits(result: dict[str, Any], elapsed_s: float) -> list[str]:
    bits = [f"done in {elapsed_s:.0f}s"]
    receipt = result.get("receipt") or {}
    actual = receipt.get("estimated_actual_usd")
    saved = receipt.get("estimated_savings_usd")
    confidence = str(receipt.get("confidence") or "estimated")
    if isinstance(actual, (int, float)) and actual:
        label = "spent" if confidence == "actual" else "est. spent"
        bits.append(f"${float(actual):.4f} {label}")
    if isinstance(saved, (int, float)) and saved:
        bits.append(f"${float(saved):.4f} saved (estimated)")
    if receipt.get("paid_call_avoided"):
        bits.append("paid call avoided")
    return bits


def _terminal_verdict(result: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Read canonical terminal presentation, with legacy import fallback."""

    if "run_result" in result:
        from opaihub.run_result import terminal_presentation

        canonical = terminal_presentation(result.get("run_result"))
        supplement = result.get("completion_verdict")
        next_action = (
            str(supplement.get("next_action") or "").strip()
            if isinstance(supplement, dict)
            else ""
        )
        return (
            canonical.state,
            canonical.reason,
            next_action,
            canonical.label,
        )

    raw = result.get("completion_verdict")
    if not isinstance(raw, dict):
        return None
    verdict = str(raw.get("verdict") or "").strip().lower()
    reason = str(raw.get("reason") or "").strip()
    next_action = str(raw.get("next_action") or "").strip()
    return (verdict, reason, next_action, verdict_label(verdict)) if verdict else None


def _answer_conflicts(result: dict[str, Any]) -> bool:
    """Whether the streamed answer claims success the verdict could not confirm.

    Round 5 finding 2 was a GUI report — a red "Failed" pill above "successfully
    pushed" — but the CLI has the same two surfaces: the streamed prose, then the
    outcome block. The engine sets one flag; both surfaces must honour it, or the
    CLI reproduces the bug the GUI just fixed.
    """

    raw = result.get("completion_verdict")
    return isinstance(raw, dict) and raw.get("answer_conflicts") is True


def _evidence_line(result: dict[str, Any]) -> str | None:
    """The changed-file evidence, so the CLI outcome block carries the same
    content the GUI outcome card shows (#396). Tests/answer evidence live in the
    verdict reason already; files are the CLI's concrete "what changed"."""

    changed = [
        str(path).strip()
        for path in (result.get("changed_files") or ())
        if str(path).strip()
    ]
    if not changed:
        return None
    shown = ", ".join(changed[:8])
    more = f" (+{len(changed) - 8} more)" if len(changed) > 8 else ""
    return f"Changed {len(changed)} file(s): {shown}{more}"


def _consent_request(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a pending command-approval request from a pipeline result.

    The pipeline surfaces a confirm-class command (git push, gh mutations, …)
    as a consent payload carrying the exact command string and the reason
    (F17). Keys are probed defensively so older payloads degrade to None.
    """
    for key in ("consent", "consent_payload", "approval"):
        payload = result.get(key)
        if isinstance(payload, dict) and str(payload.get("command") or "").strip():
            return payload
    return None


def stream_ask(
    project_root: Path,
    task: str,
    *,
    model: str | None = None,
    mode: str = "ask",
    json_out: bool = False,
    account_runner: Any = None,
    printer: Callable[[str], None] = print,
    reassure_after_s: float | None = None,
) -> int:
    """Run one task through the shared GUI pipeline with live CLI output.

    Returns the process exit code: 0 answered, 130 cancelled (Ctrl+C
    convention), 2 anything else. With ``json_out`` the activity lines are
    suppressed and the full result (plus collected events) prints as JSON.
    """
    from opai.brand import cli_header
    from opaihub.gui_pipeline import handle_gui_message

    root = project_root.expanduser().resolve()
    model_id = normalize_model_choice(model)
    if not json_out:
        printer(cli_header(mode, model_id))
    cancel = threading.Event()
    done = threading.Event()
    result_box: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    # One lock so activity lines, streamed text, and heartbeats never interleave.
    out_lock = threading.Lock()

    # #545: register this turn in the same durable thread store + owner lease
    # the GUI already writes, so a CLI-started task is discoverable from the
    # GUI (and `opai resume`, from a second terminal) instead of vanishing
    # the moment this process exits. Best-effort throughout -- a durability
    # write must never break the actual request it is describing.
    request_id = uuid.uuid4().hex[:12]
    try:
        from opai.gui_recents import begin_thread_turn

        begin_thread_turn(root, request_id=request_id, text=task, mode=mode)
    except (OSError, TypeError, ValueError):
        pass
    state = {
        "streaming": False,
        "last_line_open": False,
        "stream_line": False,
        "stream_finished": False,
    }

    def _line(text: str) -> None:
        with out_lock:
            if state["last_line_open"]:
                printer("")
                state["last_line_open"] = False
            printer(text)

    def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        if json_out:
            return
        if event.get("channel") == "status":
            # Status mirrors (connection/model) belong to the GUI status strip;
            # on the CLI they duplicate the feed line, so they are not printed.
            return
        status = str(event.get("status"))
        glyph = _GLYPH.get(status, "•")
        title = str(event.get("title") or "")
        detail = str(event.get("detail") or "")
        if str(event.get("id") or "").endswith(":stream"):
            # Mirror the GUI's single live stream row (#227): the per-chunk
            # "Streaming response" updates collapse to one line at the start and
            # one at completion — the answer text itself already streams to
            # stdout, so the intermediate char-count lines are pure noise.
            if status in {"success", "error", "cancelled"}:
                state["stream_finished"] = True
                _line(f"{glyph} {title}" + (f"  ({detail})" if detail else ""))
            elif not state["stream_line"]:
                state["stream_line"] = True
                _line(f"{glyph} {title}")
            return
        if (
            event.get("type") == "completion_verdict"
            and (event.get("metadata") or {}).get("reason_code") == "answer_delivered"
            and state["stream_finished"]
        ):
            # The provider's stream row already printed "Response received".
            # Do not print the answer-delivery verdict as a second identical
            # terminal line; the structured result still retains the verdict.
            return
        _line(f"{glyph} {title}" + (f"  ({detail})" if detail else ""))

    def on_text(chunk: str) -> None:
        if json_out:
            return
        with out_lock:
            if not state["streaming"]:
                state["streaming"] = True
                printer("")
            print(chunk, end="", flush=True)
            state["last_line_open"] = not chunk.endswith("\n")

    def job() -> None:
        try:
            result_box.update(
                handle_gui_message(
                    root,
                    task,
                    model_id=model_id,
                    mode=mode,
                    account_runner=account_runner,
                    on_event=on_event,
                    on_text=on_text,
                    cancel=cancel,
                )
            )
        except Exception as exc:  # noqa: BLE001 - degrade to a clean error result
            result_box.update({"status": "error", "answer": safe_detail(exc)})
        finally:
            done.set()

    started = time.monotonic()
    threshold = reassure_after_s
    if threshold is None:
        threshold = 15.0
    next_reassure = threshold
    worker = threading.Thread(target=job, daemon=True)
    worker.start()
    try:
        while not done.wait(0.2):
            elapsed = time.monotonic() - started
            try:
                from opai.gui_recents import refresh_thread_lease

                refresh_thread_lease(root, request_id=request_id)
            except (OSError, TypeError, ValueError):
                pass
            if not json_out and not state["streaming"] and elapsed >= next_reassure:
                sm = stage_message(elapsed, model_label=model_id)
                _line(f"… {sm['stage']} · {int(elapsed)}s elapsed (Ctrl+C to stop)")
                next_reassure = elapsed + threshold
    except KeyboardInterrupt:
        cancel.set()
        _line("⊘ Stopping…")
        done.wait(10)

    result = result_box or {"status": "error", "answer": "No result."}
    elapsed_s = time.monotonic() - started
    try:
        from opai.gui_recents import finish_thread_turn, thread_status_for_result

        finish_thread_turn(
            root,
            request_id=request_id,
            answer=str(result.get("answer") or ""),
            status=thread_status_for_result(
                str(result.get("status") or "failed"),
                result.get("completion_verdict"),
                result.get("run_result"),
            ),
            task_id=str((result.get("workflow") or {}).get("task_id") or request_id),
            mode=str((result.get("workflow") or {}).get("mode") or mode),
            checkpoint_id=str(result.get("checkpoint_id") or ""),
            changed_files=result.get("changed_files") or (),
        )
    except (OSError, TypeError, ValueError):
        pass
    status = str(result.get("status") or "error")

    if json_out:
        payload = dict(result)
        payload["events"] = events
        payload["elapsed_s"] = round(elapsed_s, 3)
        printer(json.dumps(payload, indent=2, default=str, sort_keys=True))
    else:
        answer = str(result.get("answer") or "").strip()
        if not state["streaming"] and answer:
            # Non-streamed paths (blocked/errors/local hints) still show the answer.
            _line("")
            _line(answer)
        elif state["last_line_open"]:
            _line("")
        footer = " · ".join(_footer_bits(result, elapsed_s))
        consent = _consent_request(result)
        if consent is not None:
            # F17: never bury an approval request inside a bare status line.
            _line(f"! Approval needed to run: {consent['command']}")
            reason = str(consent.get("reason") or "").strip()
            if reason:
                _line(f"  {reason}")
            _line("  Approve it in the app, or re-run with the command allowed.")
        if (terminal := _terminal_verdict(result)) is not None:
            verdict, reason, next_action, label = terminal
            glyph = (
                "✓"
                if verdict == "completed"
                else "⊘"
                if verdict == "cancelled"
                else "!"
            )
            # #396: the same verdict vocabulary the GUI and receipt summary use.
            legacy_detail = (
                f" ({status})" if status not in ANSWERED and status != verdict else ""
            )
            _line(f"{glyph} {label}{legacy_detail} — {reason}")
            if _answer_conflicts(result):
                # Round 5 finding 2: the streamed answer above claims the work
                # succeeded. Say plainly that this verdict disagrees, so the two
                # halves of the output cannot be read as opposite conclusions.
                _line(
                    "  Note: the response above claims this succeeded. OPai could "
                    "not verify that — treat the claim as unconfirmed."
                )
            if (evidence := _evidence_line(result)) is not None:
                _line(f"  {evidence}")
            if next_action and verdict != "completed":
                _line(f"  Next: {next_action}")
            # #656: the CLI lists the same typed recovery actions the GUI card
            # renders, with the same availability and disabled reasons.
            recovery = result.get("recovery")
            if verdict != "completed":
                from opaihub.recovery_actions import render_recovery_text

                for line in render_recovery_text(recovery):
                    _line(f"  {line}")
            _line(f"  {footer}")
        elif status in ANSWERED:
            _line(f"✓ {footer}")
        else:
            _line(f"✗ {status} · {footer}")

    # #295 Workstream H: the exit code names *which* ending this was, so a
    # script can retry a timeout without retrying a refusal. The mapping lives
    # in run_state beside the states themselves, not here, so the CLI cannot
    # drift from the canonical vocabulary.
    from opaihub.run_state import exit_code_for

    terminal = _terminal_verdict(result)
    if terminal is not None:
        return exit_code_for(terminal[0])
    if status == "cancelled":
        return exit_code_for("cancelled")
    if status == "duplicate_request":
        # #295 gate 3: an identical run is already in flight, so this
        # invocation started nothing. `blocked` is the honest code — the work
        # is not done *by this call* and retrying will hit the same guard while
        # the original runs — and it is already the "do not retry" signal.
        return exit_code_for("blocked")
    return exit_code_for("completed" if status in ANSWERED else "failed")
