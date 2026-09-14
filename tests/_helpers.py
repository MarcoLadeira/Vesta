"""Shared test fakes + fixtures.

These let hundreds of tests exercise Vesta's account / local / routing paths
without ever launching a real (paid) CLI, starting a model, or hitting the
network. Discovered via ``python -m unittest discover -s tests`` (the tests dir
is on sys.path, so ``from _helpers import ...`` resolves).
"""

from __future__ import annotations

import contextlib
import os
import subprocess  # noqa: S404 - test-only git setup, fixed argv, no shell
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

from opaihub.credentials import PROVIDER_ENV, CredentialStore


# Derived from the production registry rather than restated, so registering a
# new provider cannot silently leave its key un-isolated. That drift is not
# hypothetical: DEEPSEEK_API_KEY was missing here for the whole of #673.
PROVIDER_CREDENTIAL_ENV = set(PROVIDER_ENV.values()) | {
    "GH_TOKEN",
    "GITHUB_TOKEN",
}


class MemoryKeyring:
    """An in-process stand-in for the OS keychain.

    ``priority = 1`` marks it "secure" to ``CredentialStore._secure_backend``,
    so keychain reads and writes are exercised for real — against this dict.
    """

    priority = 1

    def __init__(self, values: dict[tuple[str, str], str] | None = None) -> None:
        self.values: dict[tuple[str, str], str] = dict(values or {})

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def isolated_credential_store(
    environ: dict[str, str] | None = None,
    *,
    backend: Any | None = None,
) -> CredentialStore:
    """A ``CredentialStore`` that cannot reach the developer's real keychain.

    Always pass a backend explicitly. ``CredentialStore(backend=None)`` reads
    as "no keychain" but means the opposite -- ``credentials.py`` substitutes
    ``_default_backend()``, i.e. the live OS keyring. Tests written that way
    pass under pytest only because ``tests/conftest.py`` stubs
    ``_default_backend``; the CI gate runs ``unittest discover``, which never
    loads conftest, so they read (and on failure *print*) real secrets.
    ``tests/test_credential_isolation.py`` enforces this constructor.
    """
    return CredentialStore(
        backend=MemoryKeyring() if backend is None else backend,
        environ={} if environ is None else environ,
    )


def make_repo(
    root: Path, *, files: dict[str, str] | None = None, commit: bool = False
) -> Path:
    """Initialise a throwaway git repo at ``root``.

    Optionally writes (and commits) ``files`` so ``git ls-files`` / ``git diff``
    have real content to report. Inherited ``GIT_CONFIG_*`` variables are
    scrubbed from the git child environment: a header exported with an empty
    value (unrestorable by ``mock.patch.dict`` on Windows) makes git exit 128
    with "missing config value", and a throwaway test repo never needs it.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_CONFIG_")
    }
    subprocess.run(
        ["git", "init", "-q"], cwd=root, check=True, capture_output=True, env=env
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.t"],
        cwd=root,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=root, capture_output=True, env=env
    )
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    for rel, content in (files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if commit:
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, env=env)
        subprocess.run(
            ["git", "commit", "-qm", "init"], cwd=root, capture_output=True, env=env
        )
    return root


@contextlib.contextmanager
def isolated_home():
    """Patch home paths and provider credentials to a throwaway environment.

    Makes account/global-discovery detection hermetic so the developer's real
    home can't leak into a result (the cause of an earlier CI-only failure).
    """
    with tempfile.TemporaryDirectory() as home:
        with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
            for name in PROVIDER_CREDENTIAL_ENV:
                os.environ.pop(name, None)
            yield Path(home)


class FakeAccountRunner:
    """Drop-in for ``opaihub.accounts.AccountRunner`` - never launches a CLI.

    Records every ``complete()`` call (so tests can assert read-only vs edit),
    and can simulate a clean answer, a timeout, or a raised error.
    """

    paid = True

    def __init__(
        self,
        *,
        account_id: str = "claude",
        model: str = "sonnet",
        text: str = "ok answer",
        cost: float | None = 0.0,
        timed_out: bool = False,
        raises: Exception | None = None,
    ) -> None:
        self.account_id = account_id
        self.name = account_id
        self.model = model
        self._text = text
        self._cost = cost
        self._timed_out = timed_out
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, **kwargs})
        if self._raises is not None:
            raise self._raises
        if self._timed_out:
            return {"text": "", "cost": None, "timed_out": True}
        return {"text": self._text, "cost": self._cost}


class FakeStreamingRunner:
    """Drop-in for ``AccountRunner`` exercising the ``stream()`` path.

    Emits scripted activity events + text chunks; can block until cancelled, and
    can simulate a raised error or an empty response. Never launches a CLI.
    Records every ``stream()`` call in ``.calls``.
    """

    paid = True

    def __init__(
        self,
        *,
        account_id: str = "claude",
        model: str = "opus",
        chunks: list[str] | None = None,
        cost: float | None = 0.01,
        block: bool = False,
        raises: Exception | None = None,
        events: list[tuple[str, str]] | None = None,
    ) -> None:
        self.account_id = account_id
        self.name = account_id
        self.model = model
        self._chunks = chunks if chunks is not None else ["Hello ", "world."]
        self._cost = cost
        self._block = block
        self._raises = raises
        self._events = events or [
            ("provider_request", "Connected"),
            ("file_read", "Read file: app.py"),
        ]
        self.calls: list[dict[str, Any]] = []

    def available(self) -> bool:
        return True

    def stream(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        import time

        from opai.activity import make_event

        self.calls.append({"prompt": prompt, **kwargs})
        if self._raises is not None:
            raise self._raises
        on_event = kwargs.get("on_event")
        on_text = kwargs.get("on_text")
        cancel = kwargs.get("cancel")
        for etype, title in self._events:
            if on_event:
                on_event(make_event(etype, "success", title))
        out: list[str] = []
        for chunk in self._chunks:
            if cancel is not None and cancel.is_set():
                return {"text": "".join(out), "cost": None, "cancelled": True}
            out.append(chunk)
            if on_text:
                on_text(chunk)
        while self._block:
            if cancel is not None and cancel.is_set():
                return {"text": "".join(out), "cost": None, "cancelled": True}
            time.sleep(0.02)
        return {"text": "".join(out), "cost": self._cost}


class FakeLocalRunner:
    """Drop-in for ``opaihub.local_runner.LocalRunner`` - no network, no model."""

    def __init__(
        self,
        *,
        name: str = "ollama",
        model: str = "llama3.2",
        answer: str = "local answer",
        available: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._answer = answer
        self._available = available
        self._raises = raises

    def available(self) -> bool:
        return self._available

    def complete(
        self, prompt: str, *, system: str | None = None, timeout: float = 60.0
    ) -> str:
        if self._raises is not None:
            raise self._raises
        return self._answer


class FakeCompleted:
    """Minimal stand-in for ``subprocess.CompletedProcess`` for patching runs."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
