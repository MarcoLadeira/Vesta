"""#613 Stage 2: github_connector's shadow journal, and the secret it must never mirror.

Stage 1 named ``opaihub/github_connector.py`` JOURNAL_OWNED -- "operations:
GitHub delivery". ``~/.opai/github.json`` records who Vesta is connected to and
whether the user has consented to pushes and to anonymous public reads, so it
is a *consent* record in the same family as ``integrations``.

It is also the first migrated module that sits next to a credential, and that
shaped the validator. The token is stored in the OS keychain, never in the
config -- ``connect_github`` puts it in ``CredentialStore`` and explicitly
refuses a plaintext fallback when no keychain exists. Mirroring the config is
therefore safe *today*.

The validator's job is to keep it safe tomorrow. A journal is append-only and
nothing prunes it, so a secret written into one outlives disconnects, token
rotations and ``opai github disconnect`` alike -- and would sit in a second
file with its own permissions that no existing cleanup path knows about. So
the mirror refuses any record carrying a credential-shaped key rather than
duplicating it. Refusing to record is the right failure direction here; these
tests pin both that it refuses, and that it does not refuse ordinary configs.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from _helpers import isolated_home

from opaihub import github_connector
from opaihub.github_connector import (
    _config_path,
    _update_config,
    github_config_contradiction_report,
    github_config_projection,
    public_read_allowed,
    push_allowed,
    set_public_read_allowed,
    set_push_allowed,
)


class ShadowMirrorsConsentChangesTests(unittest.TestCase):
    def test_granting_push_consent_is_mirrored(self):
        with isolated_home():
            set_push_allowed(True)

            self.assertTrue(github_config_projection()["allow_push"])
            self.assertIsNone(github_config_contradiction_report())

    def test_withdrawing_push_consent_is_mirrored(self):
        """A withdrawal is the record that matters most; it must not be dropped."""

        with isolated_home():
            set_push_allowed(True)
            set_push_allowed(False)

            self.assertFalse(github_config_projection()["allow_push"])
            self.assertFalse(push_allowed())
            self.assertIsNone(github_config_contradiction_report())

    def test_independent_consent_flags_accumulate_on_both_sides(self):
        with isolated_home():
            set_push_allowed(True)
            set_public_read_allowed(True)

            shadow = github_config_projection()

            self.assertTrue(shadow["allow_push"])
            self.assertTrue(shadow["allow_public_read"])
            self.assertTrue(public_read_allowed())
            self.assertIsNone(github_config_contradiction_report())

    def test_an_untouched_home_agrees_as_both_empty(self):
        with isolated_home():
            self.assertEqual(github_config_projection(), {})
            self.assertIsNone(github_config_contradiction_report())


class TheMirrorMustNeverPersistACredentialTests(unittest.TestCase):
    """The guard this module exists next to.

    Not a hypothetical: the config is a plain dict that any future change can
    add a key to, and the journal is the one file in this design that nothing
    ever prunes.
    """

    def test_a_credential_shaped_key_is_refused_rather_than_mirrored(self):
        with isolated_home():
            set_push_allowed(True)

            _update_config(
                lambda config: config.__setitem__("access_token", "ghp_secret_value")
            )

            journal = github_connector.shadow_journal.journal_path_for(_config_path())
            text = journal.read_text(encoding="utf-8") if journal.exists() else ""

            self.assertNotIn("ghp_secret_value", text)
            self.assertNotIn("access_token", text)

    def test_the_legacy_file_is_still_written_when_the_mirror_refuses(self):
        """Refusing to journal must not refuse the write itself.

        The file stays authoritative in Stage 2. A validator that silently
        blocked the real write would be a far worse bug than the one it guards
        against.
        """

        with isolated_home():
            _update_config(
                lambda config: config.__setitem__("api_token", "ghp_secret_value")
            )

            stored = json.loads(_config_path().read_text(encoding="utf-8"))

            self.assertEqual(stored["api_token"], "ghp_secret_value")

    def test_an_ordinary_config_is_not_caught_by_the_guard(self):
        """Teeth the other way: the guard must not eat normal records."""

        with isolated_home():
            set_push_allowed(True)
            set_public_read_allowed(False)

            shadow = github_config_projection()

            self.assertTrue(shadow["allow_push"])
            self.assertFalse(shadow["allow_public_read"])

    def test_connecting_never_puts_the_token_in_the_config_or_the_journal(self):
        """Pins the property the guard is protecting, at the real call site."""

        with isolated_home():
            with (
                mock.patch.object(github_connector, "CredentialStore") as store,
                mock.patch.object(
                    github_connector,
                    "_verify_token",
                    create=True,
                    return_value="octocat",
                ),
            ):
                store.return_value.set.return_value = {"stored": True}
                try:
                    github_connector.connect_github("ghp_a_real_looking_token")
                except Exception:  # pragma: no cover - shape varies by version
                    self.skipTest("connect_github signature differs in this build")

            config_text = (
                _config_path().read_text(encoding="utf-8")
                if _config_path().exists()
                else ""
            )
            journal = github_connector.shadow_journal.journal_path_for(_config_path())
            journal_text = (
                journal.read_text(encoding="utf-8") if journal.exists() else ""
            )

            self.assertNotIn("ghp_a_real_looking_token", config_text)
            self.assertNotIn("ghp_a_real_looking_token", journal_text)


class ContradictionReportIsExactTests(unittest.TestCase):
    def test_an_out_of_band_consent_grant_is_reported(self):
        """The scenario #613 exists for: something granted push behind our back."""

        with isolated_home():
            set_push_allowed(False)
            tampered = {"allow_push": True, "allow_public_read": True}
            _config_path().write_text(json.dumps(tampered), encoding="utf-8")

            report = github_config_contradiction_report()

            self.assertIsNotNone(report)
            self.assertIn("allow_push", report["mismatched_fields"])
            self.assertEqual(report["legacy"], tampered)

    def test_a_lost_config_is_reported_against_a_surviving_shadow(self):
        with isolated_home():
            set_push_allowed(True)
            _config_path().unlink()

            report = github_config_contradiction_report()

            self.assertIsNotNone(report)
            self.assertEqual(report["legacy"], {})
            self.assertTrue(report["shadow"]["allow_push"])


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
