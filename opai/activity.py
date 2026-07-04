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
    "completion",
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


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def make_event(
    event_type: str,
    status: str,
    title: str,
    *,
    detail: str | None = None,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Build one activity event. ``timestamp`` is ms since epoch."""
    if event_type not in TYPES:
        event_type = "tool_call"
    if status not in STATUSES:
        status = "running"
    return {
        "id": event_id or new_id(),
        "type": event_type,
        "status": status,
        "title": title,
        "detail": detail,
        "timestamp": int(time.time() * 1000),
        "durationMs": duration_ms,
        "metadata": metadata or {},
    }


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


def parse_claude_line(line: str) -> dict[str, Any]:
    """Parse one JSONL line from `claude -p --output-format stream-json --verbose`.

    Returns ``{"events": [...], "text": "<delta>", "error": "<diagnostic>",
    "cost": <float|None>, "done": bool}``. Tolerant: unknown/blank lines yield
    an empty delta so a format drift degrades to "no rich events" rather than
    crashing.
    """
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
        out["events"].append(make_event("provider_request", "success", title))
        return out
    if kind == "assistant":
        content = ((obj.get("message") or {}).get("content")) or []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                out["text"] += block.get("text") or ""
            elif btype == "tool_use":
                out["events"].append(_tool_event(block))
        if out["text"]:
            out["events"].append(
                make_event("streaming", "running", "Streaming response")
            )
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
            return out
        # `result` also carries the final text when not captured incrementally.
        if not out["text"] and obj.get("result"):
            out["text"] = str(obj.get("result"))
        out["done"] = True
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
    """
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
        out["events"].append(
            make_event("provider_request", "success", "Connected to Codex")
        )
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
            status = "success" if kind == "item.completed" else "running"
            title = f"{verb}: {target}" if target else verb
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
            status = "success" if mtype.endswith("end") else "running"
            target = str(command or "")[:200]
            out["events"].append(
                make_event(
                    "command_run",
                    status,
                    f"Ran command: {target}" if target else "Ran command",
                    detail=target or None,
                )
            )
        elif mtype == "task_complete":
            out["done"] = True
    return out
