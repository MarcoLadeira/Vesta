from __future__ import annotations

import pytest

from _helpers import PROVIDER_CREDENTIAL_ENV


@pytest.fixture(autouse=True)
def _isolate_provider_credentials(monkeypatch):
    """Keep pytest independent of the developer's provider state."""

    for name in PROVIDER_CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("opaihub.credentials._default_backend", lambda: None)
