"""Connected AI accounts - route through the CLIs you're already logged into.

OPai's "connect your Claude/Codex/Copilot account" does not handle API keys or
credentials. It detects the AI CLIs you already authenticated (`claude`,
`codex`, `copilot`) and runs a task through them on demand. Detection is
presence-based (an auth file on disk or token env var + the CLI on PATH);
execution shells out to the CLI in a read-only answer mode by default, with an
explicit opt-in for edits.

These are paid/cloud calls (they spend your subscription), so OPai treats them
as the gated tier: never auto-fired by Auto routing, recorded to the ledger,
and blocked under panic mode. Nothing here runs unless the user picks the
account model and sends.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import shutil
import subprocess  # nosec B404 - we invoke the user's own logged-in AI CLIs
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from opai.model_registry import models_for as _models_for

from .command_runner import redact
from .proc import provider_child_env
from .process_tree import isolated_group_kwargs, terminate_tree

_CONNECTION_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CONNECTION_CACHE_LOCK = threading.RLock()
_CONNECTION_CACHE_TTL = 300.0
_CONNECTION_HISTORY: dict[tuple[str, str], dict[str, Any]] = {}
_CONNECTION_HISTORY_LIMIT = 64
# Capability classifications come from an auth-status check.  They must not
# outlive the check indefinitely because a user can switch Codex sign-in modes
# without removing the local auth artifact.
_ACCOUNT_TYPE_HISTORY_TTL_MS = int(_CONNECTION_CACHE_TTL * 1000)
_CLI_VERSION_CACHE: dict[str, str] = {}
_CLI_CAPABILITY_CACHE: dict[str, bool] = {}
_CODEX_CURRENT_DEFAULT_MIN_VERSION = (0, 143, 0)

# What a provider CLI can do is a property of the *machine*, not of one OPai
# process. Keeping the answer only in the in-process caches above meant the
# model picker enumerated an unknown CLI as fully capable on every cold start:
# Codex looked selectable, the user picked it, and the run hard-failed with
# "requires a newer version of Codex". Whether it looked available depended on
# whether some earlier code path in that same process had happened to probe —
# which is exactly the "sometimes it works, sometimes it doesn't" experience.
#
# So the verdict is persisted next to OPai's other machine-scoped state, keyed
# by the executable's identity (path + size + mtime). An upgrade changes that
# identity and invalidates the entry immediately; otherwise the entry is
# trusted for _CLI_PROBE_TTL_SECONDS. Steady state costs one small file read;
# only a first launch or a freshly changed binary pays for a subprocess.
_CLI_PROBE_TTL_SECONDS = 6 * 3600.0
_CLI_PROBE_LOCK = threading.RLock()

_INVALID_CODEX_TIER = 'service_tier = "default"'

_AUTH_FAILURE_CODES = {"AUTH_MISSING", "AUTH_INVALID", "AUTH_EXPIRED"}

_LOGIN_ARGV: dict[str, list[str]] = {
    "claude": ["auth", "login"],
    "codex": ["login"],
    "copilot": ["login"],
}


def _connection_key(account_id: str, home: Path | None = None) -> tuple[str, str]:
    return account_id, str((home or Path.home()).expanduser().resolve())


def _safe_connection_summary(result: dict[str, Any]) -> dict[str, Any]:
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    safe_error = {
        key: error[key]
        for key in (
            "code",
            "title",
            "userMessage",
            "authStatus",
            "provider",
            "recoveryActions",
        )
        if key in error
    }
    return {
        key: value
        for key, value in {
            "providerId": result.get("providerId"),
            "displayName": result.get("displayName"),
            "authStatus": result.get("authStatus"),
            "credentialSource": result.get("credentialSource"),
            "accountType": result.get("accountType"),
            "lastCheckedAt": result.get("lastCheckedAt"),
            "lastError": result.get("lastError"),
            "lastErrorCode": result.get("lastErrorCode"),
            "error": safe_error or None,
            "safeDiagnostic": result.get("safeDiagnostic"),
            "cliPresent": result.get("cliPresent"),
            "detected": result.get("detected"),
            "loginHint": result.get("loginHint"),
            "envOverridesRemoved": list(result.get("envOverridesRemoved") or []),
        }.items()
        if value is not None
    }


def _remember_connection(
    account_id: str, result: dict[str, Any], *, home: Path | None = None
) -> dict[str, Any]:
    with _CONNECTION_CACHE_LOCK:
        key = _connection_key(account_id, home)
        _CONNECTION_HISTORY.pop(key, None)
        _CONNECTION_HISTORY[key] = _safe_connection_summary(result)
        while len(_CONNECTION_HISTORY) > _CONNECTION_HISTORY_LIMIT:
            _CONNECTION_HISTORY.pop(next(iter(_CONNECTION_HISTORY)))
    return result


def _invalidate_cache_for_error(account_id: str, error: dict[str, Any]) -> None:
    if str(error.get("code") or "") in _AUTH_FAILURE_CODES:
        invalidate_connection_cache(account_id)
        account = next(
            (item for item in list_connected_accounts() if item["id"] == account_id),
            {
                "id": account_id,
                "label": account_id.capitalize(),
                "cli_present": False,
                "authenticated": False,
            },
        )
        _remember_connection(
            account_id,
            connection_for_account(
                account,
                auth_status=str(error.get("authStatus") or "invalid"),
                last_checked_at=int(time.time() * 1000),
                error=error,
            ),
        )


def codex_config_issue(home: Path | None = None) -> dict[str, Any]:
    """Describe the one known-invalid Codex service tier without reading secrets."""

    path = (home or Path.home()).expanduser() / ".codex" / "config.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    matches = [
        index + 1
        for index, line in enumerate(lines)
        if line.strip().split("#", 1)[0].strip() == _INVALID_CODEX_TIER
    ]
    return {
        "provider": "codex",
        "repairable": len(matches) == 1,
        "code": "CODEX_INVALID_SERVICE_TIER" if matches else None,
        "line": matches[0] if len(matches) == 1 else None,
        "path": str(path),
        "message": (
            "Codex service_tier 'default' is invalid. Remove that assignment to use "
            "Codex's standard default."
            if matches
            else None
        ),
    }


def repair_codex_config(home: Path | None = None) -> dict[str, Any]:
    """Back up config.toml and remove only the exact invalid tier assignment."""

    issue = codex_config_issue(home)
    if not issue["repairable"]:
        raise ValueError("No uniquely repairable Codex service tier was found")
    path = Path(issue["path"])
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    kept = [
        line
        for line in lines
        if line.strip().split("#", 1)[0].strip() != _INVALID_CODEX_TIER
    ]
    backup = path.with_name(f"{path.name}.bak-{int(time.time())}")
    shutil.copy2(path, backup)
    path.write_text("".join(kept), encoding="utf-8")
    return {
        "provider": "codex",
        "repaired": True,
        "path": str(path),
        "backupPath": str(backup),
    }


def _hidden_run(
    cmd: list[str],
    *,
    cwd: str | None,
    timeout: float,
    env: dict[str, str] | None = None,
):
    """Run a CLI fully in the background - no console window, no stdin prompt.

    On Windows a GUI app (PySide6) that shells out to a console program pops a
    visible terminal; CREATE_NO_WINDOW suppresses it so the chat stays inline.
    stdin is closed so a CLI never blocks waiting for input, and output is
    decoded as UTF-8 with replacement so odd bytes can't crash the GUI.
    ``env`` (when given) is the sanitized child environment from
    :func:`opaihub.proc.provider_child_env` — parent AI-session variables must
    never leak into a provider CLI OPai owns.
    """
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "stdin": subprocess.DEVNULL,
    }
    if env is not None:
        kwargs["env"] = env
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, **kwargs)  # nosec B603 - argv list, no shell, user's own CLI


def _popen(cmd: list[str], *, cwd: str | None, env: dict[str, str] | None = None):
    """Start a killable, line-buffered CLI process for streaming reads.

    Launched in its own process group/session (#108) so a Stop or window close
    can terminate the entire tree — provider CLIs spawn grandchildren that must
    not survive cancellation and keep spending or mutating the repo.
    """
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "stdin": subprocess.DEVNULL,
        "bufsize": 1,
    }
    if env is not None:
        kwargs["env"] = env
    kwargs.update(isolated_group_kwargs())
    return subprocess.Popen(cmd, **kwargs)  # nosec B603 - argv list, no shell, user's own CLI


def _is_login_sentinel(text: str) -> bool:
    """True when a CLI 'answer' is really its not-signed-in message.

    The claude CLI can exit non-zero yet still emit structured output whose
    ``result`` field is literally "Not logged in · Please run /login". That is
    an auth failure wearing an answer's clothes — rendering it as a normal
    assistant message (and recording a receipt for it) misleads the user.
    """
    t = str(text or "").strip().lower()
    return t.startswith("not logged in") and len(t) <= 120


def _terminate(proc: Any) -> None:
    """Stop a running process *and its whole tree* (#108): grandchildren spawned
    by a provider CLI must not survive Stop and keep spending or mutating the
    repo. Delegates to the platform-aware, idempotent tree killer."""
    terminate_tree(proc)


def _terminate_async(proc: Any, *, after: Callable[[], None] | None = None) -> None:
    """Start best-effort tree cleanup without delaying a known terminal result."""

    def _cleanup() -> None:
        try:
            _terminate(proc)
        finally:
            if after is not None:
                after()

    threading.Thread(target=_cleanup, daemon=True).start()


def _process_returncode(proc: Any) -> int:
    """Return a real process code; tolerate lightweight test/provider shims."""

    value = getattr(proc, "returncode", 0)
    return value if isinstance(value, int) else 0


# How to detect a logged-in account and how to drive its CLI non-interactively.
ACCOUNT_SPECS: list[dict[str, Any]] = [
    {
        "id": "claude",
        "label": "Claude",
        "cli": "claude",
        "auth_files": [".claude/.credentials.json", ".claude.json"],
        "vendor": "Anthropic Claude Code",
        "login_hint": "Run `claude` once and sign in to connect your account.",
    },
    {
        "id": "codex",
        "label": "Codex",
        "cli": "codex",
        "auth_files": [".codex/auth.json"],
        "vendor": "OpenAI Codex CLI",
        "login_hint": "Run `codex` once and sign in to connect your account.",
    },
    {
        "id": "copilot",
        "label": "Copilot",
        "cli": "copilot",
        # GitHub Copilot CLI stores its OAuth token in the OS keychain and only
        # falls back to a plaintext config.json on headless hosts, so we also
        # accept the GH token env vars it honours as a "connected" signal.
        "auth_files": [".copilot/config.json"],
        "auth_env": ["GH_TOKEN", "GITHUB_TOKEN", "COPILOT_API_KEY"],
        "vendor": "GitHub Copilot CLI",
        "login_hint": "Run `copilot` once and `/login` to connect your account.",
    },
]


def _which(name: str) -> str | None:
    return shutil.which(name)


def list_connected_accounts(home: Path | None = None) -> list[dict[str, Any]]:
    """Detect which AI accounts are connected via their installed CLIs.

    Connected = the CLI is on PATH *and* an auth file exists. Never reads the
    auth file contents - presence is enough, and OPai must not touch secrets.
    """
    user_home = (home or Path.home()).expanduser()
    accounts: list[dict[str, Any]] = []
    for spec in ACCOUNT_SPECS:
        cli_path = _which(spec["cli"])
        authed = any((user_home / rel).exists() for rel in spec["auth_files"])
        if not authed:
            authed = any(os.environ.get(name) for name in spec.get("auth_env", []))
        accounts.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "vendor": spec["vendor"],
                "cli": spec["cli"],
                "cli_path": cli_path,
                "cli_present": bool(cli_path),
                "authenticated": authed,
                "connected": bool(cli_path and authed),
                "login_hint": spec["login_hint"],
            }
        )
    return accounts


def connection_for_account(
    account: dict[str, Any],
    *,
    auth_status: str | None = None,
    last_checked_at: int | None = None,
    error: dict[str, Any] | None = None,
    env_overrides_removed: list[str] | None = None,
    account_type: str | None = None,
) -> dict[str, Any]:
    """Build the safe connection payload used by Settings and the chat gate.

    An auth-file signal means "detected", not "verified". Only an explicit
    provider status command may promote it to ``connected``.
    """

    cli_present = bool(account.get("cli_present"))
    authenticated = bool(account.get("authenticated"))
    if auth_status is None:
        if authenticated and not cli_present:
            auth_status = "misconfigured"
        elif not authenticated:
            auth_status = "not_configured"
        else:
            auth_status = "unknown"
    if auth_status == "connected":
        diagnostic = "Sign-in verified locally; provider acceptance is confirmed by each request."
    elif auth_status == "unknown":
        diagnostic = "Account sign-in was detected but has not been verified."
    elif auth_status == "not_configured":
        diagnostic = "No provider sign-in was detected."
    elif auth_status == "misconfigured":
        # A provided error (e.g. a broken CLI config file like Codex's invalid
        # service_tier) is far more specific and actionable than the generic
        # "CLI unavailable" — surface it so the user gets the real fix.
        diagnostic = str(
            (error or {}).get("userMessage")
            or "A sign-in was detected, but the provider CLI is unavailable."
        )
    else:
        diagnostic = str(
            (error or {}).get("technicalMessage") or "Connection check failed."
        )
    if env_overrides_removed:
        # Names only — explains why OPai's verdict can differ from a terminal
        # that still carries a parent AI session's variables.
        diagnostic += " Ignored inherited session overrides: " + ", ".join(
            env_overrides_removed
        )
    return {
        "providerId": str(account.get("id") or "unknown"),
        "displayName": str(account.get("label") or account.get("id") or "Provider"),
        "userFacingName": "OPai",
        "authStatus": auth_status,
        "credentialSource": "user_account",
        # Safe, normalized classification from an account status command. It
        # never contains credential material or raw provider output.
        "accountType": str(account_type or "unknown"),
        "lastCheckedAt": last_checked_at,
        "lastError": (error or {}).get("userMessage"),
        "lastErrorCode": (error or {}).get("code"),
        # The full normalized error, so callers keep the classification (and its
        # recovery actions) instead of re-deriving it from the diagnostic text.
        "error": error or None,
        "safeDiagnostic": diagnostic,
        "cliPresent": cli_present,
        "detected": authenticated,
        "loginHint": account.get("login_hint"),
        # Names (never values) of parent-session env vars OPai stripped before
        # probing/running this CLI, so diagnostics can explain why OPai's
        # verdict may differ from a contaminated terminal's.
        "envOverridesRemoved": list(env_overrides_removed or []),
    }


def invalidate_connection_cache(
    account_id: str, *, clear_history: bool = False
) -> None:
    """Drop any cached 'connected' verdicts for this provider.

    ``test_account_connection`` caches a successful check for
    ``_CONNECTION_CACHE_TTL`` (5 minutes) so repeated messages don't re-shell
    out on every send. But a Claude/Codex OAuth session can die *between*
    messages — the cached check said connected, yet the next real completion
    call gets a genuine 401 from the provider. Without invalidation, OPai
    would keep telling the user "connected" (from cache) for up to 5 more
    minutes while every send keeps failing. Call this the moment a live
    completion comes back with an auth-shaped error, so the next check (an
    automatic retry, or the user clicking "Test connection") does a fresh
    probe instead of repeating the stale verdict.
    """
    with _CONNECTION_CACHE_LOCK:
        for key in [k for k in _CONNECTION_CACHE if k[0] == account_id]:
            _CONNECTION_CACHE.pop(key, None)
        if clear_history:
            for key in [k for k in _CONNECTION_HISTORY if k[0] == account_id]:
                _CONNECTION_HISTORY.pop(key, None)


def account_connections(home: Path | None = None) -> list[dict[str, Any]]:
    """Return detected connection state without contacting any provider."""

    return [
        connection_for_account(account) for account in list_connected_accounts(home)
    ]


def _account_type_from_status(account_id: str, detail: str) -> str:
    """Return a safe Codex capability class from status text, never raw text."""

    if account_id != "codex":
        return "unknown"
    normalized_detail = detail.lower()
    if "chatgpt" in normalized_detail:
        return "chatgpt"
    if "api key" in normalized_detail or "api-key" in normalized_detail:
        return "api_key"
    return "unknown"


def test_account_connection(
    account_id: str,
    *,
    home: Path | None = None,
    run: Callable[[list[str]], Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run a provider's local, non-completion auth status command when available."""

    from opai.provider_contract import normalize_provider_error

    cache_key = _connection_key(account_id, home)
    if run is None and not force:
        with _CONNECTION_CACHE_LOCK:
            cached = _CONNECTION_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _CONNECTION_CACHE_TTL:
            return dict(cached[1])

    account = next(
        (item for item in list_connected_accounts(home) if item["id"] == account_id),
        {
            "id": account_id,
            "label": account_id.capitalize(),
            "cli_present": False,
            "authenticated": False,
        },
    )
    detected = connection_for_account(account)
    if detected["authStatus"] in {"not_configured", "misconfigured"}:
        return _remember_connection(account_id, detected, home=home)
    checked_at = int(time.time() * 1000)
    if account_id == "copilot":
        detected["lastCheckedAt"] = checked_at
        detected["safeDiagnostic"] = (
            "Account sign-in was detected; this CLI exposes no safe status command."
        )
        return _remember_connection(account_id, detected, home=home)
    commands = {
        "claude": [account.get("cli_path") or "claude", "auth", "status"],
        "codex": [account.get("cli_path") or "codex", "login", "status"],
    }
    command = commands.get(account_id)
    if command is None:
        detected["lastCheckedAt"] = checked_at
        detected["safeDiagnostic"] = "No safe status command is available."
        return _remember_connection(account_id, detected, home=home)

    # The status probe must run in a SANITIZED environment: inherited parent
    # AI-session variables (CLAUDE_CODE_*, stale ANTHROPIC_/OPENAI_ overrides)
    # make `claude auth status` report the parent's session instead of the
    # CLI's own persisted login — the exact "says connected, then 401s" trap.
    child_env, env_removed = provider_child_env(account_id)
    execute = run or (
        lambda argv: _hidden_run(argv, cwd=None, timeout=15.0, env=child_env)
    )
    try:
        proc = execute(command)
    except (OSError, subprocess.SubprocessError) as exc:
        error = normalize_provider_error(account_id, str(exc))
        return _remember_connection(
            account_id,
            connection_for_account(
                account,
                auth_status="provider_unavailable",
                last_checked_at=checked_at,
                error=error,
                env_overrides_removed=env_removed,
            ),
            home=home,
        )
    returncode = int(getattr(proc, "returncode", 0) or 0)
    detail = "\n".join(
        part.strip()
        for part in (
            str(getattr(proc, "stderr", "") or ""),
            str(getattr(proc, "stdout", "") or ""),
        )
        if part.strip()
    )
    status_error = normalize_provider_error(account_id, detail, returncode=returncode)
    structured_logged_in: bool | None = None
    if account_id == "claude" and returncode == 0:
        try:
            status_payload = json.loads(str(getattr(proc, "stdout", "") or ""))
        except (json.JSONDecodeError, TypeError):
            status_payload = None
        if isinstance(status_payload, dict) and isinstance(
            status_payload.get("loggedIn"), bool
        ):
            structured_logged_in = status_payload["loggedIn"]
            if not structured_logged_in:
                status_error = normalize_provider_error(
                    account_id, "Not logged in", returncode=returncode
                )
    status_failed = structured_logged_in is False or (
        structured_logged_in is None
        and status_error["code"] not in {"UNKNOWN", "NO_RESPONSE"}
    )
    if returncode == 0 and not status_failed:
        account_type = _account_type_from_status(account_id, detail)
        if account_id == "codex":
            cli_version = _account_cli_version(account, run=run)
            if not _codex_cli_supports_current_default(cli_version):
                error = normalize_provider_error(
                    account_id,
                    (
                        f"Codex CLI {cli_version} requires an update. "
                        "The current account-default model requires a newer version "
                        "of the Codex CLI."
                    ),
                )
                return _remember_connection(
                    account_id,
                    connection_for_account(
                        account,
                        auth_status="misconfigured",
                        last_checked_at=checked_at,
                        error=error,
                        env_overrides_removed=env_removed,
                        account_type=account_type,
                    ),
                    home=home,
                )
        result = connection_for_account(
            account,
            auth_status="connected",
            last_checked_at=checked_at,
            env_overrides_removed=env_removed,
            account_type=account_type,
        )
        if run is None:
            with _CONNECTION_CACHE_LOCK:
                _CONNECTION_CACHE[cache_key] = (time.monotonic(), dict(result))
        return _remember_connection(account_id, result, home=home)
    error = status_error
    status = error["authStatus"]
    if status == "unknown":
        status = "disconnected"
    return _remember_connection(
        account_id,
        connection_for_account(
            account,
            auth_status=status,
            last_checked_at=checked_at,
            error=error,
            env_overrides_removed=env_removed,
        ),
        home=home,
    )


def _connection_health(connection: dict[str, Any]) -> str:
    if not connection.get("cliPresent", True):
        return "not_installed"
    return {
        "not_configured": "not_configured",
        "unknown": "detected",
        "connected": "verified",
        "misconfigured": "degraded",
        "provider_unavailable": "degraded",
        "invalid": "failed",
        "expired": "failed",
        "disconnected": "failed",
    }.get(str(connection.get("authStatus") or "unknown"), "degraded")


def _with_connection_history(
    current: dict[str, Any], history: dict[str, Any]
) -> dict[str, Any]:
    """Keep the latest check unless local install/credential evidence changed."""

    if not history:
        return current
    changed = any(
        key in history and current.get(key) != history.get(key)
        for key in ("cliPresent", "detected")
    )
    merged = {**current, **history}
    for key in ("cliPresent", "detected", "loginHint"):
        if key in current:
            merged[key] = current[key]
    if changed:
        for key in ("authStatus", "safeDiagnostic", "error"):
            merged[key] = current.get(key)
    checked_at = history.get("lastCheckedAt")
    try:
        account_type_is_fresh = (
            int(time.time() * 1000) - int(checked_at) <= _ACCOUNT_TYPE_HISTORY_TTL_MS
        )
    except (TypeError, ValueError):
        account_type_is_fresh = False
    if not account_type_is_fresh:
        # Do not retain an API-key capability after the status result that
        # established it has expired.  Unknown falls back to the Codex CLI's
        # own compatible default model.
        merged["accountType"] = str(current.get("accountType") or "unknown")
    return merged


def _cli_probe_store_path(home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser() / ".opai" / "cli_capability.json"


def _cli_fingerprint(cli_path: str) -> str:
    """Identity of the executable at ``cli_path`` — size and mtime, not content.

    A reinstall or upgrade changes at least one of these, which invalidates the
    stored verdict without waiting out the TTL. Unreadable paths return ``""``
    so nothing is trusted for a binary we cannot even stat.
    """
    try:
        stat = Path(cli_path).stat()
    except OSError:
        return ""
    return f"{int(stat.st_size)}:{int(stat.st_mtime)}"


def _read_cli_probe(
    cli_path: str, field: str, *, home: Path | None = None, now: float | None = None
) -> Any:
    """A persisted probe result for this exact binary, or ``None`` if unknown."""
    fingerprint = _cli_fingerprint(cli_path)
    if not fingerprint:
        return None
    path = _cli_probe_store_path(home)
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    entry = store.get(cli_path) if isinstance(store, dict) else None
    if not isinstance(entry, dict) or entry.get("fingerprint") != fingerprint:
        return None
    ts = time.time() if now is None else float(now)
    try:
        checked_at = float(entry.get("checked_at") or 0.0)
    except (TypeError, ValueError):
        return None
    if (ts - checked_at) > _CLI_PROBE_TTL_SECONDS:
        return None
    return entry.get(field)


def _write_cli_probe(
    cli_path: str,
    field: str,
    value: Any,
    *,
    home: Path | None = None,
    now: float | None = None,
) -> None:
    """Persist one probe result. Best-effort — a cache miss is never fatal."""
    fingerprint = _cli_fingerprint(cli_path)
    if not fingerprint:
        return
    path = _cli_probe_store_path(home)
    ts = time.time() if now is None else float(now)
    with _CLI_PROBE_LOCK:
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            store = {}
        if not isinstance(store, dict):
            store = {}
        entry = store.get(cli_path)
        if not isinstance(entry, dict) or entry.get("fingerprint") != fingerprint:
            entry = {"fingerprint": fingerprint}
        entry[field] = value
        entry["checked_at"] = ts
        store[cli_path] = entry
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name, suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(store, sort_keys=True, indent=2) + "\n")
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError:
            return


def _account_cli_version(
    account: dict[str, Any],
    *,
    run: Callable[[list[str]], Any] | None = None,
    home: Path | None = None,
) -> str:
    cli_path = str(account.get("cli_path") or "")
    if not cli_path:
        return ""
    if run is None:
        if cli_path in _CLI_VERSION_CACHE:
            return _CLI_VERSION_CACHE[cli_path]
        stored = _read_cli_probe(cli_path, "version", home=home)
        if isinstance(stored, str):
            _CLI_VERSION_CACHE[cli_path] = stored
            return stored
    child_env, _removed = provider_child_env(str(account.get("id") or ""))
    execute = run or (
        lambda argv: _hidden_run(argv, cwd=None, timeout=0.75, env=child_env)
    )
    try:
        result = execute([cli_path, "--version"])
    except (OSError, subprocess.SubprocessError):
        return ""
    if int(getattr(result, "returncode", 0) or 0) != 0:
        return ""
    raw = str(getattr(result, "stdout", "") or getattr(result, "stderr", "") or "")
    version = redact(raw).strip().splitlines()[0][:160] if raw.strip() else ""
    if run is None:
        _CLI_VERSION_CACHE[cli_path] = version
        _write_cli_probe(cli_path, "version", version, home=home)
    return version


def _semantic_version(raw: str) -> tuple[int, int, int] | None:
    """Extract a three-part CLI version without trusting surrounding text."""

    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", str(raw or ""))
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _codex_cli_supports_current_default(raw_version: str) -> bool:
    """Fail only when a parseable Codex version is known to be too old."""

    parsed = _semantic_version(raw_version)
    return parsed is None or parsed >= _CODEX_CURRENT_DEFAULT_MIN_VERSION


def _copilot_supports_scoped_permissions(
    account: dict[str, Any],
    *,
    run: Callable[[list[str]], Any] | None = None,
    home: Path | None = None,
) -> bool:
    """Whether this Copilot CLI can expose only bounded workspace edit tools."""

    cli_path = str(account.get("cli_path") or "")
    if not cli_path:
        return False
    if run is None:
        if cli_path in _CLI_CAPABILITY_CACHE:
            return _CLI_CAPABILITY_CACHE[cli_path]
        stored = _read_cli_probe(cli_path, "scoped_editing", home=home)
        if isinstance(stored, bool):
            _CLI_CAPABILITY_CACHE[cli_path] = stored
            return stored
    child_env, _removed = provider_child_env("copilot")
    execute = run or (
        lambda argv: _hidden_run(argv, cwd=None, timeout=0.75, env=child_env)
    )
    try:
        result = execute([cli_path, "--help"])
    except (OSError, subprocess.SubprocessError):
        supported = False
    else:
        output = "\n".join(
            str(part or "")
            for part in (
                getattr(result, "stdout", ""),
                getattr(result, "stderr", ""),
            )
        )
        required = (
            "--available-tools",
            "--allow-tool",
            "--deny-tool",
            "--add-dir",
        )
        supported = int(getattr(result, "returncode", 0) or 0) == 0 and all(
            flag in output for flag in required
        )
    if run is None:
        _CLI_CAPABILITY_CACHE[cli_path] = supported
        _write_cli_probe(cli_path, "scoped_editing", supported, home=home)
    return supported


def provider_connection_doctor(
    *,
    home: Path | None = None,
    accounts: list[dict[str, Any]] | None = None,
    connections: list[dict[str, Any]] | None = None,
    credentials: list[dict[str, Any]] | None = None,
    version_run: Callable[[list[str]], Any] | None = None,
    include_cli_versions: bool = True,
    include_history: bool | None = None,
) -> list[dict[str, Any]]:
    """Aggregate provider health without reading credentials or probing auth."""

    detected_accounts = (
        list_connected_accounts(home) if accounts is None else list(accounts)
    )
    base_connections = (
        [connection_for_account(item) for item in detected_accounts]
        if connections is None
        else list(connections)
    )
    use_history = connections is None if include_history is None else include_history
    by_provider = {
        str(item.get("providerId") or ""): dict(item) for item in base_connections
    }
    entries: list[dict[str, Any]] = []
    for account in detected_accounts:
        provider = str(account.get("id") or "")
        connection = by_provider.get(provider, connection_for_account(account))
        if use_history:
            with _CONNECTION_CACHE_LOCK:
                history = dict(
                    _CONNECTION_HISTORY.get(_connection_key(provider, home)) or {}
                )
            connection = _with_connection_history(connection, history)
        error = connection.get("error")
        recovery = (
            list(error.get("recoveryActions") or []) if isinstance(error, dict) else []
        )
        if connection.get("authStatus") != "connected" and "sign_in" not in recovery:
            recovery.append("sign_in")
        entries.append(
            {
                "providerId": provider,
                "displayName": str(account.get("label") or provider.title()),
                "kind": "account",
                "health": _connection_health(connection),
                "authStatus": str(connection.get("authStatus") or "unknown"),
                "credentialSource": "user_account",
                "accountType": str(connection.get("accountType") or "unknown"),
                "credentialSourceLabel": "Subscription sign-in",
                "cliInstalled": bool(account.get("cli_present")),
                "cliVersion": (
                    _account_cli_version(account, run=version_run)
                    if include_cli_versions
                    else ""
                ),
                "lastCheckedAt": connection.get("lastCheckedAt"),
                "lastError": str(connection.get("lastError") or ""),
                "lastErrorCode": str(connection.get("lastErrorCode") or ""),
                "safeDiagnostic": str(connection.get("safeDiagnostic") or ""),
                "envOverridesRemoved": [
                    str(item) for item in connection.get("envOverridesRemoved") or []
                ],
                "recoveryActions": recovery,
                "loginHint": str(
                    connection.get("loginHint") or account.get("login_hint") or ""
                ),
                "detected": bool(connection.get("detected")),
                "loginSupported": provider in _LOGIN_ARGV,
            }
        )

    if credentials is None:
        from .credentials import credential_statuses

        credential_items = credential_statuses()
    else:
        credential_items = list(credentials)
    labels = {
        "kimi": "Kimi",
        "gemini": "Gemini",
        "groq": "Groq",
        "mistral": "Mistral",
        "github": "GitHub",
    }
    for credential in credential_items:
        provider = str(credential.get("provider") or "")
        configured = bool(credential.get("configured"))
        source = str(credential.get("source") or "")
        entries.append(
            {
                "providerId": provider,
                "displayName": labels.get(provider, provider.title()),
                "kind": "api",
                "health": "detected" if configured else "not_configured",
                "authStatus": "detected" if configured else "not_configured",
                "credentialSource": source,
                "credentialSourceLabel": {
                    "environment": "Environment variable",
                    "keychain": "OS credential store",
                }.get(source, "Not configured"),
                "credentialEnvironmentName": str(credential.get("envKey") or ""),
                "cliInstalled": None,
                "cliVersion": "",
                "lastCheckedAt": credential.get("lastCheckedAt"),
                "lastError": "",
                "lastErrorCode": "",
                "safeDiagnostic": (
                    "API credential detected; use Test connection to verify it."
                    if configured
                    else "No API credential configured."
                ),
                "envOverridesRemoved": [],
                "recoveryActions": ["test_connection"] if configured else [],
                "loginHint": "",
                "detected": configured,
                "loginSupported": False,
            }
        )
    # Fold every entry onto the one capability + health truth (#168) without
    # disturbing the existing per-surface `health`/`authStatus` strings.
    _annotate_provider_capabilities(entries)
    return entries


def _annotate_provider_capabilities(entries: list[dict[str, Any]]) -> None:
    """Attach the canonical health state and capability profile to each doctor
    entry so the picker, settings, and router all read one truth (#168)."""
    from .provider_capabilities import health_from_connection, provider_profile

    for entry in entries:
        entry["healthState"] = health_from_connection(entry).value
        try:
            entry["capabilities"] = provider_profile(
                str(entry.get("providerId") or "")
            ).to_dict()
        except ValueError:
            # github (the git/PR connector) isn't an AI provider — no profile.
            entry["capabilities"] = None


def interactive_provider_login(
    account_id: str,
    *,
    home: Path | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    probe: Callable[..., dict[str, Any]] | None = None,
    platform_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    timeout: float = 900.0,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Run one allowlisted provider login in a deliberately visible terminal."""

    provider = str(account_id or "").strip().lower()
    spec = next((item for item in ACCOUNT_SPECS if item["id"] == provider), None)
    login_argv = _LOGIN_ARGV.get(provider)
    if spec is None or login_argv is None:
        return {
            "provider": provider,
            "signedIn": False,
            "status": "failed",
            "errorCode": "PROVIDER_UNKNOWN",
            "message": "This provider does not support guided sign-in.",
        }
    cli_path = which(str(spec["cli"]))
    if not cli_path:
        return {
            "provider": provider,
            "signedIn": False,
            "status": "failed",
            "errorCode": "CLI_NOT_INSTALLED",
            "message": f"{spec['label']} CLI is not installed or not on PATH.",
        }
    child_env, removed = provider_child_env(provider)
    platform = platform_name or sys.platform
    command = [cli_path, *login_argv]
    kwargs: dict[str, Any] = {
        "cwd": str(home.expanduser().resolve()) if home is not None else None,
        "env": child_env,
    }
    if platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
    elif platform.startswith("linux"):
        terminal_specs = (
            ("x-terminal-emulator", ["-e"]),
            ("gnome-terminal", ["--wait", "--"]),
            ("konsole", ["-e"]),
        )
        terminal = None
        for name, args in terminal_specs:
            terminal_path = which(name)
            if terminal_path:
                terminal = (terminal_path, args)
                break
        if terminal is None:
            return {
                "provider": provider,
                "signedIn": False,
                "status": "failed",
                "errorCode": "VISIBLE_TERMINAL_UNAVAILABLE",
                "message": "No supported visible terminal application was found.",
                "envOverridesRemoved": removed,
            }
        command = [str(terminal[0]), *terminal[1], *command]
    else:
        return {
            "provider": provider,
            "signedIn": False,
            "status": "failed",
            "errorCode": "VISIBLE_TERMINAL_UNAVAILABLE",
            "message": "Guided sign-in is not available on this platform yet.",
            "envOverridesRemoved": removed,
        }
    process = None
    try:
        process = popen(command, **kwargs)  # nosec B603 - fixed allowlisted argv
        if cancel is None:
            returncode = int(process.wait(timeout=timeout) or 0)
        else:
            deadline = time.monotonic() + timeout
            while True:
                if cancel.is_set():
                    _terminate(process)
                    return {
                        "provider": provider,
                        "signedIn": False,
                        "status": "cancelled",
                        "errorCode": "LOGIN_CANCELLED",
                        "message": "Sign-in was cancelled when OPai closed.",
                        "envOverridesRemoved": removed,
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    returncode = int(process.wait(timeout=min(0.25, remaining)) or 0)
                    break
                except subprocess.TimeoutExpired:
                    continue
    except subprocess.TimeoutExpired:
        if process is not None:
            _terminate(process)
        return {
            "provider": provider,
            "signedIn": False,
            "status": "timed_out",
            "errorCode": "LOGIN_TIMEOUT",
            "message": "The sign-in window timed out before completion.",
            "envOverridesRemoved": removed,
        }
    except (OSError, ValueError) as exc:
        return {
            "provider": provider,
            "signedIn": False,
            "status": "failed",
            "errorCode": "LOGIN_LAUNCH_FAILED",
            "message": redact(str(exc))[:500],
            "envOverridesRemoved": removed,
        }

    invalidate_connection_cache(provider)
    check = probe or test_account_connection
    try:
        connection = check(provider, home=home, force=True)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "provider": provider,
            "signedIn": False,
            "status": "failed",
            "errorCode": "LOGIN_VERIFY_FAILED",
            "message": redact(str(exc))[:500],
            "envOverridesRemoved": removed,
        }
    signed_in = bool(
        connection.get("authStatus") in {"connected", "unknown"}
        and (connection.get("authStatus") == "connected" or connection.get("detected"))
    )
    return {
        "provider": provider,
        "signedIn": signed_in,
        "status": "signed_in" if signed_in else "not_verified",
        "returncode": returncode,
        "message": (
            f"{spec['label']} sign-in verified."
            if signed_in
            else str(
                connection.get("safeDiagnostic")
                or "The provider did not report a usable sign-in."
            )
        ),
        "envOverridesRemoved": removed,
        "connection": _safe_connection_summary(connection),
    }


# Each provider's own sanctioned, non-interactive sign-out. OPai never touches
# a credential file directly — only a documented CLI subcommand. Copilot has
# no such command (verified: its --help lists `login` but no `logout`), so it
# is deliberately absent here rather than guessed at.
_LOGOUT_ARGV: dict[str, list[str]] = {
    "claude": ["auth", "logout"],
    "codex": ["logout"],
}


def disconnect_account(account_id: str, *, home: Path | None = None) -> dict[str, Any]:
    """Sign out of a connected AI account using its own CLI's logout command.

    This is the real fix for the "OPai says connected but the real request
    401s" gap: a session can go stale (expired, revoked elsewhere) in a way
    OPai's local checks cannot detect in advance. Signing out and back in via
    the provider's own flow clears it. Never reads or writes credential files
    directly; always shells out to the CLI's documented sign-out command.
    """
    spec = next((item for item in ACCOUNT_SPECS if item["id"] == account_id), None)
    if spec is None:
        return {
            "provider": account_id,
            "disconnected": False,
            "message": "Unknown provider.",
        }
    logout_argv = _LOGOUT_ARGV.get(account_id)
    if logout_argv is None:
        return {
            "provider": account_id,
            "disconnected": False,
            "unsupported": True,
            "message": (
                f"{spec['label']} CLI has no command-line sign-out. Revoke its "
                "access from your account settings or OS keychain, then run "
                f"`{spec['cli']}` again to sign back in."
            ),
        }
    cli_path = _which(spec["cli"])
    if not cli_path:
        return {
            "provider": account_id,
            "disconnected": False,
            "message": f"{spec['label']} CLI was not found on PATH.",
        }
    logout_env, _ = provider_child_env(account_id)
    try:
        proc = _hidden_run(
            [cli_path, *logout_argv], cwd=None, timeout=20.0, env=logout_env
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"provider": account_id, "disconnected": False, "message": str(exc)}
    ok = _process_returncode(proc) == 0
    invalidate_connection_cache(account_id, clear_history=ok)
    detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
    return {
        "provider": account_id,
        "disconnected": ok,
        "message": (
            f"Signed out of {spec['label']}. Run `{spec['cli']}` to sign back in."
            if ok
            else (detail or "Sign-out failed.")
        ),
    }


# Account model lists derive from the single source of truth in
# opai.model_registry (#170) — no more parallel tuples drifting against the
# provider_contract display tables and the CLIs. Shapes are preserved:
# CLAUDE_MODELS = (id, full); CODEX/COPILOT = (id, full, capability).
CLAUDE_MODELS: list[tuple[str, str]] = [
    (spec.id, spec.full) for spec in _models_for("claude")
]

CODEX_MODELS: list[tuple[str, str, str]] = [
    (spec.id, spec.full, spec.capability) for spec in _models_for("codex")
]

# Copilot multiplexes Anthropic and OpenAI models behind one subscription.
COPILOT_MODELS: list[tuple[str, str, str]] = [
    (spec.id, spec.full, spec.capability) for spec in _models_for("copilot")
]


def _account_options(
    account: dict[str, Any],
    *,
    connected: bool,
    account_type: str | None = None,
    cli_version: str = "",
    copilot_scoped_editing: bool | None = None,
) -> list[dict[str, Any]]:
    from opai.provider_contract import provider_display_name

    disabled_reason = None if connected else f"{account['label']} is not connected"
    if account["id"] == "claude":
        return [
            {
                "id": f"account:claude:{alias}",
                "label": provider_display_name("claude", alias),
                "advanced_label": provider_display_name("claude", alias, advanced=True),
                "provider": "claude",
                "model": alias,
                "kind": "account",
                "group": "claude",
                "paid": True,
                "vendor": account["vendor"],
                "connected": connected,
                "available": connected,
                "disabled_reason": disabled_reason,
                "repo_editing": True,
            }
            for alias, label in CLAUDE_MODELS
        ]
    if account["id"] == "codex":
        codex_compatible = _codex_cli_supports_current_default(cli_version)
        codex_available = connected and codex_compatible
        codex_disabled_reason = disabled_reason
        if connected and not codex_compatible:
            codex_disabled_reason = (
                "Update Codex CLI to use the current account-default model "
                "(npm install -g @openai/codex)."
            )
        normalized_account_type = str(account_type or "").lower()
        if account_type is not None and normalized_account_type != "api_key":
            return [
                {
                    "id": "account:codex",
                    "label": "Codex · Account default",
                    "advanced_label": (
                        "Codex chooses a model supported by this ChatGPT account"
                        if normalized_account_type == "chatgpt"
                        else (
                            "Codex chooses a supported model until this account's "
                            "sign-in type is verified"
                        )
                    ),
                    "provider": "codex",
                    "model": "",
                    "kind": "account",
                    "group": "codex",
                    "paid": True,
                    "vendor": account["vendor"],
                    "speed": "balanced",
                    "connected": connected,
                    "available": codex_available,
                    "disabled_reason": codex_disabled_reason,
                    "repo_editing": codex_compatible,
                    "cli_version": cli_version,
                }
            ]
        return [
            {
                "id": f"account:codex:{model_id}",
                "label": provider_display_name("codex", model_id),
                "advanced_label": provider_display_name("codex", label, advanced=True),
                "provider": "codex",
                "model": model_id,
                "kind": "account",
                "group": "codex",
                "paid": True,
                "vendor": account["vendor"],
                "speed": speed,
                "connected": connected,
                "available": codex_available,
                "disabled_reason": codex_disabled_reason,
                "repo_editing": codex_compatible,
                "cli_version": cli_version,
            }
            for model_id, label, speed in CODEX_MODELS
        ]
    if account["id"] == "copilot":
        return [
            {
                "id": f"account:copilot:{model_id}",
                "label": provider_display_name("copilot", model_id),
                "advanced_label": provider_display_name(
                    "copilot", label, advanced=True
                ),
                "provider": "copilot",
                "model": model_id,
                "kind": "account",
                "group": "copilot",
                "paid": True,
                "vendor": account["vendor"],
                "speed": speed,
                "connected": connected,
                "available": connected,
                "disabled_reason": disabled_reason,
                "repo_editing": (
                    True
                    if copilot_scoped_editing is None
                    else bool(copilot_scoped_editing)
                ),
            }
            for model_id, label, speed in COPILOT_MODELS
        ]
    return [
        {
            "id": f"account:{account['id']}",
            "label": provider_display_name(account["id"]),
            "advanced_label": provider_display_name(account["id"], advanced=True),
            "provider": account["id"],
            "model": "",
            "kind": "account",
            "group": account["id"],
            "paid": True,
            "vendor": account["vendor"],
            "connected": connected,
            "available": connected,
            "disabled_reason": disabled_reason,
            "repo_editing": True,
        }
    ]


def account_models(
    home: Path | None = None,
    *,
    include_unavailable: bool = False,
    accounts: list[dict[str, Any]] | None = None,
    account_types: dict[str, str] | None = None,
    inspect_cli_capabilities: bool = False,
) -> list[dict[str, Any]]:
    """Picker options for connected accounts (paid, run via the user's CLI).

    Claude, Codex, and Copilot expand into selectable models so the GUI is not
    locked to one opaque account option. Unavailable models can be included for
    settings.
    """
    options: list[dict[str, Any]] = []
    for account in accounts if accounts is not None else list_connected_accounts(home):
        connected = bool(account["connected"])
        if not connected and not include_unavailable:
            continue
        account_type = (
            str(account_types.get(account["id"]) or "unknown")
            if account_types is not None
            else None
        )
        cli_version = ""
        copilot_scoped_editing: bool | None = None
        cli_path = str(account.get("cli_path") or "")
        # Enumeration deliberately avoids provider probes, but "unknown" used to
        # be resolved *optimistically*, so a cold-start picker offered a Codex
        # whose CLI could not run anything. The persisted verdict (see
        # _read_cli_probe) makes the common case both honest and probe-free; the
        # one-shot probe below is reached only on a first launch or right after
        # the binary changed, which is exactly when guessing is most wrong.
        if account["id"] == "codex":
            if inspect_cli_capabilities:
                cli_version = _account_cli_version(account, home=home)
            else:
                cli_version = _CLI_VERSION_CACHE.get(cli_path) or (
                    _account_cli_version(account, home=home) if cli_path else ""
                )
        if account["id"] == "copilot":
            if inspect_cli_capabilities:
                copilot_scoped_editing = _copilot_supports_scoped_permissions(
                    account, home=home
                )
            elif cli_path in _CLI_CAPABILITY_CACHE:
                copilot_scoped_editing = _CLI_CAPABILITY_CACHE[cli_path]
            elif cli_path:
                copilot_scoped_editing = _copilot_supports_scoped_permissions(
                    account, home=home
                )
        options.extend(
            _account_options(
                account,
                connected=connected,
                account_type=account_type,
                cli_version=cli_version,
                copilot_scoped_editing=copilot_scoped_editing,
            )
        )
    return options


# --------------------------------------------------------------------------- #
# Claude PreToolUse hook gate (F23).
#
# Full Auto used to hand the claude CLI a blanket ``--dangerously-skip-
# permissions`` with no further control, so classified-destructive commands
# (``gh issue close``, ``git push --force``, ``rm -rf``) ran with zero
# confirmation. Full Auto now pairs that flag with a generated ``--settings``
# file registering a PreToolUse hook for the Bash tool; the hook is the
# ``opai hooks claude-pre-tool`` subcommand, which re-classifies every shell
# command through opaihub.sandbox + opaihub.safety_gates and denies anything
# that needs explicit user confirmation. Net posture: auto-approve EXCEPT
# classified-destructive, which the hook denies.
# --------------------------------------------------------------------------- #
_CLAUDE_HOOK_SETTINGS_NAME = "opai-claude-hooks.json"


def claude_hook_command() -> str:
    """Shell command Claude Code runs for each PreToolUse (Bash) event."""
    executable = sys.executable or "python"
    return f'"{executable}" -m opai hooks claude-pre-tool'


def build_claude_hook_settings() -> dict[str, Any]:
    """The ``--settings`` payload wiring OPai's gate into Claude Code hooks."""
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": claude_hook_command()},
                    ],
                }
            ]
        }
    }


