"""GitHub account connector: PAT auth, status, and pull-request creation.

Lets a user authorize OPai with a GitHub personal access token once, then have
coding runs commit, push, and open pull requests as part of the normal
implement flow. Three rules keep it inside OPai's safety contract:

- **The token never touches project files or logs.** It lives in the system
  keychain (via :mod:`opaihub.credentials`) or the ``GITHUB_TOKEN``/``GH_TOKEN``
  environment, and every error path is redacted.
- **Outward actions are opt-in.** ``git push`` and PR creation stay disabled
  until the user runs ``opai github allow-push on`` (persisted consent,
  revocable), even after a token is connected.
- **Network calls are explicit and injectable.** Only ``api.github.com`` is
  contacted, only when the user connects or a run pushes/opens a PR; tests
  inject a fake transport so nothing here needs the real network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed git argv, never a shell
from pathlib import Path
from typing import Any, Callable

from .command_runner import redact
from .credentials import CredentialStore, CredentialStoreUnavailable
from .proc import no_window_kwargs

API_ROOT = "https://api.github.com"
PROVIDER = "github"
_TOKEN_ENV_VARS = ("GITHUB_TOKEN", "GH_TOKEN")


def _config_path() -> Path:
    # Resolved at call time so tests (and changed HOME) are honoured.
    return Path.home() / ".opai" / "github.json"


# method, url, token, json-payload-or-None -> (status_code, parsed_json)
HttpFn = Callable[[str, str, str, "dict[str, Any] | None"], "tuple[int, Any]"]


def _default_http(
    method: str, url: str, token: str, payload: dict[str, Any] | None
) -> tuple[int, Any]:
    """Minimal GitHub API transport. api.github.com only; errors stay parsed."""
    import urllib.error
    import urllib.request

    if not url.startswith(API_ROOT + "/"):
        raise ValueError("Refusing to contact a non-GitHub API host")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(  # noqa: S310 - fixed https host
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "OPai",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        # nosec B310 - scheme and host are pinned to https://api.github.com by
        # the startswith guard above; no file:/custom schemes can reach here.
        with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310
            return int(response.status), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            body = {}
        return int(exc.code), body


def _load_config() -> dict[str, Any]:
    try:
        return dict(json.loads(_config_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _save_config(config: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def stored_github_token() -> tuple[str, str]:
    """Return ``(token, source)``; source is ``env``, ``keychain``, or ``""``."""
    for name in _TOKEN_ENV_VARS:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value, "env"
    try:
        value = CredentialStore().get(PROVIDER)
    except CredentialStoreUnavailable:
        value = None
    if value:
        return str(value), "keychain"
    return "", ""


def connect_github(token: str, *, http: HttpFn = _default_http) -> dict[str, Any]:
    """Validate a PAT against GitHub and store it in the system keychain.

    An invalid token is never stored. The token value never appears in the
    result, the config file, or any error message.
    """
    cleaned = str(token or "").strip()
    if not cleaned:
        return {"connected": False, "error": "A token is required"}
    status_code, body = http("GET", f"{API_ROOT}/user", cleaned, None)
    if status_code != 200 or not isinstance(body, dict) or not body.get("login"):
        return {
            "connected": False,
            "error": (
                "GitHub rejected the token"
                if status_code in (401, 403)
                else f"GitHub validation failed (HTTP {status_code})"
            ),
            "hint": (
                "Create a token at github.com/settings/tokens with `repo` scope "
                "(classic) or Contents+Pull requests read/write (fine-grained)."
            ),
        }
    login = str(body["login"])
    try:
        CredentialStore().set(PROVIDER, cleaned)
        stored = "keychain"
    except CredentialStoreUnavailable:
        # No secure keychain on this machine: never fall back to plaintext.
        # The user can still export GITHUB_TOKEN in their shell profile.
        stored = "env-only"
    config = _load_config()
    config["login"] = login
    config.setdefault("allow_push", False)
    _save_config(config)
    return {
        "connected": True,
        "login": login,
        "stored": stored,
        "allow_push": bool(config["allow_push"]),
        "next_step": (
            "Pushes and PRs stay off until you run: opai github allow-push on"
        ),
    }


def disconnect_github() -> dict[str, Any]:
    """Remove the stored token and revoke push consent."""
    removed = False
    try:
        result = CredentialStore().delete(PROVIDER)
        removed = bool(result.get("deleted"))
    except CredentialStoreUnavailable:
        removed = False
    config = _load_config()
    config.pop("login", None)
    config["allow_push"] = False
    _save_config(config)
    env_token = any(os.environ.get(name) for name in _TOKEN_ENV_VARS)
    return {
        "disconnected": True,
        "keychain_removed": removed,
        "note": (
            "GITHUB_TOKEN/GH_TOKEN is still set in this environment; unset it to "
            "fully disconnect."
            if env_token
            else ""
        ),
    }


def set_push_allowed(allowed: bool) -> dict[str, Any]:
    """Persist the explicit consent that lets runs push and open PRs.

    Consent alone is inert: pushes and PRs also need a connected token. The
    result reports whether the combination is actually ``ready`` and, if not,
    names the missing piece — so ``allow-push on`` never claims a capability the
    run cannot deliver.
    """
    config = _load_config()
    config["allow_push"] = bool(allowed)
    _save_config(config)
    readiness = github_readiness()
    return {
        "allow_push": bool(allowed),
        "connected": readiness["connected"],
        "ready": readiness["ready"],
        "reason": readiness["reason"],
        "next_step": readiness["next_step"],
    }


def push_allowed() -> bool:
    return bool(_load_config().get("allow_push"))


def github_readiness() -> dict[str, Any]:
    """Whether runs can actually push and open PRs, and what's missing if not.

    Two independent gates must both be satisfied: a **connected token** and the
    persisted **allow-push consent**. Every surface (CLI, agent instructions,
    GUI) reads this one truth so the reason a PR can't be opened is always
    specific and actionable — never a bare "allow-push on" when consent is
    already on and the real gap is a missing token.
    """
    token, source = stored_github_token()
    connected = bool(token)
    allow = bool(_load_config().get("allow_push"))
    ready = connected and allow
    if ready:
        reason, next_step = "ready", ""
    elif not connected and not allow:
        reason = "no_token_and_consent_off"
        next_step = (
            "Connect a token (opai github connect --token <PAT>, or set "
            "GITHUB_TOKEN), then run: opai github allow-push on"
        )
    elif not connected:
        reason = "no_token"
        next_step = (
            "Consent is on, but no GitHub token is connected. Connect one: "
            "opai github connect --token <PAT> (or set GITHUB_TOKEN)."
        )
    else:
        reason = "consent_off"
        next_step = "A token is connected. Enable pushes/PRs: opai github allow-push on"
    return {
        "connected": connected,
        "token_source": source,
        "allow_push": allow,
        "ready": ready,
        "reason": reason,
        "next_step": next_step,
    }


def github_status() -> dict[str, Any]:
    """Local-only status: token presence, source, cached login, consent, and
    whether runs are actually ready to push/open PRs."""
    token, source = stored_github_token()
    config = _load_config()
    readiness = github_readiness()
    return {
        "connected": bool(token),
        "token_source": source,
        "login": str(config.get("login") or ""),
        "allow_push": bool(config.get("allow_push")),
        "ready_for_push": readiness["ready"],
        "readiness_reason": readiness["reason"],
        "hint": readiness["next_step"]
        or "GitHub is connected and pushes/PRs are enabled.",
    }


_SLUG_PATTERNS = (
    # https://github.com/owner/repo(.git)  |  git@github.com:owner/repo(.git)
    re.compile(r"^https?://github\.com/(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?/?$"),
    re.compile(r"^git@github\.com:(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?$"),
    re.compile(r"^ssh://git@github\.com/(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?$"),
)


def repo_slug(project_root: Path) -> str:
    """``owner/repo`` from the origin remote, or ``""`` when not GitHub."""
    git_exe = shutil.which("git")
    if not git_exe:
        return ""
    try:
        completed = subprocess.run(  # nosec B603 - fixed git argv, no shell
            [git_exe, "remote", "get-url", "origin"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    url = (completed.stdout or "").strip()
    for pattern in _SLUG_PATTERNS:
        match = pattern.match(url)
        if match:
            return match.group("slug")
    return ""


def create_pull_request(
    project_root: Path,
    *,
    title: str,
    body: str,
    head: str,
    base: str = "main",
    http: HttpFn = _default_http,
) -> dict[str, Any]:
    """Open a PR on the origin GitHub repository. Requires token + consent."""
    readiness = github_readiness()
    if not readiness["ready"]:
        # Name the actual missing gate (token vs consent), not just consent.
        return {
            "ok": False,
            "error": readiness["next_step"],
            "reason": readiness["reason"],
        }
    token, _source = stored_github_token()
    slug = repo_slug(project_root)
    if not slug:
        return {"ok": False, "error": "The origin remote is not a GitHub repository"}
    clean_title = redact(str(title or "").strip())[:256]
    if not clean_title:
        return {"ok": False, "error": "A PR title is required"}
    status_code, response = http(
        "POST",
        f"{API_ROOT}/repos/{slug}/pulls",
        token,
        {
            "title": clean_title,
            "body": redact(str(body or ""))[:20_000],
            "head": str(head or "").strip(),
            "base": str(base or "main").strip(),
        },
    )
    if status_code in (200, 201) and isinstance(response, dict):
        return {
            "ok": True,
            "url": str(response.get("html_url") or ""),
            "number": response.get("number"),
        }
    message = ""
    if isinstance(response, dict):
        message = str(response.get("message") or "")
        errors = response.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                message = f"{message}: {first.get('message') or first}".strip(": ")
    return {
        "ok": False,
        "error": redact(f"GitHub PR creation failed (HTTP {status_code}) {message}"),
    }
