"""Tests must never read the developer's real credential store.

Background. ``CredentialStore(backend=None)`` reads as "no keychain" but means
the opposite: ``credentials.py`` substitutes ``_default_backend()``, the live
OS keyring. Six such constructions shipped in ``test_deepseek_provider.py``.
They passed everywhere anyway, because ``tests/conftest.py`` stubs
``_default_backend`` -- and conftest is a *pytest* mechanism. The CI gate
(``scripts/ci_local.py``) runs ``python -m unittest discover -s tests``, which
never loads conftest, so on any machine with a provider key in the keychain the
gate went red and the assertion failure *printed the real secret* into the log.

So the isolation cannot live only in conftest. This module enforces it
structurally, in a way both runners execute:

* every ``CredentialStore(...)`` in ``tests/`` passes an explicit backend;
* the env-var scrub list is derived from ``PROVIDER_ENV``, never restated;
* ``isolated_credential_store`` demonstrably ignores a populated keyring.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest import mock

from _helpers import (
    PROVIDER_CREDENTIAL_ENV,
    MemoryKeyring,
    isolated_credential_store,
)
from opaihub.credentials import PROVIDER_ENV, SERVICE_NAME

TESTS_DIR = Path(__file__).resolve().parent


#: The single sanctioned escape. Applied to a line, it exempts that one
#: construction -- used below to demonstrate that the hazard is real. It is
#: greppable, and ``OnlyOneSanctionedHazardTests`` stops it from spreading.
HAZARD_MARKER = "# credential-isolation: hazard-demo"


def _unsafe_store_constructions(source: str, filename: str) -> list[tuple[str, int]]:
    """Return ``(filename, lineno)`` for each store built without a backend.

    A construction is unsafe when the ``backend`` keyword is absent (the
    default is the live keyring) or is the literal ``None`` (which
    ``credentials.py`` replaces with the live keyring). ``**kwargs`` forwarding
    is treated as safe -- it cannot be judged statically, and no test uses it.
    A call carrying ``HAZARD_MARKER`` anywhere in its line span is exempt --
    the span, not just the first line, because the formatter is free to split
    a long call and push the trailing comment onto the closing parenthesis.
    """
    findings: list[tuple[str, int]] = []
    lines = source.splitlines()

    def exempt(node: ast.Call) -> bool:
        start = max(node.lineno, 1)
        end = min(node.end_lineno or node.lineno, len(lines))
        return any(HAZARD_MARKER in line for line in lines[start - 1 : end])

    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if name != "CredentialStore":
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords}
        if None in keywords:  # **kwargs -- not statically decidable
            continue
        if exempt(node):
            continue
        backend = keywords.get("backend")
        if backend is None:
            findings.append((filename, node.lineno))
        elif isinstance(backend, ast.Constant) and backend.value is None:
            findings.append((filename, node.lineno))
    return findings


class NoTestReadsTheRealKeychainTests(unittest.TestCase):
    def test_every_store_in_the_test_suite_declares_its_backend(self) -> None:
        violations: list[tuple[str, int]] = []
        for path in sorted(TESTS_DIR.glob("*.py")):
            violations += _unsafe_store_constructions(
                path.read_text(encoding="utf-8"), path.name
            )
        self.assertEqual(
            violations,
            [],
            "CredentialStore(backend=None) reads the developer's OS keyring. "
            "Use _helpers.isolated_credential_store() instead. Offenders: "
            + ", ".join(f"{name}:{line}" for name, line in violations),
        )

    def test_the_detector_detects(self) -> None:
        """A analyser that never fires would leave this file permanently green."""
        for snippet in (
            "CredentialStore(backend=None, environ={})",
            "CredentialStore(environ={})",
            "CredentialStore()",
            "credentials.CredentialStore(backend=None)",
        ):
            with self.subTest(snippet=snippet):
                self.assertEqual(
                    len(_unsafe_store_constructions(snippet, "<snippet>")),
                    1,
                    f"failed to flag: {snippet}",
                )

    def test_the_detector_does_not_false_positive(self) -> None:
        for snippet in (
            "CredentialStore(backend=MemoryKeyring(), environ={})",
            "CredentialStore(backend=backend, environ={})",
            "CredentialStore(**kwargs)",
            "SomethingElse(backend=None)",
            "isolated_credential_store()",
        ):
            with self.subTest(snippet=snippet):
                self.assertEqual(
                    _unsafe_store_constructions(snippet, "<snippet>"),
                    [],
                    f"wrongly flagged: {snippet}",
                )

    def test_the_marker_actually_exempts(self) -> None:
        flagged = "CredentialStore(backend=None)"
        self.assertEqual(len(_unsafe_store_constructions(flagged, "<s>")), 1)
        self.assertEqual(
            _unsafe_store_constructions(f"{flagged}  {HAZARD_MARKER}", "<s>"), []
        )

    def test_the_marker_survives_the_formatter_splitting_the_call(self) -> None:
        """``ruff format`` moves the trailing comment to the closing paren."""
        split = f"CredentialStore(\n    backend=None, environ={{}}\n)  {HAZARD_MARKER}"
        self.assertEqual(_unsafe_store_constructions(split, "<s>"), [])

    def test_the_detector_reaches_the_real_test_files(self) -> None:
        """Guard against a glob that silently matches nothing."""
        scanned = sorted(TESTS_DIR.glob("*.py"))
        self.assertGreater(len(scanned), 100)
        self.assertIn("test_deepseek_provider.py", [p.name for p in scanned])


class OnlyOneSanctionedHazardTests(unittest.TestCase):
    """The escape hatch must stay a demonstration, not become a habit."""

    def _marker_sites(self) -> list[str]:
        sites: list[str] = []
        for path in sorted(TESTS_DIR.glob("*.py")):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if HAZARD_MARKER in line and "HAZARD_MARKER" not in line:
                    sites.append(f"{path.name}:{number}")
        return sites

    def test_the_marker_is_used_exactly_once_and_only_here(self) -> None:
        sites = self._marker_sites()
        self.assertEqual(
            len(sites),
            1,
            "the credential-isolation escape hatch is for one hazard "
            f"demonstration in this file; found: {sites}",
        )
        self.assertTrue(sites[0].startswith("test_credential_isolation.py:"), sites)


class ScrubListCannotDriftTests(unittest.TestCase):
    def test_every_registered_provider_env_var_is_scrubbed(self) -> None:
        missing = sorted(set(PROVIDER_ENV.values()) - PROVIDER_CREDENTIAL_ENV)
        self.assertEqual(
            missing,
            [],
            "PROVIDER_CREDENTIAL_ENV must cover every provider in PROVIDER_ENV; "
            f"missing: {missing}",
        )

    def test_deepseek_is_covered(self) -> None:
        """The regression that motivated deriving the set instead of listing it."""
        self.assertIn("DEEPSEEK_API_KEY", PROVIDER_CREDENTIAL_ENV)


class IsolatedStoreIgnoresTheMachineTests(unittest.TestCase):
    """Runner-independent proof: these pass with or without conftest loaded."""

    def _populated_keyring(self) -> MemoryKeyring:
        return MemoryKeyring(
            {
                (SERVICE_NAME, provider): "real-machine-secret"
                for provider in PROVIDER_ENV
            }
        )

    def test_a_populated_keyring_is_not_read(self) -> None:
        keyring = self._populated_keyring()
        with mock.patch(
            "opaihub.credentials._default_backend", return_value=keyring
        ) as default_backend:
            store = isolated_credential_store()
            for provider in PROVIDER_ENV:
                with self.subTest(provider=provider):
                    value = store.get(provider)
                    # Compared as a boolean so a leak is never rendered.
                    self.assertTrue(
                        value is None,
                        f"{provider} resolved to a credential from the machine",
                    )
                    self.assertFalse(store.status(provider)["configured"])
        default_backend.assert_not_called()

    def test_the_ambient_process_environment_is_not_read(self) -> None:
        with mock.patch.dict(
            "os.environ", {name: "real-env-secret" for name in PROVIDER_ENV.values()}
        ):
            store = isolated_credential_store()
            for provider in PROVIDER_ENV:
                with self.subTest(provider=provider):
                    self.assertTrue(store.get(provider) is None)

    def test_an_unpatched_store_would_have_read_the_keyring(self) -> None:
        """Proves the hazard is real, not theoretical -- the bug this file fixes."""
        from opaihub.credentials import CredentialStore

        keyring = self._populated_keyring()
        with mock.patch("opaihub.credentials._default_backend", return_value=keyring):
            leaky = CredentialStore(
                backend=None, environ={}
            )  # credential-isolation: hazard-demo
            self.assertEqual(leaky.get("kimi"), "real-machine-secret")

    def test_explicit_values_still_work(self) -> None:
        store = isolated_credential_store({"DEEPSEEK_API_KEY": "from-env"})
        self.assertEqual(store.get("deepseek"), "from-env")
        self.assertEqual(store.status("deepseek")["source"], "environment")


if __name__ == "__main__":
    unittest.main()