def claude_hook_settings_path() -> Path:
    """Deterministic settings location (rewritten each gated run)."""
    return Path(tempfile.gettempdir()) / "opai" / _CLAUDE_HOOK_SETTINGS_NAME


def ensure_claude_hook_settings(path: Path | None = None) -> Path:
    """Write the hook settings file if missing/stale; return its path.

    Best-effort: a write failure leaves any previous (identical-content) file
    in place, and the deterministic path is still returned so the CLI either
    reads a valid gate or errors on a missing file rather than running
    ungated.
    """
    target = path or claude_hook_settings_path()
    payload = json.dumps(build_claude_hook_settings(), indent=2, sort_keys=True) + "\n"
    try:
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != payload:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
    except OSError:
        pass
    return target


def _guard_int_env(name: str, default: int) -> int:
    """Non-negative int knob from the environment; bad values keep the default.

    Used by the F27 no-progress guard (`OPAI_NO_PROGRESS_STEP_BUDGET`,
    `OPAI_NO_PROGRESS_SECONDS`). ``0`` disables the corresponding check.
    """
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _codex_safety_preamble() -> str:
    """The Full Auto safety gate spelled out for `codex exec` (F23, Round 2).

    Codex exec has no PreToolUse hook, so this prompt IS the gate. It must also
    be honest about *why* a push is refused: telling a user whose push consent
    is already granted to go click "Enable pushes & PRs" sends them hunting for
    a button that now reads "Disable pushes & PRs" — the exact wrong-directions
    failure the 2026-07-24 retest caught. So the push sentence tracks the real
    consent state, and the model is told not to invent an alternative.
    """
    try:
        from .github_connector import push_allowed, stored_github_token

        consented = bool(push_allowed()) and bool(stored_github_token()[0])
    except Exception:  # noqa: BLE001 - fail closed to the "not enabled" wording
        consented = False
    push_guidance = (
        (
            "For a git push: pushes ARE already enabled for this user, but this "
            "runner may not shell out to `git push`. Say plainly that you cannot "
            "push from this run and that they can push from a terminal. Do NOT "
            "tell them to enable anything in Settings — it is already on — and do "
            "not tell them an approval prompt is waiting for them, because this "
            "runner does not raise one."
        )
        if consented
        else (
            "For a git push: tell the user to enable pushes once in Settings -> "
            "Providers & Connections (connect a GitHub token, then click "
            '"Enable pushes & PRs"). After that OPai can push, and will ask '
            "them to approve each push."
        )
    )
    return (
        "Safety: destructive or external-mutating commands (git push, gh "
        "issue/pr mutations, rm -rf, deploys) are denied in this mode. Do not "
        "attempt them. " + push_guidance + " For anything else, tell them to "
        "run it themselves in a terminal. Never invent a Settings button, page, "
        "or toggle you were not told about here, and never report an action as "
        "done when it was denied.\n\n"
    )


