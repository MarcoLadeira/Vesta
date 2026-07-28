from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opaihub import auto_router, provider_reliability as reliability


def _free(model_id: str, provider: str) -> dict:
    return {"id": model_id, "provider": provider, "kind": "free", "available": True}


def _account(model_id: str, provider: str, available: bool = True) -> dict:
    return {
        "id": model_id,
        "provider": provider,
        "kind": "account",
        "available": available,
    }


class ProviderOfTests(unittest.TestCase):
    def test_extracts_provider_slug_from_every_id_shape(self) -> None:
        self.assertEqual(auto_router.provider_of("account:claude:haiku"), "claude")
        self.assertEqual(auto_router.provider_of("free:kimi:kimi-k2.6"), "kimi")
        self.assertEqual(auto_router.provider_of("ollama:qwen"), "ollama")
        self.assertEqual(auto_router.provider_of("auto"), "")
        self.assertEqual(auto_router.provider_of(""), "")

    def test_reason_slug_maps_error_codes(self) -> None:
        self.assertEqual(
            auto_router.reason_slug("failed", {"code": "AUTH_INVALID"}), "auth"
        )
        self.assertEqual(
            auto_router.reason_slug("failed", {"code": "PROVIDER_RATE_LIMITED"}),
            "rate-limit",
        )
        self.assertEqual(auto_router.reason_slug("empty"), "no-answer")
        self.assertEqual(
            auto_router.reason_slug("capability_mismatch"), "capability"
        )


class ChainOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_chain_is_local_first_then_free_then_paid(self) -> None:
        catalog = {
            "models": [
                _account("account:claude:haiku", "claude"),
                _free("free:kimi:kimi-k2.6", "kimi"),
            ]
        }
        chain = auto_router.resolve_auto_chain(self.root, "explain", catalog)
        kinds = [c["kind"] for c in chain]
        self.assertEqual(kinds[0], "local")  # the live-detection sentinel leads
        self.assertEqual(chain[0]["id"], "auto")
        # free before paid; every paid entry marked paid=True at the tail.
        self.assertLess(kinds.index("free"), kinds.index("account"))
        self.assertTrue(chain[-1]["paid"])
        self.assertFalse(chain[1]["paid"])

    def test_unavailable_free_and_failed_accounts_excluded(self) -> None:
        catalog = {
            "models": [
                {"id": "free:gemini:x", "provider": "gemini", "kind": "free"},  # no avail
                _free("free:kimi:kimi-k2.6", "kimi"),
                _account("account:claude:haiku", "claude"),
            ],
            "connections": [{"providerId": "claude", "authStatus": "expired"}],
        }
        chain = auto_router.resolve_auto_chain(self.root, "explain", catalog)
        ids = [c["id"] for c in chain]
        self.assertIn("free:kimi:kimi-k2.6", ids)
        self.assertNotIn("free:gemini:x", ids)  # not verified available
        self.assertNotIn("account:claude:haiku", ids)  # connection expired

    def test_recent_failure_deprioritizes_provider_within_bucket(self) -> None:
        catalog = {
            "models": [
                _free("free:kimi:kimi-k2.6", "kimi"),
                _free("free:groq:x", "groq"),
            ]
        }
        # groq just failed → it should sink behind kimi.
        reliability.record_provider_outcome(self.root, "groq", False, reason="timeout")
        chain = auto_router.resolve_auto_chain(self.root, "explain", catalog)
        free_ids = [c["id"] for c in chain if c["kind"] == "free"]
        self.assertEqual(free_ids[0], "free:kimi:kimi-k2.6")
        self.assertEqual(free_ids[1], "free:groq:x")

    def _two_free(self) -> dict:
        return {
            "models": [
                _free("free:kimi:kimi-k2.6", "kimi"),
                _free("free:groq:x", "groq"),
            ]
        }

    def _first_free(self, catalog: dict, *, now: float | None = None) -> str:
        chain = auto_router.resolve_auto_chain(
            self.root, "explain", catalog, now=now
        )
        return next(c["id"] for c in chain if c["kind"] == "free")

    def test_a_provider_that_just_worked_leads_the_follow_up_turn(self) -> None:
        # Rotation and conversation consistency pull in opposite directions.
        # Pure LRU sorted the provider that *just answered* last, so a follow-up
        # actively routed away from whatever worked — two turns of one
        # conversation on two providers, with different style and different
        # context, for no reason the user could see. Recent success wins inside
        # the stickiness window.
        reliability.record_provider_outcome(self.root, "kimi", True, now=1000.0)
        self.assertEqual(
            self._first_free(self._two_free(), now=1001.0), "free:kimi:kimi-k2.6"
        )

    def test_rotation_resumes_once_the_conversation_goes_cold(self) -> None:
        # The original guarantee is scoped, not removed: outside the window,
        # least-recently-used still leads so load spreads across free tiers.
        reliability.record_provider_outcome(self.root, "kimi", True, now=1000.0)
        cold = 1000.0 + reliability.STICKY_SECONDS + 1
        self.assertEqual(self._first_free(self._two_free(), now=cold), "free:groq:x")

    def test_stickiness_never_outranks_a_failure(self) -> None:
        # A provider that succeeded and then failed must not stay preferred —
        # stickiness is a tiebreak among healthy providers, never a lock.
        reliability.record_provider_outcome(self.root, "kimi", True, now=1000.0)
        reliability.record_provider_outcome(
            self.root, "kimi", False, reason="timeout", now=1001.0
        )
        self.assertEqual(
            self._first_free(self._two_free(), now=1002.0), "free:groq:x"
        )


