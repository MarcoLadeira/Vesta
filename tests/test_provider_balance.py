"""Tests for the per-provider credit/balance truth (Credits & Balance).

Covers the store (observed exhaustion, manual entry, live probes, TTLs), the
UI snapshot shape, the Auto-router exclusion, the model-catalog removal, the
run-outcome recording hooks, and the settings payload — the full path from
"Moonshot refused for insufficient balance" to "Kimi is hidden from the picker
and skipped by Auto, with an explanation".
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from opaihub import provider_balance as pb


class _Root:
    """A throwaway project root per test."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


class ExhaustionRecordTests(unittest.TestCase):
    def test_default_is_not_exhausted(self):
        with _Root() as root:
            self.assertFalse(pb.is_exhausted(root, "kimi"))

    def test_record_exhausted_marks_provider_out(self):
        with _Root() as root:
            pb.record_exhausted(root, "kimi")
            self.assertTrue(pb.is_exhausted(root, "kimi"))

    def test_observed_exhaustion_sets_amount_to_zero(self):
        with _Root() as root:
            pb.record_exhausted(root, "kimi")
            snap = pb.balance_snapshot(root, "kimi")
            self.assertEqual(snap["status"], "out")
            self.assertEqual(snap["amount"], 0.0)
            self.assertEqual(snap["source"], "observed")

    def test_success_clears_exhaustion(self):
        with _Root() as root:
            pb.record_exhausted(root, "kimi")
            pb.record_success(root, "kimi")
            self.assertFalse(pb.is_exhausted(root, "kimi"))
            # The disproven observed zero is dropped, not kept as fake truth.
            self.assertIsNone(pb.balance_snapshot(root, "kimi")["amount"])

    def test_exhaustion_expires_after_ttl(self):
        with _Root() as root:
            stale = time.time() - pb.EXHAUSTED_TTL_SECONDS - 1
            pb.record_exhausted(root, "kimi", now=stale)
            self.assertFalse(pb.is_exhausted(root, "kimi"))

    def test_exhaustion_within_ttl_still_counts(self):
        with _Root() as root:
            recent = time.time() - pb.EXHAUSTED_TTL_SECONDS / 2
            pb.record_exhausted(root, "kimi", now=recent)
            self.assertTrue(pb.is_exhausted(root, "kimi"))

    def test_local_and_auto_sentinels_are_never_recorded(self):
        with _Root() as root:
            pb.record_exhausted(root, "auto")
            pb.record_exhausted(root, "local")
            pb.record_exhausted(root, "")
            self.assertFalse(pb._load(root))

    def test_success_on_unknown_provider_is_a_noop(self):
        with _Root() as root:
            pb.record_success(root, "kimi")  # nothing recorded, must not raise
            self.assertFalse(pb._load(root))


