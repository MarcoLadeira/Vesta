"""Inline capture: route an agent's call through OPai automatically.

This is the engine behind the agent shim (#91, Epic A #85). Instead of running
``claude`` / ``codex`` / ``copilot`` directly, the installed wrapper calls this
proxy, which:

  1. **gates** destructive requests *before* any paid call (every mode except
     Full Auto), so OPai blocks risk before spending money;
  2. **routes** the task through the connected account runner; and
  3. **records** the call to the local ledger with its real cost (honest spend).

It **fails open**: if OPai's own logic errors, the raw agent still runs so a
developer is never blocked by the firewall. The per-agent seam (``SUPPORTED_AGENTS``
+ ``_resolve_runner``) keeps Cursor/Cline addable later without touching callers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

SUPPORTED_AGENTS = ("claude", "codex", "copilot")
_EDIT_MODES = {"safe-auto", "full-auto"}


def _blocked(agent: str, reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "agent": agent,
        "captured": True,
        "paid": False,
        "reason": reason,
        "answer": (
            "OPai stopped this before running it because it looks risky"
            + (f": {reason}." if reason else ".")
            + "\nRe-run in Full Auto only if you intend that."
        ),
    }


def _resolve_runner(agent: str, model: str | None, runner: Any) -> Any:
    """Return an injected runner, or build one for a connected account (or None)."""
    if runner is not None:
        return runner
    from .accounts import runner_for_account

    return runner_for_account(agent, model=model)


def _fail_open(
    project_root: Path,
    task: str,
    agent: str,
    model: str | None,
    mode: str | None,
    runner: Any,
    error: Exception,
) -> dict[str, Any]:
    """OPai's own path failed — run the raw agent so the dev is never blocked.

    Marked ``captured=False`` because routing/recording was bypassed; the answer
    still comes back. If even the raw runner is unavailable, surface a clean error.
    """
    try:
        run = _resolve_runner(agent, model, runner)
        if run is None or not run.available():
            return {
                "status": "fail_open_unavailable",
                "agent": agent,
                "captured": False,
                "paid": False,
                "answer": (
                    f"OPai couldn't route this and {agent} isn't connected. "
                    f"Connect {agent} and try again."
                ),
                "error": str(error),
            }
        allow_edits = (mode or "ask") in _EDIT_MODES
        result = run.complete(
            task, project_root=project_root, allow_edits=allow_edits, mode=mode
        )
        text = result.get("text") if isinstance(result, dict) else str(result)
        return {
            "status": "fail_open",
            "agent": agent,
            "captured": False,
            "paid": True,
            "answer": text or "(no output)",
            "error": str(error),
        }
    except Exception as exc:  # noqa: BLE001 - fail-open must never raise
        return {
            "status": "fail_open_error",
            "agent": agent,
            "captured": False,
            "paid": False,
            "answer": f"{agent} couldn't run that. Try again.",
            "error": f"{error} / {exc}",
        }


def proxy_run(
    project_root: Path,
    task: str,
    *,
    agent: str,
    model: str | None = None,
    mode: str | None = None,
    runner: Any = None,
) -> dict[str, Any]:
    """Route one agent call through OPai. The single entrypoint for the shim.

    Returns a result dict that always carries ``captured`` (whether OPai routed
    and recorded the call) and ``agent``. ``runner`` is injectable for tests.
    """
    root = project_root.expanduser().resolve()
    agent = (agent or "").strip().lower()
    mode = mode or "ask"
    capture_id = uuid4().hex

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        """Attach and persist one terminal capture event without blocking work."""
        result["capture_id"] = capture_id
        status = str(result.get("status") or "unknown")
        outcome = {
            "answered_by_account": "completed",
            "account_error": "failed",
            "account_timeout": "timeout",
            "fail_open_unavailable": "fail_open_unavailable",
            "fail_open_error": "fail_open_error",
            "unsupported_agent": "unsupported",
        }.get(status, status)
        try:
            from .ledger import record_capture_session

            record_capture_session(
                root,
                task,
                capture_id=capture_id,
                agent=agent,
                mode=mode,
                model=str(result.get("model") or model or ""),
                outcome=outcome,
                captured=bool(result.get("captured")),
                paid=bool(result.get("paid")),
                spend_accounted=bool(result.get("ledger_recorded")),
            )
        except Exception as exc:  # noqa: BLE001 - capture must fail open too
            result["capture_warning"] = (
                f"capture ledger unavailable: {type(exc).__name__}"
            )
        return result

    if agent not in SUPPORTED_AGENTS:
        return finish(
            {
                "status": "unsupported_agent",
                "agent": agent,
                "captured": False,
                "paid": False,
                "answer": (
                    f"OPai can route {', '.join(SUPPORTED_AGENTS)} today, not '{agent}'."
                ),
            }
        )

    # 1) Safety gate BEFORE any paid call. safety_warnings only fires in non
    #    Full-Auto modes, so Full Auto is the single opt-in to skip the gate.
    try:
        from .intent_router import safety_warnings

        warnings = safety_warnings(root, task, mode=mode)
    except Exception:
        warnings = []
    if warnings and mode != "full-auto":
        return finish(_blocked(agent, str(warnings[0].get("reason", ""))))

    # 2) Route through OPai's account path: classify, run, and record real spend.
    #    _ask_account already does honest cost accounting (#90) and never raises
    #    for ordinary CLI failures; we still wrap it so any surprise fails open.
    try:
        from opai import app_state as A

        result = A._ask_account(
            root,
            task,
            agent,
            model=model,
            allow_edits=mode in _EDIT_MODES,
            runner=runner,
            mode=mode,
        )
        if not isinstance(result, dict):
            raise TypeError("account path returned a non-dict result")
        if result.get("status") == "failed":
            error = result.get("error")
            error_code = error.get("code") if isinstance(error, dict) else ""
            result["status"] = (
                "account_timeout"
                if error_code == "PROVIDER_TIMEOUT"
                else "account_error"
            )
        result.setdefault("agent", agent)
        result["captured"] = True
        return finish(result)
    except Exception as exc:  # noqa: BLE001 - degrade to fail-open, never crash
        return finish(_fail_open(root, task, agent, model, mode, runner, exc))
