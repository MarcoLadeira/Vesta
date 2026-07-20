"""AI activity events, stale-response guard, slow-model thresholds, error mapper,
and provider stream parsers — the correctness-critical core, kept Qt-free and
framework-free so it is unit-tested in Python (and the request-ID/threshold
logic is mirrored in ``assets/web/activity.js`` for the front-end).

Nothing here talks to a model or a display. The bridge and the runner feed it
raw provider output and call these helpers; the results drive the timeline, the
stop button, and the slow-model UX.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

# --------------------------------------------------------------------------- #
# Event model (mirrors the requested TS AiActivityEvent)
# --------------------------------------------------------------------------- #
STATUSES = ("pending", "running", "success", "warning", "error", "cancelled")

TYPES = (
    "request_prepare",
    "context_read",
    "model_selected",
    "provider_checking",
    "provider_authenticated",
    "provider_auth_failed",
    "provider_request",
    "request_sending",
    "waiting_first_token",
    "streaming",
    "tool_call",
    "file_read",
    "file_edit",
    "command_run",
    "command_complete",
    "ci_watch",
    "validation",
    "retry",
    "retrying",
    "verifying",
    "completion",
    "completion_verdict",
    "stopped",
    "completed",
    "failed",
    "cancelled",
    "error",
)

# Which tool name maps to which activity type + a verb for the title.
_TOOL_MAP = {
    "read": ("file_read", "Read file"),
    "edit": ("file_edit", "Edited file"),
    "write": ("file_edit", "Wrote file"),
    "multiedit": ("file_edit", "Edited file"),
    "notebookedit": ("file_edit", "Edited notebook"),
    "bash": ("command_run", "Ran command"),
    "grep": ("context_read", "Searched code"),
    "glob": ("context_read", "Searched files"),
    "webfetch": ("ci_watch", "Fetched URL"),
    "websearch": ("context_read", "Web search"),
    "task": ("tool_call", "Ran subtask"),
}


# Schema v2 channels (docs/AI_ACTIVITY_UX.md): "feed" renders a timeline row,
# "status" feeds the persistent status strip and never becomes a row.
CHANNELS = ("feed", "status")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def derived_id(request_id: str, phase: str) -> str:
    """Stable id for one logical step of a request.

    Repeated states that share a derived id upsert the same row in place
    instead of appending duplicates (the Calm Stream coalescing contract).
    """
    return f"{request_id}:{phase}"


def make_event(
    event_type: str,
    status: str,
    title: str,
    *,
    detail: str | None = None,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
    duration_ms: int | None = None,
    request_id: str | None = None,
    phase: str | None = None,
    channel: str | None = None,
    group: str | None = None,
) -> dict[str, Any]:
    """Build one activity event. ``timestamp`` is ms since epoch.

    Schema v2 fields (``request_id``/``phase``/``channel``/``group``) serialize
    as ``requestId``/``phase``/``channel``/``group`` and are omitted when not
    provided, so v1 payloads stay byte-identical.
    """
    if event_type not in TYPES:
        event_type = "tool_call"
    if status not in STATUSES:
        status = "running"
    event: dict[str, Any] = {
        "id": event_id or new_id(),
        "type": event_type,
        "status": status,
        "title": title,
        "detail": detail,
        "timestamp": int(time.time() * 1000),
        "durationMs": duration_ms,
        "metadata": metadata or {},
    }
    if request_id is not None:
        event["requestId"] = request_id
    if phase is not None:
        event["phase"] = phase
    if channel is not None:
        event["channel"] = channel if channel in CHANNELS else "feed"
    if group is not None:
        event["group"] = group
    return event


def emit_event(
    on_event: Callable[[dict[str, Any]], Any] | None,
    event_type: str,
    status: str,
    title: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build and best-effort deliver one event without risking the operation."""

    event = make_event(event_type, status, title, **kwargs)
    if on_event is not None:
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 - activity is observability, not control flow
            return event
    return event


# --------------------------------------------------------------------------- #
# Stale-response guard — the anti-race primitive
# --------------------------------------------------------------------------- #
def should_apply(
    current_request_id: str | None, incoming_request_id: str | None
) -> bool:
    """True only when an incoming signal belongs to the active request.

    Every request has a unique id; a reply/token/event only updates the UI when
    its id matches the current active id. A cancelled or superseded request sets
    the current id to something else (or None), so its late output is ignored —
    no stale response can overwrite the current message.
    """
    if not current_request_id:
        return False
    return current_request_id == incoming_request_id


