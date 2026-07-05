"""Subprocess helpers that never flash a console window on Windows.

A GUI app (PySide6 / QtWebEngine) that shells out to a console program pops a
visible terminal for a split second on Windows. OPai collects git evidence
(status / diff), selects tests, and probes providers on *every* message, so
without suppression that becomes a burst of terminals flashing on screen each
time you send. ``CREATE_NO_WINDOW`` stops the child from ever getting a console.

``opaihub.accounts`` already applies this to the provider CLIs it launches;
this module is the shared source for every *other* subprocess OPai spawns, so
the behaviour is consistent and unit-testable in one place.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - this module only computes flags, never runs a shell
import sys
from typing import Any


def no_window_kwargs() -> dict[str, Any]:
    """Return ``subprocess`` kwargs that stop a child console window flashing.

    On Windows this is ``{"creationflags": CREATE_NO_WINDOW}``; on every other
    platform there is no console-window concept, so it is an empty dict that
    leaves the call untouched.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


# --------------------------------------------------------------------------- #
# Sanitized child environments for provider CLIs (the auth-truth fix).
#
# OPai is often launched from a terminal where another AI session is running
# (Claude Code, Codex, an agent harness). Those parents export session
# variables — CLAUDECODE, CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH, stale
# ANTHROPIC_API_KEY/OPENAI_API_KEY overrides, custom base URLs — that a child
# provider CLI then inherits. The result is the exact reported failure class:
# `claude auth status` says "logged in" (it reads the parent session's
# markers) while the real completion 401s or reports "Not logged in", because
# the child deferred auth to a host session that does not exist inside OPai.
#
# OPai's account connectors mean ONE thing: "route through the CLI's own
# persisted sign-in". So every provider CLI spawn gets a copy of the
# environment with the hijacking variables removed. Only NAMES of removed
# variables are ever reported — values are never read, logged, or returned.
# --------------------------------------------------------------------------- #
_ENV_DENY_EXACT: dict[str, frozenset[str]] = {
    "claude": frozenset(
        {
            # Auth/endpoint overrides that beat the CLI's own OAuth login.
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_CUSTOM_HEADERS",
            # Model overrides that would silently beat OPai's --model choice.
            "ANTHROPIC_MODEL",
            "ANTHROPIC_SMALL_FAST_MODEL",
            # Parent-session markers ("a host manages your session/tokens").
            "CLAUDECODE",
            "CLAUDE_AGENT_SDK_VERSION",
        }
    ),
    "codex": frozenset(
        {
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORGANIZATION",
        }
    ),
    # Copilot: GH_TOKEN/GITHUB_TOKEN are legitimate primary auth signals for
    # the copilot CLI (accounts.py treats them as "connected"), so nothing is
    # stripped for it.
    "copilot": frozenset(),
}

_ENV_DENY_PREFIXES: dict[str, tuple[str, ...]] = {
    # Every CLAUDE_CODE_* var is parent-session state (SDK_HAS_OAUTH_REFRESH,
    # CHILD_SESSION, SESSION_ID, ENTRYPOINT, OAUTH_SCOPES, ...). None of them
    # belong in a fresh CLI run that OPai owns.
    "claude": ("CLAUDE_CODE_",),
    "codex": (),
    "copilot": (),
}


def provider_child_env(
    provider: str, base_env: dict[str, str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Return ``(env, removed_names)`` for spawning a provider CLI.

    ``env`` is a copy of the current environment with the provider's
    hijacking variables removed; ``removed_names`` lists (names only, sorted)
    what was stripped so diagnostics can say "ignored session overrides:
    ANTHROPIC_API_KEY" without ever touching a value. Unknown providers get
    the environment unchanged.
    """
    source = dict(os.environ if base_env is None else base_env)
    exact = _ENV_DENY_EXACT.get(str(provider or "").lower(), frozenset())
    prefixes = _ENV_DENY_PREFIXES.get(str(provider or "").lower(), ())
    removed: list[str] = []
    env: dict[str, str] = {}
    for name, value in source.items():
        if name in exact or any(name.startswith(prefix) for prefix in prefixes):
            removed.append(name)
            continue
        env[name] = value
    return env, sorted(removed)
