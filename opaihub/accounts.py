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

import json
import os
import queue
import shutil
import subprocess  # nosec B404 - we invoke the user's own logged-in AI CLIs
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_CONNECTION_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CONNECTION_CACHE_LOCK = threading.RLock()
_CONNECTION_CACHE_TTL = 300.0

_INVALID_CODEX_TIER = 'service_tier = "default"'

_AUTH_FAILURE_CODES = {"AUTH_MISSING", "AUTH_INVALID", "AUTH_EXPIRED"}


def invalidate_account_connection_cache(account_id: str) -> None:
    """Discard local probe results after execution proves auth is unusable."""

    with _CONNECTION_CACHE_LOCK:
        stale = [key for key in _CONNECTION_CACHE if key[0] == account_id]
        for key in stale:
            _CONNECTION_CACHE.pop(key, None)


def _invalidate_cache_for_error(account_id: str, error: dict[str, Any]) -> None:
    if str(error.get("code") or "") in _AUTH_FAILURE_CODES:
        invalidate_account_connection_cache(account_id)


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


def _hidden_run(cmd: list[str], *, cwd: str | None, timeout: float):
    """Run a CLI fully in the background - no console window, no stdin prompt.

    On Windows a GUI app (PySide6) that shells out to a console program pops a
    visible terminal; CREATE_NO_WINDOW suppresses it so the chat stays inline.
    stdin is closed so a CLI never blocks waiting for input, and output is
    decoded as UTF-8 with replacement so odd bytes can't crash the GUI.
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
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, **kwargs)  # nosec B603 - argv list, no shell, user's own CLI


def _popen(cmd: list[str], *, cwd: str | None):
    """Start a killable, line-buffered CLI process for streaming reads."""
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
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, **kwargs)  # nosec B603 - argv list, no shell, user's own CLI


def _terminate(proc: Any) -> None:
    """Stop a running process: terminate, then hard-kill if it won't exit."""
    try:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    except (OSError, ValueError):
        pass


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
    return {
        "providerId": str(account.get("id") or "unknown"),
        "displayName": str(account.get("label") or account.get("id") or "Provider"),
        "userFacingName": "OPai",
        "authStatus": auth_status,
        "credentialSource": "user_account",
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
    }


def account_connections(home: Path | None = None) -> list[dict[str, Any]]:
    """Return detected connection state without contacting any provider."""

    return [
        connection_for_account(account) for account in list_connected_accounts(home)
    ]


def test_account_connection(
    account_id: str,
    *,
    home: Path | None = None,
    run: Callable[[list[str]], Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run a provider's local, non-completion auth status command when available."""

    from opai.provider_contract import normalize_provider_error

    cache_key = (account_id, str((home or Path.home()).expanduser().resolve()))
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
        return detected
    checked_at = int(time.time() * 1000)
    if account_id == "copilot":
        detected["lastCheckedAt"] = checked_at
        detected["safeDiagnostic"] = (
            "Account sign-in was detected; this CLI exposes no safe status command."
        )
        return detected
    commands = {
        "claude": [account.get("cli_path") or "claude", "auth", "status"],
        "codex": [account.get("cli_path") or "codex", "login", "status"],
    }
    command = commands.get(account_id)
    if command is None:
        detected["lastCheckedAt"] = checked_at
        detected["safeDiagnostic"] = "No safe status command is available."
        return detected

    execute = run or (lambda argv: _hidden_run(argv, cwd=None, timeout=15.0))
    try:
        proc = execute(command)
    except (OSError, subprocess.SubprocessError) as exc:
        error = normalize_provider_error(account_id, str(exc))
        return connection_for_account(
            account,
            auth_status="provider_unavailable",
            last_checked_at=checked_at,
            error=error,
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
        result = connection_for_account(
            account, auth_status="connected", last_checked_at=checked_at
        )
        if run is None:
            with _CONNECTION_CACHE_LOCK:
                _CONNECTION_CACHE[cache_key] = (time.monotonic(), dict(result))
        return result
    error = status_error
    status = error["authStatus"]
    if status == "unknown":
        status = "disconnected"
    return connection_for_account(
        account,
        auth_status=status,
        last_checked_at=checked_at,
        error=error,
    )


# Claude model aliases the `claude` CLI understands, cheapest-capable first.
CLAUDE_MODELS: list[tuple[str, str]] = [
    ("sonnet", "Sonnet 4.6"),
    ("opus", "Opus 4.8"),
    ("haiku", "Haiku 4.5"),
]

CODEX_MODELS: list[tuple[str, str, str]] = [
    ("gpt-5.5", "GPT-5.5", "best"),
    ("gpt-5.4", "GPT-5.4", "balanced"),
    ("gpt-5.4-mini", "GPT-5.4 Mini", "fast"),
    ("gpt-5.3-codex-spark", "GPT-5.3 Codex Spark", "preview"),
]

# Models the `copilot` CLI exposes via `--model`. Copilot multiplexes Anthropic
# and OpenAI models behind one subscription; balanced/default first.
COPILOT_MODELS: list[tuple[str, str, str]] = [
    ("claude-sonnet-4.6", "Claude Sonnet 4.6", "balanced"),
    ("gpt-5.2", "GPT-5.2", "best"),
    ("claude-haiku-4.5", "Claude Haiku 4.5", "fast"),
]


def _account_options(
    account: dict[str, Any], *, connected: bool
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
            }
            for alias, label in CLAUDE_MODELS
        ]
    if account["id"] == "codex":
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
                "available": connected,
                "disabled_reason": disabled_reason,
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
        }
    ]


