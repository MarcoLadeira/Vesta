"""Connected AI accounts - route through the CLIs you're already logged into.

OPai's "connect your Claude/Codex account" does not handle API keys or
credentials. It detects the AI CLIs you already authenticated (`claude`,
`codex`) and runs a task through them on demand. Detection is presence-based
(an auth file on disk + the CLI on PATH); execution shells out to the CLI in a
read-only answer mode by default, with an explicit opt-in for edits.

These are paid/cloud calls (they spend your subscription), so OPai treats them
as the gated tier: never auto-fired by Auto routing, recorded to the ledger,
and blocked under panic mode. Nothing here runs unless the user picks the
account model and sends.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - we invoke the user's own logged-in AI CLIs
import sys
import tempfile
from pathlib import Path
from typing import Any


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


def account_models(home: Path | None = None) -> list[dict[str, Any]]:
    """Picker options for connected accounts (paid, run via the user's CLI)."""
    options: list[dict[str, Any]] = []
    for account in list_connected_accounts(home):
        if not account["connected"]:
            continue
        options.append(
            {
                "id": f"account:{account['id']}",
                "label": f"{account['label']} · your account",
                "provider": account["id"],
                "kind": "account",
                "paid": True,
                "vendor": account["vendor"],
            }
        )
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
        self, prompt: str, *, allow_edits: bool, out_file: str | None = None
    ) -> list[str]:
        """Construct the CLI argv. Pure + side-effect free so tests can assert it."""
        if self.account_id == "claude":
            cmd = [self.cli_path, "-p"]
            if self.model:
                cmd += ["--model", self.model]
            if allow_edits:
                cmd += ["--permission-mode", "acceptEdits"]
            cmd.append(prompt)
            return cmd
        if self.account_id == "codex":
            cmd = [
                self.cli_path,
                "exec",
                "--sandbox",
                "workspace-write" if allow_edits else "read-only",
                "--color",
                "never",
                "--skip-git-repo-check",
            ]
            if self.model:
                cmd += ["--model", self.model]
            if out_file:
                cmd += ["--output-last-message", out_file]
            cmd.append(prompt)
            return cmd
        raise ValueError(f"unknown account: {self.account_id}")

    def complete(
        self,
        prompt: str,
        *,
        project_root: Path | None = None,
        allow_edits: bool = False,
        timeout: float = 240.0,
    ) -> str:
        cwd = str(project_root) if project_root else None
        if self.account_id == "codex":
            with tempfile.NamedTemporaryFile(
                "r", suffix=".txt", delete=False, encoding="utf-8"
            ) as handle:
                out_path = handle.name
            cmd = self.build_command(prompt, allow_edits=allow_edits, out_file=out_path)
            proc = _hidden_run(cmd, cwd=cwd, timeout=timeout)
            try:
                answer = Path(out_path).read_text(encoding="utf-8").strip()
            except OSError:
                answer = ""
            finally:
                Path(out_path).unlink(missing_ok=True)
            return answer or (proc.stdout or proc.stderr or "").strip()
        cmd = self.build_command(prompt, allow_edits=allow_edits)
        proc = _hidden_run(cmd, cwd=cwd, timeout=timeout)
        return (proc.stdout or proc.stderr or "").strip()


def runner_for_account(
    account_id: str, *, model: str | None = None, home: Path | None = None
) -> AccountRunner | None:
    """Build a runner for a connected account, or None if it is not connected."""
    for account in list_connected_accounts(home):
        if account["id"] == account_id and account["connected"]:
            return AccountRunner(account_id, account["cli_path"], model=model)
    return None
