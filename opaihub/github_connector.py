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
import subprocess  # nosec B404 - fixed git argv, never a shell
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from .command_runner import redact
from .credentials import CredentialStore, CredentialStoreUnavailable
from .proc import no_window_kwargs
from .safety_gates import resolve_trusted_git_executable

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
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OPai",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(  # noqa: S310 - fixed https host
        url,
        data=data,
        method=method,
        headers=headers,
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
    # Crash-safe: a temp file + atomic replace can never leave a torn config.
    from .atomic_io import atomic_write_text

    atomic_write_text(
        _config_path(), json.dumps(config, indent=2, sort_keys=True) + "\n"
    )


def _update_config(
    mutator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Read-modify-write the connector config under a cross-process lock (#477).

    Concurrent independent updates — connecting a token in one process while
    another flips allow-push — serialize instead of clobbering each other: each
    re-reads the latest config inside the lock, applies only its own change, and
    writes atomically. Returns the persisted config.
    """
    from .atomic_io import interprocess_transaction

    path = _config_path()
    with interprocess_transaction(path):
        config = _load_config()
        mutator(config)
        _save_config(config)
        return config


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

    def _apply(config: dict[str, Any]) -> None:
        config["login"] = login
        config.setdefault("allow_push", False)
        config.setdefault("allow_public_read", False)

    config = _update_config(_apply)
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

    def _apply(config: dict[str, Any]) -> None:
        config.pop("login", None)
        config["allow_push"] = False
        config["allow_public_read"] = False

    _update_config(_apply)
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
    _update_config(lambda config: config.__setitem__("allow_push", bool(allowed)))
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


def set_public_read_allowed(allowed: bool) -> dict[str, Any]:
    """Persist explicit consent for anonymous reads from a public GitHub origin."""

    _update_config(
        lambda config: config.__setitem__("allow_public_read", bool(allowed))
    )
    return {
        "allow_public_read": bool(allowed),
        "note": (
            "Anonymous issue search is enabled for the active public GitHub origin."
            if allowed
            else "Anonymous GitHub reads are disabled."
        ),
    }


def public_read_allowed() -> bool:
    return bool(_load_config().get("allow_public_read"))


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
        "allow_public_read": bool(config.get("allow_public_read")),
        "ready_for_push": readiness["ready"],
        "readiness_reason": readiness["reason"],
        "hint": readiness["next_step"]
        or "GitHub is connected and pushes/PRs are enabled.",
    }


def verify_github_connection(*, http: HttpFn = _default_http) -> dict[str, Any]:
    """Live-check the stored GitHub token for the Connection Doctor (Bug 5).

    GitHub is not an AI-provider adapter, so the generic ``testProvider`` probe
    could only ever return a bare "Unsupported AI provider" with no reason. This
    read-only check reuses the stored token, validates it against the pinned
    ``/user`` endpoint, and shapes its result like the provider probes
    (``connected`` + ``authStatus`` + ``safeDiagnostic`` + ``lastCheckedAt``) so
    the doctor card always shows a concrete, actionable reason. Never stores or
    mutates the token, and the token value never appears in the result.
    """
    token, source = stored_github_token()
    base: dict[str, Any] = {
        "provider": PROVIDER,
        "lastCheckedAt": int(time.time() * 1000),
    }
    if not token:
        return {
            **base,
            "connected": False,
            "authStatus": "not_configured",
            "safeDiagnostic": (
                "No GitHub token connected. Add a personal access token with "
                "repo scope (or set GITHUB_TOKEN), then test again."
            ),
        }
    try:
        status_code, body = http("GET", f"{API_ROOT}/user", token, None)
    except OSError:
        return {
            **base,
            "connected": False,
            "authStatus": "provider_unavailable",
            "safeDiagnostic": (
                "Couldn't reach api.github.com — check your network and retry."
            ),
        }
    if status_code == 200 and isinstance(body, dict) and body.get("login"):
        login = str(body["login"])
        return {
            **base,
            "connected": True,
            "authStatus": "connected",
            "login": login,
            "safeDiagnostic": (
                f"Token valid — signed in as {login} (source: {source})."
            ),
        }
    if status_code in (401, 403):
        return {
            **base,
            "connected": False,
            "authStatus": "invalid",
            "safeDiagnostic": (
                "GitHub rejected the stored token (it may be expired or missing "
                "repo scope). Reconnect a valid personal access token."
            ),
        }
    return {
        **base,
        "connected": False,
        "authStatus": "provider_unavailable",
        "safeDiagnostic": f"GitHub check failed (HTTP {status_code}). Try again shortly.",
    }


_SLUG_PATTERNS = (
    # https://github.com/owner/repo(.git)  |  git@github.com:owner/repo(.git)
    re.compile(r"^https?://github\.com/(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?/?$"),
    re.compile(r"^git@github\.com:(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?$"),
    re.compile(r"^ssh://git@github\.com/(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?$"),
)


def repo_slug(project_root: Path) -> str:
    """``owner/repo`` from the origin remote, or ``""`` when not GitHub."""
    git_exe = resolve_trusted_git_executable(project_root)
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
    from opai.authorship import with_pr_attribution

    clean_title = redact(str(title or "").strip())[:256]
    if not clean_title:
        return {"ok": False, "error": "A PR title is required"}
    status_code, response = http(
        "POST",
        f"{API_ROOT}/repos/{slug}/pulls",
        token,
        {
            "title": clean_title,
            # #contributor: say plainly that OPai opened this. A reviewer
            # should not have to read `git log` to learn whether a human or an
            # assistant wrote what they are reviewing.
            "body": redact(with_pr_attribution(str(body or "")))[:20_000],
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


def _read_context(
    project_root: Path,
) -> tuple[tuple[str, str] | None, dict[str, Any] | None]:
    """Resolve (token, slug) for a read-only GitHub call, or an honest error.

    Reads require a connected token but NOT push consent — they are not outward
    mutations, so a user who connected a token can read PR/issue state.
    """
    token, _source = stored_github_token()
    if not token:
        return None, {
            "ok": False,
            "error": "No GitHub token. Connect with: opai github connect",
        }
    slug = repo_slug(project_root)
    if not slug:
        return None, {
            "ok": False,
            "error": "The origin remote is not a GitHub repository",
        }
    return (token, slug), None


def _issue_search_error(reason: str, message: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "error": redact(message)[:500]}


def _literal_search_term(value: str, *, limit: int) -> str:
    """Quote user text so GitHub search qualifiers cannot change repository scope."""

    cleaned = " ".join(str(value or "").replace("\\", " ").replace('"', " ").split())
    return cleaned[:limit]


def search_issues(
    project_root: Path,
    *,
    query: str = "",
    state: str = "open",
    labels: tuple[str, ...] = (),
    limit: int = 20,
    http: HttpFn = _default_http,
    allow_public: bool = False,
) -> dict[str, Any]:
    """Search bounded issues on the active GitHub origin.

    User query text is quoted as a literal and response text is returned as
    bounded, redacted, explicitly untrusted data. A token authorizes reads;
    anonymous public access is possible only when the caller separately records
    consent and passes ``allow_public=True``.
    """

    slug = repo_slug(project_root)
    if not slug:
        return _issue_search_error(
            "not_github", "The active origin is not a GitHub repository"
        )
    token, _source = stored_github_token()
    if not token and not allow_public:
        return _issue_search_error(
            "consent_required",
            "Connect GitHub or approve public repository reads before searching issues",
        )

    normalized_state = str(state or "open").strip().lower()
    if normalized_state not in {"open", "closed", "all"}:
        return _issue_search_error("invalid_request", "Invalid GitHub issue state")
    try:
        requested = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        return _issue_search_error("invalid_request", "Invalid GitHub issue limit")

    terms = [f"repo:{slug}", "is:issue"]
    if normalized_state != "all":
        terms.append(f"state:{normalized_state}")
    for raw_label in tuple(labels)[:10]:
        label = _literal_search_term(str(raw_label), limit=64)
        if label:
            terms.append(f'label:"{label}"')
    literal_query = _literal_search_term(query, limit=500)
    if literal_query:
        terms.append(f'"{literal_query}"')
    search_query = " ".join(terms)

    issues: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for page in (1, 2):
        page_size = min(30, requested)
        url = f"{API_ROOT}/search/issues?{urlencode({'q': search_query, 'per_page': page_size, 'page': page})}"
        status_code, response = http("GET", url, token, None)
        message = (
            str(response.get("message") or "") if isinstance(response, dict) else ""
        )
        if status_code == 429 or (
            status_code == 403 and "rate limit" in message.lower()
        ):
            return _issue_search_error(
                "rate_limit", "GitHub issue search is rate limited; retry later"
            )
        if status_code in {401, 403}:
            return _issue_search_error(
                "auth", "GitHub authentication does not permit issue search"
            )
        if status_code != 200:
            return _issue_search_error(
                "github_error", f"GitHub issue search failed (HTTP {status_code})"
            )
        raw_items = response.get("items") if isinstance(response, dict) else response
        if not isinstance(raw_items, list):
            return _issue_search_error(
                "github_error", "GitHub issue search returned an invalid response"
            )
        for item in raw_items:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            try:
                number = int(item.get("number"))
            except (TypeError, ValueError):
                continue
            if number < 1 or number in seen_numbers:
                continue
            seen_numbers.add(number)
            item_labels = [
                redact(str(label.get("name") or ""))[:64]
                for label in (item.get("labels") or [])[:10]
                if isinstance(label, dict) and label.get("name")
            ]
            issues.append(
                {
                    "number": number,
                    "title": redact(str(item.get("title") or ""))[:256],
                    "labels": item_labels,
                    "url": f"https://github.com/{slug}/issues/{number}",
                    "state": str(item.get("state") or "unknown")[:16],
                    "excerpt": redact(str(item.get("body") or ""))[:1000],
                }
            )
            if len(issues) >= requested:
                break
        if len(issues) >= requested or len(raw_items) < page_size:
            break
    return {
        "ok": True,
        "issues": issues,
        "content_trust": "untrusted_quoted_data",
        "origin": slug,
    }


def pull_request_status(
    project_root: Path, number: int, *, http: HttpFn = _default_http
) -> dict[str, Any]:
    """Read a pull request's state and CI check summary (read-only, token-gated)."""
    context, error = _read_context(project_root)
    if error:
        return error
    token, slug = context
    code, pr = http("GET", f"{API_ROOT}/repos/{slug}/pulls/{int(number)}", token, None)
    if code != 200 or not isinstance(pr, dict):
        return {"ok": False, "error": f"Could not read PR #{number} (HTTP {code})"}
    checks: dict[str, Any] = {"total": 0, "success": 0, "failed": 0, "pending": 0}
    sha = str((pr.get("head") or {}).get("sha") or "")
    if sha:
        c_code, runs = http(
            "GET", f"{API_ROOT}/repos/{slug}/commits/{sha}/check-runs", token, None
        )
        if c_code == 200 and isinstance(runs, dict):
            for run in runs.get("check_runs") or []:
                checks["total"] += 1
                conclusion = str(run.get("conclusion") or "").lower()
                if run.get("status") != "completed":
                    checks["pending"] += 1
                elif conclusion in {"success", "neutral", "skipped"}:
                    checks["success"] += 1
                else:
                    checks["failed"] += 1
    return {
        "ok": True,
        "number": pr.get("number"),
        "state": str(pr.get("state") or "unknown"),
        "merged": bool(pr.get("merged")),
        "mergeable_state": str(pr.get("mergeable_state") or "unknown"),
        "title": redact(str(pr.get("title") or ""))[:256],
        "url": str(pr.get("html_url") or ""),
        "checks": checks,
    }


def get_issue(
    project_root: Path,
    number: int,
    *,
    include_comments: bool = False,
    http: HttpFn = _default_http,
) -> dict[str, Any]:
    """Read a single issue's title, state, labels, and body (read-only, token-gated).

    With ``include_comments=True`` the issue's discussion is fetched too, so an
    agent solving the issue can see reproduction details and maintainer
    feedback (F19). Comment bodies are redacted before they leave the machine,
    exactly like the issue body.
    """
    context, error = _read_context(project_root)
    if error:
        return error
    token, slug = context
    code, issue = http(
        "GET", f"{API_ROOT}/repos/{slug}/issues/{int(number)}", token, None
    )
    if code != 200 or not isinstance(issue, dict):
        return {"ok": False, "error": f"Could not read issue #{number} (HTTP {code})"}
    labels = [
        str(label.get("name"))
        for label in (issue.get("labels") or [])
        if isinstance(label, dict) and label.get("name")
    ]
    result: dict[str, Any] = {
        "ok": True,
        "number": issue.get("number"),
        "state": str(issue.get("state") or "unknown"),
        "title": redact(str(issue.get("title") or ""))[:256],
        "body": redact(str(issue.get("body") or ""))[:5000],
        "labels": labels,
        "url": str(issue.get("html_url") or ""),
    }
    if include_comments:
        c_code, raw_comments = http(
            "GET",
            f"{API_ROOT}/repos/{slug}/issues/{int(number)}/comments?per_page=50",
            token,
            None,
        )
        if c_code != 200 or not isinstance(raw_comments, list):
            return {
                "ok": False,
                "error": f"Could not read issue #{number} comments (HTTP {c_code})",
            }
        comments: list[dict[str, Any]] = []
        for item in raw_comments[:50]:
            if not isinstance(item, dict):
                continue
            author = item.get("user")
            comments.append(
                {
                    "author": str(author.get("login") or "")
                    if isinstance(author, dict)
                    else "",
                    "created_at": str(item.get("created_at") or ""),
                    "body": redact(str(item.get("body") or ""))[:2000],
                }
            )
        result["comments"] = comments
    return result


def add_comment(
    project_root: Path, number: int, body: str, *, http: HttpFn = _default_http
) -> dict[str, Any]:
    """Comment on an issue or PR. Outward action: needs token AND push consent.

    GitHub's issue-comments endpoint serves pull requests too, so this one call
    comments on either. The body is redacted before it leaves the machine.
    """
    readiness = github_readiness()
    if not readiness["ready"]:
        return {
            "ok": False,
            "error": readiness["next_step"],
            "reason": readiness["reason"],
        }
    token, _source = stored_github_token()
    slug = repo_slug(project_root)
    if not slug:
        return {"ok": False, "error": "The origin remote is not a GitHub repository"}
    clean = redact(str(body or "").strip())[:60_000]
    if not clean:
        return {"ok": False, "error": "A comment body is required"}
    code, response = http(
        "POST",
        f"{API_ROOT}/repos/{slug}/issues/{int(number)}/comments",
        token,
        {"body": clean},
    )
    if code in (200, 201) and isinstance(response, dict):
        return {
            "ok": True,
            "url": str(response.get("html_url") or ""),
            "id": response.get("id"),
        }
    return {"ok": False, "error": redact(f"Comment failed (HTTP {code})")}


def request_reviewers(
    project_root: Path,
    number: int,
    reviewers: list[str],
    *,
    http: HttpFn = _default_http,
) -> dict[str, Any]:
    """Request reviewers on a PR. Outward action: needs token AND push consent."""
    readiness = github_readiness()
    if not readiness["ready"]:
        return {
            "ok": False,
            "error": readiness["next_step"],
            "reason": readiness["reason"],
        }
    token, _source = stored_github_token()
    slug = repo_slug(project_root)
    if not slug:
        return {"ok": False, "error": "The origin remote is not a GitHub repository"}
    names = [str(name).strip() for name in (reviewers or []) if str(name).strip()][:15]
    if not names:
        return {"ok": False, "error": "At least one reviewer login is required"}
    code, response = http(
        "POST",
        f"{API_ROOT}/repos/{slug}/pulls/{int(number)}/requested_reviewers",
        token,
        {"reviewers": names},
    )
    if code in (200, 201):
        return {"ok": True, "requested": names}
    message = ""
    if isinstance(response, dict):
        message = str(response.get("message") or "")
    return {
        "ok": False,
        "error": redact(f"Requesting reviewers failed (HTTP {code}) {message}".strip()),
    }