class ManualBalanceTests(unittest.TestCase):
    def test_manual_balance_is_stored_and_snapshotted(self):
        with _Root() as root:
            snap = pb.set_manual_balance(root, "claude", 85.0, currency="EUR")
            self.assertEqual(snap["status"], "ok")
            self.assertEqual(snap["amount"], 85.0)
            self.assertEqual(snap["currency"], "EUR")
            self.assertEqual(snap["source"], "manual")
            self.assertEqual(snap["percent"], 100.0)

    def test_currency_is_normalized_and_bad_currency_falls_back(self):
        with _Root() as root:
            self.assertEqual(
                pb.set_manual_balance(root, "claude", 1, currency="eur")["currency"],
                "EUR",
            )
            self.assertEqual(
                pb.set_manual_balance(root, "claude", 1, currency="<x>")["currency"],
                "USD",
            )

    def test_zero_manual_balance_marks_exhausted(self):
        with _Root() as root:
            snap = pb.set_manual_balance(root, "kimi", 0)
            self.assertEqual(snap["status"], "out")
            self.assertTrue(pb.is_exhausted(root, "kimi"))

    def test_positive_manual_balance_clears_exhaustion(self):
        with _Root() as root:
            pb.record_exhausted(root, "kimi")
            pb.set_manual_balance(root, "kimi", 20)
            self.assertFalse(pb.is_exhausted(root, "kimi"))
            self.assertEqual(pb.balance_snapshot(root, "kimi")["status"], "ok")

    def test_low_status_below_15_percent_of_reference(self):
        with _Root() as root:
            pb.set_manual_balance(root, "gemini", 100)
            snap = pb.set_manual_balance(root, "gemini", 10)
            self.assertEqual(snap["status"], "low")
            self.assertEqual(snap["percent"], 10.0)

    def test_invalid_amounts_are_rejected(self):
        with _Root() as root:
            for bad in (-1, float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    pb.set_manual_balance(root, "claude", bad)
            with self.assertRaises(ValueError):
                pb.set_manual_balance(root, "", 5)

    def test_clear_manual_balance_removes_manual_truth(self):
        with _Root() as root:
            pb.set_manual_balance(root, "claude", 85)
            pb.clear_manual_balance(root, "claude")
            snap = pb.balance_snapshot(root, "claude")
            self.assertEqual(snap["status"], "unknown")
            self.assertIsNone(snap["amount"])

    def test_clear_manual_zero_also_clears_its_exhaustion(self):
        with _Root() as root:
            pb.set_manual_balance(root, "kimi", 0)
            pb.clear_manual_balance(root, "kimi")
            self.assertFalse(pb.is_exhausted(root, "kimi"))


class SnapshotShapeTests(unittest.TestCase):
    def test_unknown_snapshot_shape(self):
        with _Root() as root:
            snap = pb.balance_snapshot(root, "mistral")
            self.assertEqual(snap["status"], "unknown")
            self.assertIsNone(snap["amount"])
            self.assertIsNone(snap["percent"])
            self.assertEqual(snap["source"], "none")
            self.assertIn("rechargeHint", snap)
            self.assertIn("displayName", snap)

    def test_percent_is_bounded_0_to_100(self):
        with _Root() as root:
            pb.set_manual_balance(root, "claude", 50)
            # Reference stays at the max seen (50); a bigger amount caps at 100.
            entry = pb._load(root)["claude"]
            entry["amount"] = 80.0
            pb._save(root, {"claude": entry})
            self.assertEqual(pb.balance_snapshot(root, "claude")["percent"], 100.0)

    def test_store_file_contains_no_free_text(self):
        """Only numbers, closed slugs, and currency codes may be persisted."""
        with _Root() as root:
            pb.record_exhausted(root, "kimi", source="HTTP 429 sk-secret-token leaked!")
            raw = (root / ".opaihub" / "health" / "provider_balance.json").read_text(
                encoding="utf-8"
            )
            data = json.loads(raw)

            # Scan the persisted *strings*, not the serialized bytes. The file
            # also carries a float epoch, and "429" occurs inside an ordinary
            # timestamp about once in a few hundred runs -- 1786442940 is one,
            # and it failed this test on hosted CI. That is the clock, not a
            # leak, and a leak check that fires on the clock trains people to
            # rerun the job. Numbers are allowed here by construction; what
            # must never appear is caller free text.
            def _strings(value: object):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict):
                    for key, item in value.items():
                        yield str(key)
                        yield from _strings(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from _strings(item)

            persisted = list(_strings(data))
            for text in persisted:
                for token in ("sk-secret", "429", "leaked", "HTTP"):
                    self.assertNotIn(token, text)
            self.assertEqual(data["kimi"]["exhausted_source"], "observed")

    def test_display_names_and_hints_cover_known_providers(self):
        for provider in (
            "kimi",
            "claude",
            "codex",
            "copilot",
            "gemini",
            "groq",
            "mistral",
        ):
            self.assertTrue(pb.provider_display_name(provider))
            self.assertIn(provider, pb.RECHARGE_HINTS)


class LiveProbeTests(unittest.TestCase):
    def _with_key(self):
        store = mock.MagicMock()
        store.get.return_value = "test-key"
        return mock.patch("opaihub.credentials.CredentialStore", return_value=store)

    def test_probe_updates_amount_and_reference(self):
        with _Root() as root, self._with_key():
            with mock.patch.object(pb, "_probe_moonshot", return_value=(12.5, "USD")):
                snap = pb.probe_balance(root, "kimi", force=True)
        self.assertEqual(snap["status"], "ok")
        self.assertEqual(snap["amount"], 12.5)
        self.assertEqual(snap["source"], "provider")
        self.assertTrue(snap["supportsLiveBalance"])

    def test_probe_zero_marks_exhausted(self):
        with _Root() as root, self._with_key():
            with mock.patch.object(pb, "_probe_moonshot", return_value=(0.0, "USD")):
                snap = pb.probe_balance(root, "kimi", force=True)
            self.assertEqual(snap["status"], "out")
            self.assertTrue(pb.is_exhausted(root, "kimi"))

    def test_probe_positive_clears_prior_exhaustion(self):
        with _Root() as root, self._with_key():
            pb.record_exhausted(root, "kimi")
            with mock.patch.object(pb, "_probe_moonshot", return_value=(9.0, "USD")):
                pb.probe_balance(root, "kimi", force=True)
            self.assertFalse(pb.is_exhausted(root, "kimi"))

    def test_probe_respects_ttl_cache(self):
        with _Root() as root, self._with_key():
            fetch = mock.Mock(return_value=(5.0, "USD"))
            with mock.patch.object(pb, "_probe_moonshot", fetch):
                pb.probe_balance(root, "kimi", force=True)
                pb.probe_balance(root, "kimi")  # within TTL — no second fetch
            self.assertEqual(fetch.call_count, 1)

    def test_probe_force_bypasses_ttl(self):
        with _Root() as root, self._with_key():
            fetch = mock.Mock(return_value=(5.0, "USD"))
            with mock.patch.object(pb, "_probe_moonshot", fetch):
                pb.probe_balance(root, "kimi", force=True)
                pb.probe_balance(root, "kimi", force=True)
            self.assertEqual(fetch.call_count, 2)

    def test_probe_failure_keeps_previous_truth(self):
        with _Root() as root, self._with_key():
            pb.set_manual_balance(root, "kimi", 30)
            with mock.patch.object(pb, "_probe_moonshot", return_value=None):
                snap = pb.probe_balance(root, "kimi", force=True)
            self.assertEqual(snap["amount"], 30)
            self.assertEqual(snap["source"], "manual")

    def test_probe_without_key_is_a_noop(self):
        with _Root() as root:
            store = mock.MagicMock()
            store.get.return_value = ""
            fetch = mock.Mock(return_value=(5.0, "USD"))
            with (
                mock.patch("opaihub.credentials.CredentialStore", return_value=store),
                mock.patch.object(pb, "_probe_moonshot", fetch),
            ):
                snap = pb.probe_balance(root, "kimi", force=True)
            fetch.assert_not_called()
            self.assertEqual(snap["status"], "unknown")

    def test_unsupported_provider_never_probes(self):
        self.assertFalse(pb.supports_live_balance("claude"))
        self.assertFalse(pb.supports_live_balance("gemini"))
        self.assertTrue(pb.supports_live_balance("kimi"))
        with _Root() as root:
            snap = pb.probe_balance(root, "claude", force=True)
            self.assertEqual(snap["status"], "unknown")

    def test_moonshot_parser_reads_available_balance(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps(
            {"code": 0, "data": {"available_balance": 3.25}, "status": True}
        ).encode("utf-8")
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(pb._probe_moonshot("key"), (3.25, "USD"))

    def test_moonshot_parser_tolerates_garbage(self):
        response = mock.MagicMock()
        response.read.return_value = b"not json"
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertIsNone(pb._probe_moonshot("key"))
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertIsNone(pb._probe_moonshot("key"))


class BalanceOverviewTests(unittest.TestCase):
    def test_overview_dedupes_and_orders(self):
        with _Root() as root:
            pb.set_manual_balance(root, "claude", 85, currency="EUR")
            overview = pb.balance_overview(
                root,
                [
                    {"provider": "claude", "kind": "account", "configured": True},
                    {"provider": "claude", "kind": "account", "configured": True},
                    {"provider": "kimi", "kind": "free", "configured": False},
                ],
            )
        self.assertEqual([o["provider"] for o in overview], ["claude", "kimi"])
        self.assertEqual(overview[0]["amount"], 85)
        self.assertEqual(overview[1]["status"], "not_configured")

    def test_overview_probe_only_hits_configured_live_providers(self):
        with _Root() as root:
            fetch = mock.Mock(return_value=(5.0, "USD"))
            store = mock.MagicMock()
            store.get.return_value = "key"
            with (
                mock.patch("opaihub.credentials.CredentialStore", return_value=store),
                mock.patch.object(pb, "_probe_moonshot", fetch),
            ):
                pb.balance_overview(
                    root,
                    [
                        {"provider": "kimi", "kind": "free", "configured": True},
                        {"provider": "claude", "kind": "account", "configured": True},
                        {"provider": "gemini", "kind": "free", "configured": True},
                    ],
                    probe=True,
                    force=True,
                )
            self.assertEqual(fetch.call_count, 1)


class RouterExclusionTests(unittest.TestCase):
    CATALOG = {
        "models": [
            {
                "id": "free:kimi:kimi-k2.6",
                "kind": "free",
                "provider": "kimi",
                "available": True,
            },
            {
                "id": "free:gemini:g",
                "kind": "free",
                "provider": "gemini",
                "available": True,
            },
            {
                "id": "account:claude",
                "kind": "account",
                "provider": "claude",
                "available": True,
            },
        ],
        "connections": [],
    }

    def test_exhausted_provider_is_excluded_from_the_chain(self):
        from opaihub.auto_router import resolve_auto_chain

        with _Root() as root:
            pb.record_exhausted(root, "kimi")
            ids = [c["id"] for c in resolve_auto_chain(root, "fix a bug", self.CATALOG)]
        self.assertNotIn("free:kimi:kimi-k2.6", ids)
        self.assertIn("free:gemini:g", ids)
        self.assertIn("account:claude", ids)

    def test_exhausted_account_is_excluded_too(self):
        from opaihub.auto_router import resolve_auto_chain

        with _Root() as root:
            pb.record_exhausted(root, "claude")
            ids = [c["id"] for c in resolve_auto_chain(root, "fix a bug", self.CATALOG)]
        self.assertNotIn("account:claude", ids)

    def test_catalog_out_of_credit_flag_excludes_without_store(self):
        from opaihub.auto_router import resolve_auto_chain

        catalog = {
            "models": [
                {
                    "id": "free:kimi:kimi-k2.6",
                    "kind": "free",
                    "provider": "kimi",
                    "available": True,
                    "out_of_credit": True,
                }
            ],
            "connections": [],
        }
        with _Root() as root:
            ids = [c["id"] for c in resolve_auto_chain(root, "task", catalog)]
        self.assertEqual(ids, ["auto"])

    def test_expired_exhaustion_readmits_the_provider(self):
        from opaihub.auto_router import resolve_auto_chain

        with _Root() as root:
            pb.record_exhausted(
                root, "kimi", now=time.time() - pb.EXHAUSTED_TTL_SECONDS - 1
            )
            ids = [c["id"] for c in resolve_auto_chain(root, "task", self.CATALOG)]
        self.assertIn("free:kimi:kimi-k2.6", ids)

    def test_diagnostics_name_the_skipped_providers(self):
        from opaihub.auto_router import routing_diagnostics

        with _Root() as root:
            pb.record_exhausted(root, "kimi")
            diag = routing_diagnostics(root, "task", self.CATALOG)
        self.assertEqual(diag["skipped_out_of_credit"], ["kimi"])


class RunOutcomeRecordingTests(unittest.TestCase):
    def test_quota_error_code_records_exhaustion(self):
        from opai.app_state import _note_provider_balance

        with _Root() as root:
            _note_provider_balance(
                root, "kimi", {"error": {"code": "PROVIDER_QUOTA_EXHAUSTED"}}
            )
            self.assertTrue(pb.is_exhausted(root, "kimi"))

    def test_raw_insufficient_balance_last_error_records_exhaustion(self):
        """The tool loop reports provider_error without normalizing — the raw
        Moonshot suspension text in last_error must still count."""
        from opai.app_state import _note_provider_balance

        with _Root() as root:
            _note_provider_balance(
                root,
                "kimi",
                {
                    "last_error": (
                        "HTTP 429: Your account org-x is suspended due to "
                        "insufficient balance, please recharge your account"
                    )
                },
            )
            self.assertTrue(pb.is_exhausted(root, "kimi"))

    def test_completed_run_clears_exhaustion(self):
        from opai.app_state import _note_provider_balance

        with _Root() as root:
            pb.record_exhausted(root, "gemini")
            _note_provider_balance(root, "gemini", {"completion_state": "completed"})
            self.assertFalse(pb.is_exhausted(root, "gemini"))

    def test_plain_rate_limit_does_not_record_exhaustion(self):
        from opai.app_state import _note_provider_balance

        with _Root() as root:
            _note_provider_balance(
                root, "gemini", {"error": {"code": "PROVIDER_RATE_LIMITED"}}
            )
            self.assertFalse(pb.is_exhausted(root, "gemini"))


class InsufficientBalanceClassificationTests(unittest.TestCase):
    def test_moonshot_suspension_classifies_as_quota_exhausted(self):
        from opai.provider_contract import classify_error_code

        message = (
            "HTTP 429: Your account org-ab7cea466aa74bafab285ee19a8c2e37 "
            "<ak-xxxx> is suspended due to insufficient balance, please "
            "recharge your account or check your plan and billing details"
        )
        self.assertEqual(classify_error_code(message), "PROVIDER_QUOTA_EXHAUSTED")

    def test_anthropic_low_credit_classifies_as_quota_exhausted(self):
        from opai.provider_contract import classify_error_code

        self.assertEqual(
            classify_error_code(
                "Your credit balance is too low to access the Anthropic API"
            ),
            "PROVIDER_QUOTA_EXHAUSTED",
        )

    def test_plain_429_still_classifies_as_rate_limited(self):
        from opai.provider_contract import classify_error_code

        self.assertEqual(
            classify_error_code("429 too many requests"), "PROVIDER_RATE_LIMITED"
        )


class CatalogRemovalTests(unittest.TestCase):
    def test_out_of_credit_model_is_removed_with_explanation(self):
        from opai.app_state import available_models

        with _Root() as root:
            pb.record_exhausted(root, "kimi")
            with mock.patch.dict("os.environ", {"MOONSHOT_API_KEY": "k"}):
                catalog = available_models(root, discover_local=False)
        kimi = [m for m in catalog["models"] if str(m.get("provider") or "") == "kimi"]
        self.assertTrue(kimi)
        for model in kimi:
            self.assertFalse(model["available"])
            self.assertTrue(model["out_of_credit"])
            self.assertIn("out of credit", model["disabled_reason"])
            self.assertEqual(model["balance"]["status"], "out")

    def test_healthy_provider_keeps_balance_attached(self):
        from opai.app_state import available_models

        with _Root() as root:
            pb.set_manual_balance(root, "gemini", 4.2)
            with mock.patch.dict("os.environ", {"GOOGLE_API_KEY": "g"}):
                catalog = available_models(root, discover_local=False)
        gemini = [
            m for m in catalog["models"] if str(m.get("provider") or "") == "gemini"
        ]
        self.assertTrue(gemini)
        self.assertEqual(gemini[0]["balance"]["amount"], 4.2)
        self.assertFalse(gemini[0]["out_of_credit"])
        self.assertTrue(gemini[0]["available"])

    def test_auto_and_local_entries_carry_no_balance(self):
        from opai.app_state import available_models

        with _Root() as root:
            catalog = available_models(root, discover_local=False)
        auto = next(m for m in catalog["models"] if m["kind"] == "auto")
        self.assertIsNone(auto["balance"])
        self.assertFalse(auto["out_of_credit"])


class SettingsPayloadBalanceTests(unittest.TestCase):
    def test_provider_balances_payload_covers_accounts_and_free(self):
        from opai.gui_web import provider_balances_payload

        with _Root() as root:
            pb.set_manual_balance(root, "claude", 85, currency="EUR")
            models = {
                "connections": [
                    {"providerId": "claude", "authStatus": "connected"},
                    {"providerId": "codex", "authStatus": "not_configured"},
                ]
            }
            store = mock.MagicMock()
            store.get.side_effect = lambda p: "key" if p == "kimi" else ""
            with mock.patch("opaihub.credentials.CredentialStore", return_value=store):
                payload = provider_balances_payload(root, models)
        by_provider = {item["provider"]: item for item in payload}
        # Accounts and every free provider are present, deduped.
        for provider in ("claude", "codex", "kimi", "gemini", "groq", "mistral"):
            self.assertIn(provider, by_provider)
        self.assertEqual(by_provider["claude"]["amount"], 85)
        self.assertEqual(by_provider["claude"]["currency"], "EUR")
        self.assertTrue(by_provider["claude"]["configured"])
        self.assertFalse(by_provider["codex"]["configured"])
        self.assertTrue(by_provider["kimi"]["configured"])
        self.assertEqual(by_provider["gemini"]["status"], "not_configured")

    def test_payload_build_never_probes_by_default(self):
        from opai.gui_web import provider_balances_payload

        with _Root() as root:
            fetch = mock.Mock(return_value=(5.0, "USD"))
            store = mock.MagicMock()
            store.get.return_value = "key"
            with (
                mock.patch("opaihub.credentials.CredentialStore", return_value=store),
                mock.patch.object(pb, "_probe_moonshot", fetch),
            ):
                provider_balances_payload(root, {"connections": []})
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