class RetryClassificationTests(unittest.TestCase):
    def test_retryable_and_terminal_partition(self) -> None:
        for status in ["runner_error", "needs_model", "capability_mismatch", "empty"]:
            self.assertTrue(auto_router.is_retryable_status(status), status)
        for status in ["answered", "cancelled", "needs_command_approval", "blocked"]:
            self.assertFalse(auto_router.is_retryable_status(status), status)


class ReliabilityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_penalty_grows_with_recent_failures_and_decays(self) -> None:
        base = 1_000_000.0
        self.assertEqual(reliability.reliability_penalty(self.root, "kimi"), 0.0)
        reliability.record_provider_outcome(self.root, "kimi", False, now=base)
        self.assertGreater(reliability.reliability_penalty(self.root, "kimi", now=base), 0.0)
        # Old failures fall out of the rolling window.
        future = base + reliability.WINDOW_SECONDS + 10
        self.assertEqual(
            reliability.reliability_penalty(self.root, "kimi", now=future), 0.0
        )

    def test_cooldown_flag_clears_after_a_success(self) -> None:
        base = 2_000_000.0
        reliability.record_provider_outcome(self.root, "groq", False, now=base)
        self.assertTrue(reliability.in_cooldown(self.root, "groq", now=base + 1))
        reliability.record_provider_outcome(self.root, "groq", True, now=base + 2)
        self.assertFalse(reliability.in_cooldown(self.root, "groq", now=base + 3))

    def test_sentinel_and_empty_providers_are_never_recorded(self) -> None:
        reliability.record_provider_outcome(self.root, "auto", False)
        reliability.record_provider_outcome(self.root, "", False)
        self.assertEqual(reliability.reliability_snapshot(self.root), {})

    def test_records_no_secret(self) -> None:
        reliability.record_provider_outcome(
            self.root, "kimi", False, reason="token=super-secret-value"
        )
        snap = reliability.reliability_snapshot(self.root)
        self.assertNotIn("super-secret-value", repr(snap))
        text = (reliability._path(self.root)).read_text(encoding="utf-8")
        self.assertNotIn("super-secret-value", text)


if __name__ == "__main__":
    unittest.main()