def account_models(
    home: Path | None = None,
    *,
    include_unavailable: bool = False,
    accounts: list[dict[str, Any]] | None = None,
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
        options.extend(_account_options(account, connected=connected))
    return options


class AccountRunner:
    """Run one task through a logged-in CLI. Paid/cloud; read-only by default."""

    paid = True

    def __init__(self, account_id: str, cli_path: str, *, model: str | None = None):
        self.account_id = account_id
        self.name = account_id
        self.cli_path = cli_path
        self.model = model or ""

    def available(self) -> bool:
        return bool(self.cli_path) and Path(self.cli_path).exists()

    def build_command(
        self,
        prompt: str,
        *,
        allow_edits: bool = False,
        out_file: str | None = None,
        mode: str | None = None,
        stream: bool = False,
    ) -> list[str]:
        """Construct the CLI argv. Pure + side-effect free so tests can assert it."""
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
                cmd += ["--dangerously-skip-permissions"]
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
            approval = "never" if selected_mode == "full-auto" else "on-request"
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
            cmd.append(prompt)
            return cmd
        if self.account_id == "copilot":
            # `-s` prints only the agent's reply (no banner/stats) and
            # `--no-ask-user` stops it pausing for input in non-interactive use.
            cmd = [self.cli_path, "-s", "--no-ask-user"]
            if self.model:
                cmd += [f"--model={self.model}"]
            if selected_mode in {"safe-auto", "full-auto"}:
                # The only bypass Copilot exposes is all-tools; gate it to edits.
                cmd += ["--allow-all-tools"]
            if selected_mode in {"ask", "plan", "approve-edits"}:
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
    ) -> dict[str, Any]:
        """Run the task; return ``{"text", "cost", "timed_out"?}``.

        Agentic runs (especially Full Auto building a feature) can take many
        minutes, so the timeout is generous. If it is still exceeded the CLI is
        stopped and a clean ``timed_out`` flag is returned rather than raising a
        ``TimeoutExpired`` that would dump the raw command into the chat.
        """
        cwd = str(project_root) if project_root else None
        from opai.provider_contract import normalize_provider_error

        if self.account_id == "codex":
            with tempfile.NamedTemporaryFile(
                "r", suffix=".txt", delete=False, encoding="utf-8"
            ) as handle:
                out_path = handle.name
            cmd = self.build_command(
                prompt, allow_edits=allow_edits, out_file=out_path, mode=mode
            )
            try:
                proc = _hidden_run(cmd, cwd=cwd, timeout=timeout)
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
        cmd = self.build_command(prompt, allow_edits=allow_edits, mode=mode)
        try:
            proc = _hidden_run(cmd, cwd=cwd, timeout=timeout)
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
            if native_error or (returncode != 0 and (known_failure or not text)):
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
            if known_failure and (returncode != 0 or not raw):
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
        from opai.activity import make_event, parse_claude_line, parse_codex_line

        cwd = str(project_root) if project_root else None
        out_path: str | None = None
        if self.account_id == "codex":
            with tempfile.NamedTemporaryFile(
                "r", suffix=".txt", delete=False, encoding="utf-8"
            ) as handle:
                out_path = handle.name
        structured = self.account_id in {"claude", "codex"}
        line_parser = (
            parse_claude_line if self.account_id == "claude" else parse_codex_line
        )
        cmd = self.build_command(
            prompt,
            allow_edits=allow_edits,
            out_file=out_path,
            mode=mode,
            stream=structured,
        )
        try:
            proc = _popen(cmd, cwd=cwd)
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
        streamed_any = False
        open_pipes = 2
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
                if part.get("error"):
                    provider_errors.append(str(part["error"]))
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

        if stopped is not None:
            _terminate(proc)
            if out_path:
                Path(out_path).unlink(missing_ok=True)
            partial = "".join(text_parts).strip()
            if stopped == "timed_out":
                return {"text": partial, "cost": cost, "timed_out": True}
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
        # A real answer wins over an unexplained non-zero exit. Provider-native
        # error events and known stderr diagnostics were handled above, so this
        # preserves valid partial answers without promoting error payloads.
        if text:
            return {"text": text, "cost": cost, "returncode": returncode}
        if returncode not in (0, None) or known_failure:
            _invalidate_cache_for_error(self.account_id, normalized)
            return {
                "text": "",
                "cost": cost,
                "error": normalized,
                "returncode": returncode,
            }
        return {"text": "", "cost": cost, "returncode": returncode}


def runner_for_account(
    account_id: str, *, model: str | None = None, home: Path | None = None
) -> AccountRunner | None:
    """Build a runner for a connected account, or None if it is not connected."""
    for account in list_connected_accounts(home):
        if account["id"] == account_id and account["connected"]:
            return AccountRunner(account_id, account["cli_path"], model=model)
    return None