# --------------------------------------------------------------------------- #
# Slow-model thresholds — "is it still working?"
# --------------------------------------------------------------------------- #
# Seconds after which we escalate the reassurance copy.
TAKING_LONGER_S = 15
STILL_WORKING_S = 45


def stage_message(
    elapsed_s: float,
    *,
    streaming: bool = False,
    model_label: str | None = None,
) -> dict[str, Any]:
    """Reassurance state for a running request based on elapsed time.

    Returns ``{stage, reassurance, suggest_faster, severity}``. Once tokens are
    streaming we say so; otherwise we escalate from waiting → taking longer →
    still working so a slow model (Opus) never looks frozen.
    """
    short = str(model_label or "the model").split(" · ")[0].split(" (")[0].strip()
    if streaming:
        return {
            "stage": "Streaming response",
            "reassurance": "",
            "suggest_faster": False,
            "severity": "running",
        }
    if elapsed_s < TAKING_LONGER_S:
        return {
            "stage": f"Waiting for {short}",
            "reassurance": "",
            "suggest_faster": False,
            "severity": "running",
        }
    if elapsed_s < STILL_WORKING_S:
        return {
            "stage": f"Waiting for {short}",
            "reassurance": f"{short} is taking longer than usual — complex prompts can take a while.",
            "suggest_faster": True,
            "severity": "warning",
        }
    return {
        "stage": f"Still working — {short}",
        "reassurance": "Still working. You can keep waiting, stop, or switch to a faster model.",
        "suggest_faster": True,
        "severity": "warning",
    }


# --------------------------------------------------------------------------- #
# Error mapper — every failure is recoverable and human-readable
# --------------------------------------------------------------------------- #
# status -> (title, what happened, what to do, [action ids])
_ERRORS: dict[str, dict[str, Any]] = {
    "cancelled": {
        "title": "Stopped by you",
        "what": "Generation was stopped before it finished.",
        "next": "You can edit the prompt, retry, or switch model.",
        "actions": ["retry", "edit"],
        "tone": "neutral",
    },
    "account_timeout": {
        "title": "{model} ran out of time",
        "what": "The request passed the time limit and was stopped.",
        "next": "Try a smaller request, a faster model, or run the long task in your terminal.",
        "actions": ["retry", "switch_model"],
        "tone": "warning",
    },
    "account_error": {
        "title": "{model} hit an error",
        "what": "The model couldn't finish the request.",
        "next": "Try again, or pick a different model.",
        "actions": ["retry", "switch_model", "details"],
        "tone": "error",
    },
    "account_not_connected": {
        "title": "No account connected",
        "what": "That model needs a signed-in CLI (Claude, Codex, or Copilot).",
        "next": "Run the CLI once to sign in, then pick it in the model menu.",
        "actions": ["connect", "switch_model"],
        "tone": "warning",
    },
    "needs_model": {
        "title": "No free model for this",
        "what": "Auto had no local model available to answer.",
        "next": "Pick Claude, Codex, or Copilot, or add a local model under Advanced.",
        "actions": ["switch_model", "connect"],
        "tone": "warning",
    },
    "needs_confirmation": {
        "title": "Needs a paid model",
        "what": "OPai won't spend on a paid call automatically.",
        "next": "Pick your Claude, Codex, or Copilot account to run it.",
        "actions": ["switch_model"],
        "tone": "warning",
    },
    "blocked": {
        "title": "Blocked as risky",
        "what": "Safe Auto stopped this before running it.",
        "next": "Switch to Full Auto only if you intend that.",
        "actions": ["edit"],
        "tone": "warning",
    },
    "blocked_panic": {
        "title": "Panic mode is on",
        "what": "Panic mode blocks paid/cloud calls (local-only).",
        "next": "Turn panic off in Settings to use a paid account.",
        "actions": ["edit"],
        "tone": "warning",
    },
    "rate_limit": {
        "title": "Rate limited",
        "what": "The provider is throttling requests right now.",
        "next": "Wait a moment and retry, or switch model.",
        "actions": ["retry", "switch_model"],
        "tone": "warning",
    },
    "network": {
        "title": "Network problem",
        "what": "Couldn't reach the provider.",
        "next": "Check your connection and retry.",
        "actions": ["retry", "details"],
        "tone": "error",
    },
    "unauthorized": {
        "title": "Not signed in",
        "what": "The CLI reported an auth problem.",
        "next": "Re-run the CLI and sign in, then retry.",
        "actions": ["connect", "retry"],
        "tone": "error",
    },
    "context_too_large": {
        "title": "Too much context",
        "what": "The request was larger than the model allows.",
        "next": "Reduce the prompt or context and retry.",
        "actions": ["edit", "retry"],
        "tone": "warning",
    },
    "runner_error": {
        "title": "Local model couldn't answer",
        "what": "The local model failed or isn't running.",
        "next": "Pick Claude, Codex, or Copilot, or check your local model.",
        "actions": ["switch_model", "retry"],
        "tone": "error",
    },
    "empty": {
        "title": "Empty response",
        "what": "The model returned nothing.",
        "next": "Retry, or pick a different model.",
        "actions": ["retry", "switch_model"],
        "tone": "warning",
    },
}

