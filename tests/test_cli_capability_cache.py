"""Provider CLI capability verdicts must survive a process restart.

The consistency defect: what a CLI can do was cached only in memory, and an
unknown CLI was enumerated *optimistically*. So on every cold start the model
picker offered a Codex whose CLI could not run anything, the user picked it, and
the run hard-failed with "requires a newer version of Codex". Whether the model
looked usable depended on whether something earlier in that same process had
happened to probe — the definition of "sometimes it works, sometimes it doesn't".
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest import mock

from opaihub import accounts


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class CliProbePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.cli = self.home / "codex.cmd"
        self.cli.write_text("binary", encoding="utf-8")
        self.account = {"id": "codex", "cli_path": str(self.cli)}
        accounts._CLI_VERSION_CACHE.clear()
        accounts._CLI_CAPABILITY_CACHE.clear()

    def tearDown(self) -> None:
        accounts._CLI_VERSION_CACHE.clear()
        accounts._CLI_CAPABILITY_CACHE.clear()
        self._tmp.cleanup()

    def test_a_probed_version_is_readable_after_the_caches_are_cleared(self) -> None:
        accounts._write_cli_probe(
            str(self.cli), "version", "codex-cli 0.128.0", home=self.home
        )
        # A new process starts with empty in-memory caches.
        accounts._CLI_VERSION_CACHE.clear()
        self.assertEqual(
            accounts._account_cli_version(self.account, home=self.home),
            "codex-cli 0.128.0",
        )

    def test_a_changed_binary_invalidates_the_stored_verdict(self) -> None:
        accounts._write_cli_probe(
            str(self.cli), "version", "codex-cli 0.128.0", home=self.home
        )
        accounts._CLI_VERSION_CACHE.clear()
        # An upgrade rewrites the executable; the old verdict must not survive it.
        self.cli.write_text("a different, newer binary", encoding="utf-8")
        self.assertIsNone(
            accounts._read_cli_probe(str(self.cli), "version", home=self.home)
        )

    def test_a_stale_verdict_expires(self) -> None:
        accounts._write_cli_probe(
            str(self.cli), "version", "codex-cli 0.128.0", home=self.home, now=1000.0
        )
        fresh = accounts._read_cli_probe(
            str(self.cli), "version", home=self.home, now=1000.0
        )
        self.assertEqual(fresh, "codex-cli 0.128.0")
        stale = accounts._read_cli_probe(
            str(self.cli),
            "version",
            home=self.home,
            now=1000.0 + accounts._CLI_PROBE_TTL_SECONDS + 1,
        )
        self.assertIsNone(stale)

    def test_probing_writes_the_verdict_for_the_next_process(self) -> None:
        version = accounts._account_cli_version(
            self.account,
            run=lambda _argv: _Result("codex-cli 0.128.0"),
            home=self.home,
        )
        self.assertEqual(version, "codex-cli 0.128.0")
        # An injected runner is a test double, so it must not poison the store.
        self.assertIsNone(
            accounts._read_cli_probe(str(self.cli), "version", home=self.home)
        )

    def test_unreadable_binaries_are_never_trusted(self) -> None:
        missing = str(self.home / "not-installed")
        accounts._write_cli_probe(missing, "version", "whatever", home=self.home)
        self.assertIsNone(accounts._read_cli_probe(missing, "version", home=self.home))


class NoBlockingProbeTests(unittest.TestCase):
    """Enumeration must never launch a provider CLI.

    A subprocess here blocks whatever is enumerating — the settings page,
    the model picker — for up to a timeout per CLI. QAR8-27 briefly added a
    synchronous fallback probe to get cold-start honesty, which bought
    first-launch accuracy at the price of blocking every enumeration with a
    cold cache. `test_settings_payload_includes_github` caught it, but only
    when run alone: in a full suite an earlier test warmed the in-process
    cache and hid it. This asserts it with the cache explicitly cold, so
    order can never mask it again.
    """

    def setUp(self) -> None:
        accounts._CLI_VERSION_CACHE.clear()
        accounts._CLI_CAPABILITY_CACHE.clear()

    def tearDown(self) -> None:
        accounts._CLI_VERSION_CACHE.clear()
        accounts._CLI_CAPABILITY_CACHE.clear()

    def test_enumeration_with_a_cold_cache_never_probes(self) -> None:
        detected = [
            {
                "id": "codex",
                "label": "Codex",
                "vendor": "OpenAI",
                "connected": True,
                "cli_path": "/nonexistent/codex",
            },
            {
                "id": "copilot",
                "label": "GitHub Copilot",
                "vendor": "GitHub",
                "connected": True,
                "cli_path": "/nonexistent/copilot",
            },
        ]
        boom = AssertionError("enumeration launched a provider CLI")
        with (
            mock.patch.object(accounts, "_account_cli_version", side_effect=boom),
            mock.patch.object(
                accounts, "_copilot_supports_scoped_permissions", side_effect=boom
            ),
        ):
            accounts.account_models(accounts=detected)

    def test_an_explicit_inspection_still_probes(self) -> None:
        # The probe is not removed, only moved: callers that already run
        # off the UI thread opt in and persist the verdict for the rest.
        detected = [
            {
                "id": "codex",
                "label": "Codex",
                "vendor": "OpenAI",
                "connected": True,
                "cli_path": "/nonexistent/codex",
            },
        ]
        calls: list[int] = []
        with mock.patch.object(
            accounts,
            "_account_cli_version",
            side_effect=lambda *a, **k: calls.append(1) or "codex-cli 0.128.0",
        ):
            accounts.account_models(accounts=detected, inspect_cli_capabilities=True)
        self.assertEqual(len(calls), 1)


class ColdStartHonestyTests(unittest.TestCase):
    """A cold-start picker must not offer a CLI that cannot run the request."""

    def test_an_outdated_codex_is_unavailable_with_the_exact_remedy(self) -> None:
        options = accounts._account_options(
            {"id": "codex", "label": "Codex", "vendor": "OpenAI"},
            connected=True,
            account_type="api_key",
            cli_version="codex-cli 0.128.0",
        )
        self.assertTrue(options)
        for option in options:
            self.assertFalse(option["available"])
            self.assertIn("npm install -g @openai/codex", option["disabled_reason"])
            self.assertFalse(option["repo_editing"])

    def test_a_current_codex_stays_fully_available(self) -> None:
        options = accounts._account_options(
            {"id": "codex", "label": "Codex", "vendor": "OpenAI"},
            connected=True,
            account_type="api_key",
            cli_version="codex-cli 0.143.0",
        )
        for option in options:
            self.assertTrue(option["available"])
            self.assertTrue(option["repo_editing"])

    def test_copilot_without_scoped_tools_is_marked_write_incapable(self) -> None:
        options = accounts._account_options(
            {"id": "copilot", "label": "GitHub Copilot", "vendor": "GitHub"},
            connected=True,
            copilot_scoped_editing=False,
        )
        self.assertTrue(options)
        for option in options:
            # Still selectable: Ask and Plan work fine. It just cannot be
            # handed repository write access.
            self.assertTrue(option["available"])
            self.assertFalse(option["repo_editing"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
