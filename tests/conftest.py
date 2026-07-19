from __future__ import annotations

import os

import pytest

from _helpers import PROVIDER_CREDENTIAL_ENV


@pytest.fixture(autouse=True)
def _isolate_provider_credentials(monkeypatch):
    """Keep pytest independent of the developer's provider state."""

    for name in PROVIDER_CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("opaihub.credentials._default_backend", lambda: None)


@pytest.fixture(autouse=True)
def _clean_broken_git_config_env():
    """Scrub a broken inherited GIT_CONFIG_* header before each test.

    A shell that exports ``GIT_CONFIG_COUNT=N`` with an EMPTY
    ``GIT_CONFIG_VALUE_*`` breaks on Windows the moment any
    ``mock.patch.dict(os.environ, ...)`` round-trips it: restoring the empty
    value through ``putenv`` deletes it, leaving a config header git rejects
    ("missing config value GIT_CONFIG_VALUE_0") so every later ``git init``
    in ``make_repo`` exits 128. The inherited header is junk for hermetic
    tests, so it is removed outright rather than restored via monkeypatch.
    """
    for name in list(os.environ):
        if name == "GIT_TERMINAL_PROMPT" or name.startswith("GIT_CONFIG_"):
            os.environ.pop(name, None)
    yield