_FALLBACK_ERROR = {
    "title": "Something went wrong",
    "what": "The request didn't complete.",
    "next": "Retry, or pick a different model.",
    "actions": ["retry", "switch_model", "details"],
    "tone": "error",
}


def error_card(
    status: str, *, model_label: str = "the model", detail: str = ""
) -> dict[str, Any]:
    """A calm, actionable error payload — never a raw stack trace up front."""
    short = (
        str(model_label or "the model").split(" · ")[0].split(" (")[0].strip()
        or "the model"
    )
    spec = _ERRORS.get(status, _FALLBACK_ERROR)
    return {
        "status": status,
        "title": spec["title"].format(model=short),
        "what": spec["what"].format(model=short),
        "next": spec["next"].format(model=short),
        "actions": list(spec["actions"]),
        "tone": spec["tone"],
        # Raw detail is available but the UI hides it behind "Show details".
        "detail": _redact_detail(detail),
    }


def _redact_detail(detail: str) -> str:
    from .provider_contract import dedupe_error_text, redact_secrets

    text = redact_secrets(detail)
    # Never surface obvious secrets even in the dev-details drawer.
    for token in ("--dangerously-skip-permissions",):
        text = text.replace(token, "[flag]")
    return dedupe_error_text(text)[:2000]


def classify_error(text: str) -> str:
    """Best-effort map of a raw error string to a known status."""
    low = str(text or "").lower()
    if "rate limit" in low or "429" in low or "too many requests" in low:
        return "rate_limit"
    if "unauthor" in low or "401" in low or "not logged in" in low or "sign in" in low:
        return "unauthorized"
    if "network" in low or "connection" in low or "timed out reaching" in low:
        return "network"
    if "context" in low and ("large" in low or "exceed" in low or "too long" in low):
        return "context_too_large"
    return "account_error"


# --------------------------------------------------------------------------- #
# Claude stream-json parser — real agent activity from `--output-format stream-json`
# --------------------------------------------------------------------------- #
def _safe_provider_diagnostic(value: Any) -> str:
    from .provider_contract import dedupe_error_text, redact_secrets

    return dedupe_error_text(redact_secrets(value))


