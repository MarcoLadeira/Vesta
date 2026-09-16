"""Secret-safe credentials for direct (non-account-CLI) API providers.

Covers both free-tier providers (kimi/gemini/groq/mistral) and paid
per-token providers (deepseek) — storage is the same secure contract either
way; what a provider *costs* is a property of its operational spec
(``free_models.py`` / ``paid_api_models.py``), not of how its key is stored.

Environment variables remain the highest-precedence deployment mechanism.
Desktop users may store keys in the operating-system credential store through
``keyring``.  No API returns the credential value or a derived fingerprint.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from vesta.legacy import delete_legacy_keyring_entry, migrate_keyring_entry

SERVICE_NAME = "Vesta/free-model-api"
PROVIDER_ENV = {
    "kimi": "MOONSHOT_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    # DeepSeek (#673) is direct-API like the free tier above — same secure
    # storage contract — but it is a *paid* per-token provider, not a free
    # tier. It is registered here (credential storage is tier-agnostic) and in
    # vestahub/paid_api_models.py (operational spec + real pricing), not in
    # free_models.py: that module's contract is genuinely free ($0 actual
    # cost), and folding a paid provider into it would misreport spend.
    "deepseek": "DEEPSEEK_API_KEY",
    # GitHub personal access token for the git/PR connector (#github). Stored
    # through the same keychain-or-env contract as the free-model keys.
    "github": "GITHUB_TOKEN",
}


class CredentialStoreUnavailable(RuntimeError):
    """Raised when no secure operating-system credential backend is usable."""


def _default_backend() -> Any | None:
    try:
        import keyring

        return keyring.get_keyring()
    except (ImportError, RuntimeError):
        return None


class CredentialStore:
    def __init__(
        self,
        *,
        backend: Any | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._backend = backend if backend is not None else _default_backend()
        self._environ = os.environ if environ is None else environ

    def _provider(self, provider: str) -> str:
        value = str(provider or "").strip().lower()
        if value not in PROVIDER_ENV:
            raise ValueError("Unsupported free-model provider")
        return value

    def _secure_backend(self) -> bool:
        if self._backend is None:
            return False
        try:
            return float(getattr(self._backend, "priority", 0)) >= 1
        except (TypeError, ValueError, RuntimeError):
            return False

    def _keychain_value(self, provider: str) -> str:
        if not self._secure_backend():
            return ""
        try:
            value = str(
                self._backend.get_password(SERVICE_NAME, provider) or ""
            ).strip()
        except Exception:  # noqa: BLE001 - backend failures must fail closed
            return ""
        # A key saved before the rename lives under the old service name: move
        # it (write new, then delete old only once the write succeeded).
        return value or migrate_keyring_entry(self._backend, SERVICE_NAME, provider)

    def get(self, provider: str) -> str | None:
        provider = self._provider(provider)
        env_value = str(self._environ.get(PROVIDER_ENV[provider], "") or "").strip()
        if env_value:
            return env_value
        return self._keychain_value(provider) or None

    def status(self, provider: str) -> dict[str, Any]:
        provider = self._provider(provider)
        env_value = str(self._environ.get(PROVIDER_ENV[provider], "") or "").strip()
        keychain = bool(self._keychain_value(provider)) if not env_value else False
        source = "environment" if env_value else ("keychain" if keychain else None)
        return {
            "provider": provider,
            "configured": bool(source),
            "source": source,
            "envKey": PROVIDER_ENV[provider],
            "keychainAvailable": self._secure_backend(),
        }

    def set(self, provider: str, secret: str) -> dict[str, Any]:
        provider = self._provider(provider)
        value = str(secret or "").strip()
        if not value:
            raise ValueError("API key cannot be empty")
        if not self._secure_backend():
            raise CredentialStoreUnavailable(
                "No secure operating-system credential store is available"
            )
        try:
            self._backend.set_password(SERVICE_NAME, provider, value)
        except Exception as exc:  # noqa: BLE001
            raise CredentialStoreUnavailable(
                "The operating-system credential store rejected the key"
            ) from exc
        return self.status(provider)

    def delete(self, provider: str) -> dict[str, Any]:
        provider = self._provider(provider)
        if self._secure_backend():
            # A leftover pre-rename copy would otherwise be migrated back.
            delete_legacy_keyring_entry(self._backend, provider)
            try:
                self._backend.delete_password(SERVICE_NAME, provider)
            except Exception:  # noqa: BLE001 - missing entries are already deleted
                return self.status(provider)
        return self.status(provider)


def credential_statuses() -> list[dict[str, Any]]:
    store = CredentialStore()
    return [store.status(provider) for provider in PROVIDER_ENV]