class AccountRunner:
    """Run one task through a logged-in CLI. Paid/cloud; read-only by default."""

    paid = True

    def __init__(self, account_id: str, cli_path: str, *, model: str | None = None):
        self.account_id = account_id
        self.name = account_id
        self.cli_path = cli_path
        # Resolve aliases/dated ids to the provider's canonical model id so the
        # CLI always receives a value it accepts — e.g. a stale "sonnet-5"
        # selection becomes "claude-sonnet-5" instead of failing "model not
        # found" (#307). Unknown ids pass through untouched.
        from opai.model_registry import resolve_id

        self.model = resolve_id(account_id, model) or (model or "")

    def available(self) -> bool:
        return bool(self.cli_path) and Path(self.cli_path).exists()

    def supports_scoped_editing(self) -> bool:
        if self.account_id != "copilot":
            return True
        return _copilot_supports_scoped_permissions(
            {"id": self.account_id, "cli_path": self.cli_path}
        )

    def build_command(
        self,
        prompt: str,
        *,
        allow_edits: bool = False,
        out_file: str | None = None,
        mode: str | None = None,
        stream: bool = False,
        edit_grant: bool = False,
        project_root: Path | None = None,
    ) -> list[str]:
        """Construct the CLI argv. Pure + side-effect free so tests can assert it.

        ``edit_grant`` is the one-shot approval from the in-context
        "Allow edits once" card (F26): in Safe Auto it maps to the claude
        CLI's ``--permission-mode acceptEdits`` so file edits proceed while
        Bash and destructive actions stay gated. It never applies to
        read-only modes and is redundant in Full Auto.
        """
        selected_mode = mode or ("safe-auto" if allow_edits else "ask")
        if self.account_id == "claude":
            # Non-stream: `json` gives final text + real $ cost in one object.
            # Stream: `stream-json --verbose` emits JSONL events (text deltas +
            # tool_use) so the GUI can show live activity and cost at the end.
            if stream:
                cmd = [
                    self.cli_path,
                    "-p",
                    "--output-format",
                    "stream-json",
                    "--verbose",
                ]
            else:
                cmd = [self.cli_path, "-p", "--output-format", "json"]
            if self.model:
                cmd += ["--model", self.model]
            if selected_mode == "full-auto":
                # Full Auto stays autonomous for ordinary commands, but every
                # Bash call is gated by the PreToolUse hook in the generated
                # settings file: the hook denies commands the OPai classifier
                # marks destructive/confirm-only (gh mutations, git push,
                # rm -rf), so those still need explicit user confirmation in
                # the UI (F23). skip-permissions is only ever emitted together
                # with this gate.
                cmd += ["--dangerously-skip-permissions"]
                cmd += ["--settings", str(claude_hook_settings_path())]
            elif selected_mode == "safe-auto" and edit_grant:
                # F26: the user clicked "Allow edits once" on the approval
                # card. acceptEdits auto-approves file edits only — Bash and
                # anything destructive still go through the CLI's own gate
                # (denied non-interactively → surfaced as approval cards).
                cmd += ["--permission-mode", "acceptEdits"]
            if selected_mode in {"ask", "plan", "approve-edits"}:
                prompt = (
                    "Do not modify files or run mutating commands. "
                    "Return an answer or patch plan only.\n\n" + prompt
                )
            cmd.append(prompt)
            return cmd
        if self.account_id == "codex":
            if selected_mode in {"safe-auto", "full-auto"}:
                sandbox = "workspace-write"
            else:
                sandbox = "read-only"
            # Codex exec has no hook protocol, so there is no way to gate
            # individual destructive commands from outside. Full Auto therefore
            # keeps `--ask-for-approval on-request`: in non-interactive exec
            # mode approval requests cannot be answered and are denied, which
            # is exactly the fail-closed posture gh/git mutations need (F23).
            approval = "on-request"
            cmd = [
                self.cli_path,
                "--ask-for-approval",
                approval,
                "exec",
                "--sandbox",
                sandbox,
                "--color",
                "never",
                "--skip-git-repo-check",
            ]
            if stream:
                # JSONL events (commands run, files changed, agent messages) so
                # the GUI/CLI timeline shows real codex activity (#106). The
                # out-file stays as the answer fallback if the schema drifts.
                cmd.append("--json")
            if self.model:
                cmd += ["--model", self.model]
            if out_file:
                cmd += ["--output-last-message", out_file]
            if selected_mode == "full-auto":
                # No hook protocol exists to enforce this, so make the gate
                # explicit to the agent as well (defense in depth for F23).
                prompt = _codex_safety_preamble() + prompt
            cmd.append(prompt)
            return cmd
        if self.account_id == "copilot":
            # `-s` prints only the agent's reply (no banner/stats) and
            # `--no-ask-user` stops it pausing for input in non-interactive use.
            cmd = [self.cli_path, "-s", "--no-ask-user"]
            if self.model:
                cmd += [f"--model={self.model}"]
            edit_mode = selected_mode in {
                "safe-auto",
                "approve-edits",
                "full-auto",
            }
            if edit_mode:
                # Current Copilot CLIs can expose a named tool subset. Shell,
                # web, and unbounded tools are absent, while edits are confined
                # to the selected repository. Older CLIs are rejected by the
                # capability preflight before this command is launched.
                cmd += [
                    "--available-tools=view,grep,glob,edit",
                    "--allow-tool=edit",
                ]
                if project_root is not None:
                    scoped_root = str(project_root.expanduser().resolve())
                    cmd += ["-C", scoped_root, "--add-dir", scoped_root]
            else:
                prompt = (
                    "Do not modify files or run mutating commands. "
                    "Return an answer or patch plan only.\n\n" + prompt
                )
            # `-p` consumes the next argument as the prompt, so it must be last.
            cmd += ["-p", prompt]
            return cmd
        raise ValueError(f"unknown account: {self.account_id}")

    def complete(
        self,
        prompt: str,
        *,
        project_root: Path | None = None,
        allow_edits: bool = False,
        mode: str | None = None,
        timeout: float = 1200.0,
        edit_grant: bool = False,
    ) -> dict[str, Any]:
        """Run the task; return ``{"text", "cost", "timed_out"?}``.

        Agentic runs (especially Full Auto building a feature) can take many
        minutes, so the timeout is generous. If it is still exceeded the CLI is
        stopped and a clean ``timed_out`` flag is returned rather than raising a
        ``TimeoutExpired`` that would dump the raw command into the chat.
        """
        cwd = str(project_root) if project_root else None
        from opai.provider_contract import normalize_provider_error

        # Sanitized child env: parent AI-session variables must never steer
        # this CLI's auth or model selection (see opaihub.proc).
        child_env, _env_removed = provider_child_env(self.account_id)

        if self.account_id == "codex":
            with tempfile.NamedTemporaryFile(
                "r", suffix=".txt", delete=False, encoding="utf-8"
            ) as handle:
                out_path = handle.name
            cmd = self.build_command(
                prompt,
                allow_edits=allow_edits,
                out_file=out_path,
                mode=mode,
                project_root=project_root,
            )
            try:
                proc = _hidden_run(cmd, cwd=cwd, timeout=timeout, env=child_env)
            except subprocess.TimeoutExpired:
                Path(out_path).unlink(missing_ok=True)
                return {"text": "", "cost": None, "timed_out": True}
            try:
                answer = Path(out_path).read_text(encoding="utf-8").strip()
            except OSError:
                answer = ""
            finally:
                Path(out_path).unlink(missing_ok=True)
            returncode = _process_returncode(proc)
            diagnostic = "\n".join(
                part.strip()
                for part in (str(proc.stderr or ""), str(proc.stdout or ""), answer)
                if part.strip()
            )
            normalized = normalize_provider_error(
                self.account_id,
                diagnostic,
                model=self.model,
                returncode=returncode,
            )
            known_failure = normalized["code"] not in {"UNKNOWN", "NO_RESPONSE"}
            if (returncode != 0 and not answer) or (
                known_failure and (returncode != 0 or not answer)
            ):
                _invalidate_cache_for_error(self.account_id, normalized)
                return {
                    "text": "",
                    "cost": None,
                    "error": normalized,
                    "returncode": returncode,
                }
            return {
                "text": answer or (proc.stdout or proc.stderr or "").strip(),
                "cost": None,
                "returncode": returncode,
            }
        cmd = self.build_command(
            prompt,
            allow_edits=allow_edits,
            mode=mode,
            edit_grant=edit_grant,
            project_root=project_root,
        )
        if "--settings" in cmd:
            # Claude Full Auto: the PreToolUse hook settings file must exist
            # before the CLI starts or the Bash gate is silently absent (F23).
            ensure_claude_hook_settings()
        try:
            proc = _hidden_run(cmd, cwd=cwd, timeout=timeout, env=child_env)
        except subprocess.TimeoutExpired:
            return {"text": "", "cost": None, "timed_out": True}
        returncode = _process_returncode(proc)
        raw = (proc.stdout or "").strip()
        # claude --output-format json -> {"result": "...", "total_cost_usd": ...}.
        # Degrade gracefully to raw text if it isn't JSON.
        try:
            data = json.loads(raw)
            text = str(data.get("result") or "").strip()
            cost = data.get("total_cost_usd")
            subtype = str(data.get("subtype") or "")
            native_error = data.get("is_error") is True or subtype.startswith("error_")
            diagnostic = "\n".join(
                part.strip()
                for part in (
                    text,
                    str(data.get("error") or ""),
                    str(proc.stderr or ""),
                )
                if part.strip()
            )
            normalized = normalize_provider_error(
                self.account_id,
                diagnostic,
                model=self.model,
                returncode=returncode,
            )
            known_failure = normalized["code"] not in {"UNKNOWN", "NO_RESPONSE"}
            if (
                native_error
                or _is_login_sentinel(text)
                or (returncode != 0 and (known_failure or not text))
            ):
                _invalidate_cache_for_error(self.account_id, normalized)
                return {
                    "text": "",
                    "cost": cost,
                    "error": normalized,
                    "returncode": returncode,
                }
            return {
                "text": text or raw,
                "cost": cost,
                "returncode": returncode,
            }
        except (json.JSONDecodeError, TypeError):
            text = raw or (proc.stderr or "").strip()
            normalized = normalize_provider_error(
                self.account_id,
                "\n".join(
                    part.strip()
                    for part in (str(proc.stderr or ""), raw)
                    if part.strip()
                ),
                model=self.model,
                returncode=returncode,
            )
            known_failure = normalized["code"] not in {"UNKNOWN", "NO_RESPONSE"}
            if _is_login_sentinel(text) or (
                known_failure and (returncode != 0 or not raw)
            ):
                _invalidate_cache_for_error(self.account_id, normalized)
                return {
                    "text": "",
                    "cost": None,
                    "error": normalized,
                    "returncode": returncode,
                }
            return {"text": text, "cost": None, "returncode": returncode}

    def stream(
        self,
        prompt: str,
        *,
        project_root: Path | None = None,
        allow_edits: bool = False,
        mode: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_text: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
        timeout: float = 1200.0,
        edit_grant: bool = False,
    ) -> dict[str, Any]:
        """Run the task with a killable subprocess, emitting live activity.

        - **claude**: parses ``stream-json`` into real events (text deltas +
          tool calls like Read/Edit/Bash) and the final cost.
        - **codex**: streams progress; the final message comes from the
          out-file (its stdout is logs, not the answer).
        - **copilot**: streams stdout text.

        ``cancel`` is polled ~5×/sec; setting it kills the process promptly.
        Returns ``{"text","cost","timed_out"?,"cancelled"?,"error"?}`` and never
        raises for provider failures — they degrade to a clean result.
        """
        from opai.activity import ActivitySession, make_event

        cwd = str(project_root) if project_root else None
        out_path: str | None = None
        if self.account_id == "codex":
            with tempfile.NamedTemporaryFile(
                "r", suffix=".txt", delete=False, encoding="utf-8"
            ) as handle:
                out_path = handle.name
        structured = self.account_id in {"claude", "codex"}
        # One session per stream call: stable derived ids so repeated states
        # ("Connected", per-chunk "Streaming response", Codex started/completed
        # pairs) coalesce into single rows instead of flooding the feed
        # (#223, #224).
        session = ActivitySession()
        line_parser = (
            session.parse_claude_line
            if self.account_id == "claude"
            else session.parse_codex_line
        )
        cmd = self.build_command(
            prompt,
            allow_edits=allow_edits,
            out_file=out_path,
            mode=mode,
            stream=structured,
            edit_grant=edit_grant,
            project_root=project_root,
        )
        if "--settings" in cmd:
            # Claude Full Auto: the PreToolUse hook settings file must exist
            # before the CLI starts or the Bash gate is silently absent (F23).
            ensure_claude_hook_settings()
        # Sanitized child env: parent AI-session variables must never steer
        # this CLI's auth or model selection (see opaihub.proc).
        child_env, _env_removed = provider_child_env(self.account_id)
        try:
            proc = _popen(cmd, cwd=cwd, env=child_env)
        except OSError as exc:
            if out_path:
                Path(out_path).unlink(missing_ok=True)
            return {"text": "", "cost": None, "error": str(exc)}

        lines: queue.Queue[tuple[str, str, str | None]] = queue.Queue()

        def _reader(source: str) -> None:
            pipe = getattr(proc, source, None)
            if pipe is None:
                lines.put(("eof", source, None))
                return
            try:
                for line in iter(pipe.readline, ""):
                    lines.put(("line", source, line))
            except (OSError, ValueError):
                pass
            finally:
                lines.put(("eof", source, None))

        threading.Thread(target=_reader, args=("stdout",), daemon=True).start()
        threading.Thread(target=_reader, args=("stderr",), daemon=True).start()

        text_parts: list[str] = []
        diagnostic_parts: list[str] = []
        provider_errors: list[str] = []
        cost: float | None = None
        started = time.monotonic()
        stopped: str | None = None
        terminal_provider_error: str | None = None
        streamed_any = False
        open_pipes = 2
        # F27 no-progress guard: an edit-intent run that keeps exploring
        # without a single edit attempt is stopped (checkpointed) instead of
        # burning the whole budget. Both knobs are env-tunable; 0 disables.
        guard_active = structured and allow_edits and mode in {"safe-auto", "full-auto"}
        step_budget = _guard_int_env("OPAI_NO_PROGRESS_STEP_BUDGET", 60)
        no_progress_seconds = _guard_int_env("OPAI_NO_PROGRESS_SECONDS", 600)
        step_ids: set[str] = set()
        edit_attempted = False
        _STEP_TYPES = {
            "tool_call",
            "file_read",
            "file_edit",
            "command_run",
            "context_read",
            "ci_watch",
        }
        while True:
            if cancel is not None and cancel.is_set():
                stopped = "cancelled"
                break
            if time.monotonic() - started > timeout:
                stopped = "timed_out"
                break
            try:
                kind, source, payload = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "eof":
                open_pipes -= 1
                if open_pipes == 0:
                    break
                continue
            if source == "stderr":
                if payload and payload.strip():
                    diagnostic_parts.append(payload.strip())
                continue
            if structured:
                raw_line = payload or ""
                try:
                    json.loads(raw_line)
                except (json.JSONDecodeError, TypeError):
                    if raw_line.strip():
                        diagnostic_parts.append(raw_line.strip())
                    continue
                part = line_parser(raw_line)
                for event in part["events"]:
                    if on_event:
                        on_event(event)
                    etype = str(event.get("type") or "")
                    if etype in _STEP_TYPES:
                        step_ids.add(str(event.get("id") or len(step_ids)))
                        if etype == "file_edit":
                            edit_attempted = True
                if guard_active and not edit_attempted:
                    elapsed = time.monotonic() - started
                    over_steps = step_budget > 0 and len(step_ids) >= step_budget
                    over_time = (
                        no_progress_seconds > 0
                        and elapsed >= no_progress_seconds
                        and len(step_ids) >= 20
                    )
                    if over_steps or over_time:
                        stopped = "no_progress"
                        if on_event:
                            on_event(
                                make_event(
                                    "completion",
                                    "warning",
                                    (
                                        "No-progress guard: stopped after "
                                        f"{len(step_ids)} steps without an edit "
                                        "attempt"
                                    ),
                                    metadata={
                                        "steps": len(step_ids),
                                        "elapsed_s": int(elapsed),
                                    },
                                )
                            )
                        break
                if part.get("error"):
                    provider_errors.append(str(part["error"]))
                    if self.account_id == "codex":
                        terminal_provider_error = provider_errors[-1]
                        # `turn.failed` is terminal. Waiting for a misbehaving
                        # Codex child to close its pipes leaves the GUI in a false
                        # "Waiting for Codex" state after the provider already
                        # supplied the actionable failure.
                        break
                if part["text"] and not (
                    self.account_id == "claude" and part.get("done") and text_parts
                ):
                    streamed_any = True
                    text_parts.append(part["text"])
                    if on_text:
                        on_text(part["text"])
                if part["cost"] is not None:
                    cost = part["cost"]
            else:
                chunk = payload or ""
                if chunk.strip():
                    if not streamed_any and on_event:
                        on_event(
                            make_event("streaming", "running", "Streaming response")
                        )
                    streamed_any = True
                    text_parts.append(chunk)
                    if on_text:
                        on_text(chunk)

        if terminal_provider_error is not None:
            # A terminal JSONL error is already enough to render the failure.
            # Tree cleanup may block on Windows taskkill, so keep it running in
            # the background rather than holding the UI spinner hostage.
            def _remove_out_file() -> None:
                if out_path:
                    with contextlib.suppress(OSError):
                        Path(out_path).unlink(missing_ok=True)

            _terminate_async(proc, after=_remove_out_file)
            returncode = getattr(proc, "returncode", None)
            if isinstance(returncode, bool) or not isinstance(returncode, int):
                returncode = None
            from opai.provider_contract import normalize_provider_error

            normalized = normalize_provider_error(
                self.account_id,
                terminal_provider_error,
                model=self.model,
                returncode=returncode,
            )
            _invalidate_cache_for_error(self.account_id, normalized)
            return {
                "text": "",
                "cost": cost,
                "error": normalized,
                "returncode": returncode,
            }

        if stopped is not None:
            _terminate(proc)
            if out_path:
                Path(out_path).unlink(missing_ok=True)
            partial = "".join(text_parts).strip()
            if stopped == "timed_out":
                return {"text": partial, "cost": cost, "timed_out": True}
            if stopped == "no_progress":
                # F27: checkpoint, honestly. The paid spend so far is real and
                # is recorded by the caller; the result can never render green.
                return {
                    "text": partial,
                    "cost": cost,
                    "no_progress": True,
                    "stopped_reason": "no_progress_guard",
                    "tool_steps": len(step_ids),
                    "edit_denials": list(session.edit_denials),
                }
            return {"text": partial, "cost": cost, "cancelled": True}

        try:
            returncode = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _terminate(proc)
            returncode = getattr(proc, "returncode", None)

        text = "".join(text_parts).strip()
        if self.account_id == "codex" and out_path:
            try:
                final = Path(out_path).read_text(encoding="utf-8").strip()
                # The JSONL parser already streamed completed agent messages;
                # the out-file is only the fallback when the schema drifted and
                # nothing was captured — never a duplicate.
                if final and not text and not provider_errors:
                    text = final
                    if on_text:
                        on_text(final)
            except OSError:
                pass
            finally:
                Path(out_path).unlink(missing_ok=True)
        from opai.provider_contract import normalize_provider_error

        if provider_errors:
            normalized = normalize_provider_error(
                self.account_id,
                "\n".join(provider_errors),
                model=self.model,
                returncode=returncode,
            )
            _invalidate_cache_for_error(self.account_id, normalized)
            return {
                "text": "",
                "cost": cost,
                "error": normalized,
                "returncode": returncode,
            }
        diagnostic = "\n".join(part for part in diagnostic_parts if part)
        normalized = normalize_provider_error(
            self.account_id,
            diagnostic,
            model=self.model,
            returncode=returncode,
        )
        known_failure = normalized["code"] not in {"UNKNOWN", "NO_RESPONSE"}
        if returncode not in (0, None) and known_failure:
            _invalidate_cache_for_error(self.account_id, normalized)
            return {
                "text": "",
                "cost": cost,
                "error": normalized,
                "returncode": returncode,
            }
        # An auth failure wearing an answer's clothes: the claude CLI can emit
        # "Not logged in · Please run /login" as its RESULT text. Rendering
        # that as a normal assistant message (and receipting it) misleads the
        # user — surface it as the auth error it really is.
        if _is_login_sentinel(text):
            sentinel_error = normalize_provider_error(
                self.account_id,
                text,
                model=self.model,
                returncode=returncode,
            )
            _invalidate_cache_for_error(self.account_id, sentinel_error)
            return {
                "text": "",
                "cost": cost,
                "error": sentinel_error,
                "returncode": returncode,
            }
        # A real answer wins over an unexplained non-zero exit. Provider-native
        # error events and known stderr diagnostics were handled above, so this
        # preserves valid partial answers without promoting error payloads.
        if text:
            return {
                "text": text,
                "cost": cost,
                "returncode": returncode,
                "edit_denials": list(session.edit_denials),
            }
        if returncode not in (0, None) or known_failure:
            _invalidate_cache_for_error(self.account_id, normalized)
            return {
                "text": "",
                "cost": cost,
                "error": normalized,
                "returncode": returncode,
            }
        return {
            "text": "",
            "cost": cost,
            "returncode": returncode,
            "edit_denials": list(session.edit_denials),
        }


def runner_for_account(
    account_id: str, *, model: str | None = None, home: Path | None = None
) -> AccountRunner | None:
    """Build a runner for a connected account, or None if it is not connected."""
    for account in list_connected_accounts(home):
        if account["id"] == account_id and account["connected"]:
            return AccountRunner(account_id, account["cli_path"], model=model)
    return None