class ActivitySession:
    """Per-request stateful emitter for the Calm Stream contract.

    Owns the derived-id state (docs/AI_ACTIVITY_UX.md) so repeated stream
    states coalesce into single rows via the front-end's id-keyed upsert:
    one ``{rid}:connect`` status event per request, one ``{rid}:stream`` row
    updated in place per chunk, and ``{rid}:tool:{seq}`` tool events grouped
    by consecutive tool type. Durations are measured, never synthesized.

    ``request_id`` defaults to a session-minted id — coalescing only needs
    stability within one request (the GUI store is per-request). Threading
    the GUI's real request id through the pipeline lands with #225.
    """

    def __init__(self, request_id: str | None = None) -> None:
        self.request_id = request_id or new_id()
        self._connected = False
        self._stream_started_ms: int | None = None
        self._stream_chars = 0
        self._tool_seq = 0
        self._group_seq = -1
        self._last_tool_type: str | None = None
        # Claude tool_use blocks, keyed by tool_use_id so a later tool_result
        # can correct the optimistic row in place (F19/F11 honesty):
        # tool_use_id -> (event_id, etype, title, detail).
        self._tool_use_index: dict[str, tuple[str, str, str, str | None]] = {}
        # File paths whose Edit/Write tool_use was denied by the provider's
        # permission system (F26): the bridge returns these so the GUI can
        # offer an in-context "Allow edits once" approval instead of a
        # dead-end prose plea. Order-preserving, deduplicated on read.
        self.edit_denials: list[str] = []
        # Normalized Bash command strings -> invocation count, for the
        # repeated-command warning (F13).
        self._command_counts: dict[str, int] = {}
        # Open Codex items: event_id -> (started_ms, etype, title, detail).
        self._codex_open: dict[str, tuple[int, str, str, str | None]] = {}
        self._codex_execs: list[str] = []  # FIFO of open legacy exec ids
        self._codex_exec_seq = 0

    def parse_claude_line(self, line: str) -> dict[str, Any]:
        """Session-aware twin of :func:`parse_claude_line` (stable ids)."""
        return _parse_claude_line(line, self)

    def parse_codex_line(self, line: str) -> dict[str, Any]:
        """Session-aware twin of :func:`parse_codex_line` (stable ids)."""
        return _parse_codex_line(line, self)

    def _connect_event(self, title: str) -> dict[str, Any] | None:
        if self._connected:
            return None  # announce once per request, never per system line
        self._connected = True
        return make_event(
            "provider_request",
            "success",
            title,
            event_id=derived_id(self.request_id, "connect"),
            request_id=self.request_id,
            channel="status",
        )

    def _stream_event(self, chunk: str) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        if self._stream_started_ms is None:
            self._stream_started_ms = now_ms
        self._stream_chars += len(chunk)
        elapsed_s = max(0, (now_ms - self._stream_started_ms) // 1000)
        detail = (
            f"{self._stream_chars:,} chars · {elapsed_s // 60:02d}:{elapsed_s % 60:02d}"
        )
        return make_event(
            "streaming",
            "running",
            "Streaming response",
            detail=detail,
            event_id=derived_id(self.request_id, "stream"),
            request_id=self.request_id,
        )

    def _finish_stream(self, ok: bool) -> dict[str, Any] | None:
        if self._stream_started_ms is None:
            return None  # nothing streamed -> no row to close
        duration_ms = max(0, int(time.time() * 1000) - self._stream_started_ms)
        return make_event(
            "streaming",
            "success" if ok else "error",
            "Response received" if ok else "Streaming interrupted",
            detail=f"{self._stream_chars:,} chars",
            event_id=derived_id(self.request_id, "stream"),
            request_id=self.request_id,
            duration_ms=duration_ms,
        )

    def _tool(self, block: dict[str, Any]) -> dict[str, Any]:
        event = _tool_event(block)
        if event["type"] != self._last_tool_type:
            self._group_seq += 1
            self._last_tool_type = event["type"]
        event["id"] = derived_id(self.request_id, f"tool:{self._tool_seq}")
        event["requestId"] = self.request_id
        event["group"] = f"{self.request_id}:g{self._group_seq}"
        self._tool_seq += 1
        tool_use_id = str(block.get("id") or "").strip()
        if tool_use_id:
            self._tool_use_index[tool_use_id] = (
                event["id"],
                str(event["type"]),
                str(event["title"]),
                event.get("detail"),
            )
        # F13: a Bash command identical to one already run this request is
        # flagged, not silently celebrated a second time.
        name = str(block.get("name") or "").strip().lower()
        if name == "bash":
            command = " ".join(
                str((block.get("input") or {}).get("command") or "").split()
            )
            if command:
                count = self._command_counts.get(command, 0) + 1
                self._command_counts[command] = count
                if count >= 2:
                    event["status"] = "warning"
                    event["title"] = (
                        f"Repeated command — same input already ran: "
                        f"{event.get('detail') or command[:200]}"
                    )
                    event["metadata"]["repeated"] = True
        return event

    def _tool_result(self, block: dict[str, Any]) -> dict[str, Any] | None:
        """Correct the optimistic tool_use row when its result contradicts it.

        Every tool_use is rendered optimistically; the tool_result that follows
        is the truth.  An ``is_error`` result flips the row to ``error`` (same
        derived id, so the front-end upserts in place); a successful but empty
        result flips it to ``warning`` — a green check for a command that
        returned nothing usable is how "✓ Ran" lies happened (F19/F11).
        """
        correction = _tool_result_correction(block)
        if correction is None:
            return None
        status, suffix, detail = correction
        tool_use_id = str(block.get("tool_use_id") or "").strip()
        prior = self._tool_use_index.get(tool_use_id)
        # F26: an Edit/Write refused by the provider's permission gate is an
        # approval request, not just a failed row. Record the file path so the
        # runner can surface an actionable in-context approval.
        if prior is not None and status == "error" and prior[1] == "file_edit":
            result_text = _tool_result_text(block.get("content"))
            if _is_edit_permission_denial(result_text):
                path = (prior[3] or "").strip()
                if path and path not in self.edit_denials:
                    self.edit_denials.append(path)
        if prior is not None:
            event_id, etype, title, prior_detail = prior
            return make_event(
                etype,
                status,
                f"{title} — {suffix}",
                detail=detail or prior_detail,
                event_id=event_id,
                request_id=self.request_id,
            )
        # Unknown tool_use_id (schema drift or a pruned index): still surface
        # the failure honestly as a standalone row rather than dropping it.
        return make_event(
            "command_complete",
            status,
            f"Command {suffix}",
            detail=detail,
            request_id=self.request_id,
        )

    def _codex_item(
        self,
        kind: str,
        etype: str,
        title: str,
        detail: str | None,
        item_id: str,
        item: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One row per Codex item: started opens it, completed closes it."""
        event_id = derived_id(self.request_id, f"codex:{item_id}")
        now_ms = int(time.time() * 1000)
        if kind == "item.started":
            self._codex_open[event_id] = (now_ms, etype, title, detail)
            return make_event(
                etype,
                "running",
                title,
                detail=detail,
                event_id=event_id,
                request_id=self.request_id,
            )
        opened = self._codex_open.pop(event_id, None)
        duration_ms = max(0, now_ms - opened[0]) if opened else None
        # F11: a completed command is only green when its exit code and output
        # actually back that up.
        status, suffix = ("success", "")
        if etype == "command_run" and item is not None:
            status, suffix = _codex_completion(item)
        return make_event(
            etype,
            status,
            f"{title} — {suffix}" if suffix else title,
            detail=detail,
            event_id=event_id,
            request_id=self.request_id,
            duration_ms=duration_ms,
        )

    def _codex_exec(
        self, begin: bool, title: str, detail: str | None, msg: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Legacy exec_command begin/end pairs coalesce FIFO (no item ids)."""
        now_ms = int(time.time() * 1000)
        if begin:
            self._codex_exec_seq += 1
            event_id = derived_id(self.request_id, f"codex:exec:{self._codex_exec_seq}")
            self._codex_execs.append(event_id)
            self._codex_open[event_id] = (now_ms, "command_run", title, detail)
            return make_event(
                "command_run",
                "running",
                title,
                detail=detail,
                event_id=event_id,
                request_id=self.request_id,
            )
        status, suffix = ("success", "")
        if msg is not None:
            status, suffix = _codex_completion(msg)
        honest_title = f"{title} — {suffix}" if suffix else title
        if self._codex_execs:
            event_id = self._codex_execs.pop(0)
            opened = self._codex_open.pop(event_id, None)
            duration_ms = max(0, now_ms - opened[0]) if opened else None
            return make_event(
                "command_run",
                status,
                honest_title,
                detail=detail,
                event_id=event_id,
                request_id=self.request_id,
                duration_ms=duration_ms,
            )
        # An end with no open begin (schema drift): standalone row, honest.
        return make_event(
            "command_run", status, honest_title, detail=detail, request_id=self.request_id
        )

    def _codex_fail_open(self) -> list[dict[str, Any]]:
        """A failed turn flips every still-running item to error — never
        leave a row spinning after the provider gave up."""
        now_ms = int(time.time() * 1000)
        events = []
        for event_id, (started_ms, etype, title, detail) in self._codex_open.items():
            events.append(
                make_event(
                    etype,
                    "error",
                    title,
                    detail=detail,
                    event_id=event_id,
                    request_id=self.request_id,
                    duration_ms=max(0, now_ms - started_ms),
                )
            )
        self._codex_open.clear()
        self._codex_execs.clear()
        return events


def parse_claude_line(line: str) -> dict[str, Any]:
    """Parse one JSONL line from `claude -p --output-format stream-json --verbose`.

    Returns ``{"events": [...], "text": "<delta>", "error": "<diagnostic>",
    "cost": <float|None>, "done": bool}``. Tolerant: unknown/blank lines yield
    an empty delta so a format drift degrades to "no rich events" rather than
    crashing.

    Stateless v1 behavior (one event per line, random ids) — kept for
    backward compatibility. The stream path uses :class:`ActivitySession`
    so repeated states coalesce.
    """
    return _parse_claude_line(line, None)


def _parse_claude_line(line: str, session: ActivitySession | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "events": [],
        "text": "",
        "error": "",
        "cost": None,
        "done": False,
    }
    line = (line or "").strip()
    if not line:
        return out
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        # Not JSON — treat as a raw text delta (best effort).
        out["text"] = line
        return out
    kind = obj.get("type")
    if kind == "system":
        model = (obj.get("model") or "").strip()
        title = f"Connected to Claude{f' · {model}' if model else ''}"
        if session is None:
            out["events"].append(make_event("provider_request", "success", title))
        else:
            connect = session._connect_event(title)
            if connect is not None:
                out["events"].append(connect)
        return out
    if kind == "assistant":
        content = ((obj.get("message") or {}).get("content")) or []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                out["text"] += block.get("text") or ""
            elif btype == "tool_use":
                out["events"].append(
                    _tool_event(block) if session is None else session._tool(block)
                )
        if out["text"]:
            out["events"].append(
                make_event("streaming", "running", "Streaming response")
                if session is None
                else session._stream_event(out["text"])
            )
        return out
    if kind == "user":
        # tool_result blocks arrive inside user messages. They are the truth
        # behind the optimistic tool_use rows: parse them so failures and
        # empty outputs correct the timeline instead of staying "✓ Ran".
        content = ((obj.get("message") or {}).get("content")) or []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if str(block.get("type") or "") != "tool_result":
                    continue
                event = (
                    _tool_result_event(block)
                    if session is None
                    else session._tool_result(block)
                )
                if event is not None:
                    out["events"].append(event)
        return out
    if kind == "result":
        cost = obj.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            out["cost"] = float(cost)
        subtype = str(obj.get("subtype") or "")
        is_error = obj.get("is_error") is True or subtype.startswith("error_")
        if is_error:
            error = obj.get("error")
            if isinstance(error, dict):
                error = error.get("message")
            out["error"] = _safe_provider_diagnostic(
                obj.get("result") or error or subtype or "error"
            )
            out["done"] = True
            if session is not None:
                interrupted = session._finish_stream(ok=False)
                if interrupted is not None:
                    out["events"].append(interrupted)
            return out
        # `result` also carries the final text when not captured incrementally.
        if not out["text"] and obj.get("result"):
            out["text"] = str(obj.get("result"))
        out["done"] = True
        if session is not None:
            finished = session._finish_stream(ok=True)
            if finished is not None:
                out["events"].append(finished)
        return out
    return out


def _tool_event(block: dict[str, Any]) -> dict[str, Any]:
    name = str(block.get("name") or "tool").strip()
    etype, verb = _TOOL_MAP.get(name.lower(), ("tool_call", f"Used {name}"))
    inp = block.get("input") or {}
    target = (
        inp.get("file_path")
        or inp.get("path")
        or inp.get("command")
        or inp.get("pattern")
        or inp.get("query")
        or ""
    )
    detail = str(target)[:200]
    title = f"{verb}: {detail}" if detail else verb
    return make_event(etype, "success", title, detail=detail, metadata={"tool": name})


def _tool_result_text(content: Any) -> str:
    """Flatten a tool_result content payload (string or content blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if str(item.get("type") or "") == "text":
                    parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""


# Provider permission-gate phrasings for a denied Edit/Write (F26). The claude
# CLI words it "Claude requested permissions to write to X, but you haven't
# granted it yet"; keep the markers narrow so ordinary tool errors (syntax,
# missing file) never masquerade as approval requests.
_EDIT_DENIAL_MARKERS = (
    "requested permissions",
    "haven't granted",
    "has not been granted",
    "permission to edit",
    "permission to write",
)


def _is_edit_permission_denial(text: str) -> bool:
    """True when a tool_result error text is a permission denial, not a bug."""
    low = str(text or "").lower()
    return any(marker in low for marker in _EDIT_DENIAL_MARKERS)


def _friendly_tool_error(text: str) -> str | None:
    """Rewrite a cryptic provider-CLI diagnostic into actionable guidance.

    The Claude Code CLI's own bash parser rejects a command over ~965 bytes as
    "malformed syntax that cannot be parsed / too long for parsing" — an
    external limit OPai cannot raise (QA pass-2 F25). Rather than surface the
    raw diagnostic, tell the user the concrete workaround. Returns ``None``
    when no rewrite applies (the raw text is used unchanged).
    """
    low = str(text or "").lower()
    if ("too long for parsing" in low or "maximum supported length" in low) or (
        "cannot be parsed" in low and "command" in low
    ):
        return (
            "Command exceeded the provider CLI's parser limit (~965 bytes). "
            "Use the Edit/Write tools instead of embedding file contents in a "
            "shell command, or split it into smaller commands."
        )
    return None


def _tool_result_correction(
    block: dict[str, Any],
) -> tuple[str, str, str | None] | None:
    """``(status, suffix, detail)`` when a tool_result contradicts the green row.

    Returns ``None`` for a successful result with real output — the optimistic
    row was honest and needs no correction.  ``is_error`` flips to ``error``;
    success with empty/whitespace output flips to ``warning`` (F19: an empty
    ``gh issue view`` must not stay "✓ Ran").
    """
    is_error = block.get("is_error") is True
    text = _tool_result_text(block.get("content"))
    if not is_error and text.strip():
        return None
    if is_error:
        if text.strip():
            # F25: surface a clear workaround for the CLI parser-length limit
            # instead of the raw "malformed syntax" diagnostic.
            friendly = _friendly_tool_error(text)
            detail = friendly or _safe_provider_diagnostic(text)[:200]
        else:
            detail = None
        return ("error", "failed", detail or None)
    return ("warning", "no output returned", None)


def _tool_result_event(block: dict[str, Any]) -> dict[str, Any] | None:
    """Stateless corrective event for a tool_result (v1 one-event-per-line)."""
    correction = _tool_result_correction(block)
    if correction is None:
        return None
    status, suffix, detail = correction
    return make_event("command_complete", status, f"Command {suffix}", detail=detail)


def _codex_completion(item: dict[str, Any]) -> tuple[str, str]:
    """Honest terminal ``(status, title-suffix)`` for a completed command item.

    A non-zero exit code is an error; a zero exit with no captured output is a
    warning; anything unverifiable (no exit code field at all) is left alone
    rather than guessed.
    """
    exit_code = item.get("exit_code", item.get("exitCode"))
    output = item.get("aggregated_output", item.get("output", item.get("stdout")))
    if isinstance(exit_code, bool) or not isinstance(exit_code, (int, float)):
        return ("success", "")
    if int(exit_code) != 0:
        return ("error", f"failed (exit {int(exit_code)})")
    if output is not None and not str(output).strip():
        return ("warning", "no output returned")
    return ("success", "")


def parse_claude_stream(lines: list[str]) -> dict[str, Any]:
    """Aggregate a Claude stream into ``{events, text, error, cost, done}``."""
    events: list[dict[str, Any]] = []
    text = ""
    error = ""
    cost = None
    done = False
    for line in lines:
        part = parse_claude_line(line)
        events.extend(part["events"])
        if part["text"] and not (part["done"] and text):
            text += part["text"]
        error = part["error"] or error
        if part["cost"] is not None:
            cost = part["cost"]
        done = done or part["done"]
    return {
        "events": events,
        "text": text,
        "error": error,
        "cost": cost,
        "done": done,
    }


# --------------------------------------------------------------------------- #
# Codex `exec --json` parser — tolerant JSONL → activity events (#106)
# --------------------------------------------------------------------------- #
# Codex emits one JSON event per line. Item kinds map to typed activity rows;
# ONLY completed agent_message items contribute text (never deltas), so text is
# never duplicated with the --output-last-message fallback. Unknown shapes are
# skipped silently: a schema drift degrades to "text from the out-file" exactly
# as before, never a crash and never invented events.
_CODEX_ITEM_MAP = {
    "command_execution": ("command_run", "Ran command"),
    "local_shell_call": ("command_run", "Ran command"),
    "file_change": ("file_edit", "Changed files"),
    "patch_apply": ("file_edit", "Applied patch"),
    "apply_patch": ("file_edit", "Applied patch"),
    "web_search": ("context_read", "Web search"),
    "mcp_tool_call": ("tool_call", "Used tool"),
    "tool_call": ("tool_call", "Used tool"),
}


def parse_codex_line(line: str) -> dict[str, Any]:
    """Parse one JSONL line from ``codex exec --json``.

    Returns the same shape as :func:`parse_claude_line`:
    ``{"events": [...], "text": str, "error": str, "cost": None,
    "done": bool}``. Codex does not report dollar cost, so ``cost`` is always
    ``None`` (honest, not zero).

    Stateless v1 behavior (one event per line, random ids) — kept for
    backward compatibility. The stream path uses :class:`ActivitySession`
    so ``item.started``/``item.completed`` coalesce into one row.
    """
    return _parse_codex_line(line, None)


def _parse_codex_line(line: str, session: ActivitySession | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "events": [],
        "text": "",
        "error": "",
        "cost": None,
        "done": False,
    }
    raw = (line or "").strip()
    if not raw:
        return out
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # codex --json stdout should be JSON; anything else is CLI noise, not
        # answer text — skip it rather than polluting the reply.
        return out
    if not isinstance(obj, dict):
        return out
    kind = str(obj.get("type") or "")
    if kind in {"thread.started", "session.created", "session_configured"}:
        if session is None:
            out["events"].append(
                make_event("provider_request", "success", "Connected to Codex")
            )
        else:
            connect = session._connect_event("Connected to Codex")
            if connect is not None:
                out["events"].append(connect)
        return out
    if kind in {"turn.completed", "turn_complete"}:
        out["done"] = True
        return out
    if kind in {"turn.failed", "error"}:
        error = obj.get("error")
        if isinstance(error, dict):
            error = error.get("message")
        message = _safe_provider_diagnostic(obj.get("message") or error or "error")
        out["error"] = message
        out["done"] = True
        if session is not None:
            out["events"].extend(session._codex_fail_open())
        out["events"].append(
            make_event("error", "error", f"Codex reported: {message[:200]}")
        )
        return out
    item = obj.get("item")
    if isinstance(item, dict) and kind.startswith("item."):
        item_type = str(item.get("type") or item.get("item_type") or "")
        if item_type == "agent_message":
            # Text only on completion — deltas are never accumulated, so the
            # out-file fallback can never double the answer.
            if kind == "item.completed":
                out["text"] = str(item.get("text") or "")
            return out
        if item_type == "reasoning":
            return out
        mapped = _CODEX_ITEM_MAP.get(item_type)
        if mapped and kind in {"item.started", "item.completed"}:
            etype, verb = mapped
            target = str(
                item.get("command")
                or item.get("cmd")
                or item.get("path")
                or item.get("query")
                or ""
            )[:200]
            title = f"{verb}: {target}" if target else verb
            item_id = str(item.get("id") or "")
            if session is not None and item_id:
                out["events"].append(
                    session._codex_item(
                        kind, etype, title, target or None, item_id, item
                    )
                )
            else:
                # No session or no item id: v1 one-event-per-line behavior.
                if kind == "item.completed":
                    status, suffix = (
                        _codex_completion(item)
                        if etype == "command_run"
                        else ("success", "")
                    )
                    title = f"{title} — {suffix}" if suffix else title
                else:
                    status = "running"
                out["events"].append(
                    make_event(etype, status, title, detail=target or None)
                )
        return out
    # Older proto-style shapes: {"msg": {"type": "exec_command_begin", ...}}.
    msg = obj.get("msg")
    if isinstance(msg, dict):
        mtype = str(msg.get("type") or "")
        if mtype in {"exec_command_begin", "exec_command_end"}:
            command = msg.get("command")
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            target = str(command or "")[:200]
            title = f"Ran command: {target}" if target else "Ran command"
            if session is not None:
                out["events"].append(
                    session._codex_exec(
                        mtype == "exec_command_begin", title, target or None, msg
                    )
                )
            else:
                if mtype.endswith("end"):
                    status, suffix = _codex_completion(msg)
                    title = f"{title} — {suffix}" if suffix else title
                else:
                    status = "running"
                out["events"].append(
                    make_event("command_run", status, title, detail=target or None)
                )
        elif mtype == "task_complete":
            out["done"] = True
    return out
