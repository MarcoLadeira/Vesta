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
from collections.abc import Callable
from pathlib import Path
from typing import Any


_WINDOWED_PREFIX = "pythonw"
_CONSOLE_PREFIX = "python"


def console_interpreter(
    executable: str | None = None,
    *,
    exists: "Callable[[str], bool] | None" = None,
) -> str:
    """Return a *console* Python to hand to ``-m pip``, never the windowed one.

    OPai's desktop app runs under ``pythonw.exe``, and its updater reinstalls
    OPai with ``sys.executable -m pip install -e .``. That is enough to brick
    the desktop icon. For a ``gui_scripts`` entry point pip's vendored distlib
    derives the windowed interpreter by substring substitution --- literally
    ``fn.replace("python", "pythonw")`` --- so an already-windowed
    ``pythonw.exe`` becomes ``pythonww.exe``, which is not a file. The
    generated ``.exe`` then exits 1 with no window, no dialog and no log: the
    icon simply does nothing. Console entry points are damaged more quietly,
    inheriting ``pythonw.exe`` and so printing nothing in a terminal.

    Neither failure is hypothetical --- both were measured on a machine whose
    launchers OPai had reinstalled from inside its own GUI.

    So when the running interpreter is windowed and its console sibling really
    exists, return the sibling. Otherwise return what we were given: a shebang
    that is merely suboptimal beats refusing to install at all.
    """
    current = sys.executable if executable is None else executable
    if not current:
        return ""
    probe = os.path.exists if exists is None else exists
    path = Path(current)
    stem = path.stem
    if not stem.lower().startswith(_WINDOWED_PREFIX):
        return current
    # "pythonw" -> "python", "pythonw3.13" -> "python3.13"; the leading
    # characters keep their original case because only the "w" is dropped.
    console = path.with_name(
        stem[: len(_CONSOLE_PREFIX)] + stem[len(_WINDOWED_PREFIX) :] + path.suffix
    )
    return str(console) if probe(str(console)) else current


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

# Marker exported into every provider CLI's environment. Any `opai` process an
# agent then launches from its own shell (e.g. by following a CLAUDE.md /
# AGENTS.md "run `opai route ...`" recipe) inherits it and can refuse the
# recursive self-invocation (F12). See opai.cli._refuse_if_nested_agent_session.
AGENT_SESSION_ENV = "OPAI_AGENT_SESSION"
# Where the one-shot command-approval handshake lives, pinned for every provider
# child so a CLI's PreToolUse hook reads the same directory OPai wrote to.
COMMAND_CONSENT_DIR_ENV = "OPAI_COMMAND_CONSENT_DIR"
# The run's autonomy level, so the PreToolUse hook the child launches gates
# by the SAME rule the in-process tool executor uses. Without it the hook had
# no idea what mode it was serving and demanded a one-shot approval for every
# push and `gh pr create` even in Full Auto, which is why an account run could
# never finish "push and open a PR".
AUTONOMY_ENV = "OPAI_AUTONOMY"


def provider_child_env(
    provider: str,
    base_env: dict[str, str] | None = None,
    *,
    session_id: str | None = None,
    autonomy: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Return ``(env, removed_names)`` for spawning a provider CLI.

    ``env`` is a copy of the current environment with the provider's
    hijacking variables removed; ``removed_names`` lists (names only, sorted)
    what was stripped so diagnostics can say "ignored session overrides:
    ANTHROPIC_API_KEY" without ever touching a value. Unknown providers get
    the environment unchanged apart from the agent-session marker below.

    Every child env also carries ``OPAI_AGENT_SESSION`` so a nested `opai`
    invocation from inside the agent can detect and refuse recursion (F12), and
    ``OPAI_COMMAND_CONSENT_DIR`` so the PreToolUse hook the child launches reads
    the *same* approval handshake directory this process wrote (Round 5 finding
    1). Both resolve that path from the temp dir by default, but pinning it
    explicitly means a child with a different TMP can never silently miss the
    grant — which would put pushing back to the dead end it used to be.
    An inherited session id is preserved when no explicit one is given.
    """
    from .command_consent import consent_dir

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
    env[AGENT_SESSION_ENV] = session_id or source.get(AGENT_SESSION_ENV) or "1"
    env[COMMAND_CONSENT_DIR_ENV] = str(consent_dir())
    if autonomy is None:
        # Never let a stale value inherited from this process grant a child an
        # autonomy level its caller did not ask for: an unspecified level must
        # fail closed, not silently become whatever the parent was running as.
        env.pop(AUTONOMY_ENV, None)
    else:
        from .command_policy import normalize_autonomy

        env[AUTONOMY_ENV] = normalize_autonomy(autonomy)
    return env, sorted(removed)
