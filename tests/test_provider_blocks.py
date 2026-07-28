"""Deterministic provider blocks: the "this cannot work yet" memory.

Covers the consistency defects these blocks exist for — a stale CLI that
refuses every request, and a provider that cannot be given bounded repository
write access — plus the self-healing rules that keep a block from becoming a
permanent ban.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opaihub import auto_router, provider_blocks as blocks


def _free(model_id: str, provider: str) -> dict:
    return {"id": model_id, "provider": provider, "kind": "free", "available": True}


def _account(model_id: str, provider: str) -> dict:
    return {"id": model_id, "provider": provider, "kind": "account", "available": True}


class ReasonMappingTests(unittest.TestCase):
    def test_maps_the_two_observed_deterministic_refusals(self) -> None:
        self.assertEqual(
            blocks.reason_for("failed", {"code": "PROVIDER_CLI_OUTDATED"}),
            "cli_outdated",
        )
        self.assertEqual(
            blocks.reason_for("capability_mismatch"), "no_scoped_edits"
        )
        self.assertEqual(
            blocks.reason_for("failed", {"code": "CONFIG_INVALID"}), "config_invalid"
        )

    def test_transient_and_billing_failures_are_not_blocks(self) -> None:
        # These belong to the reliability memory / balance store; blocking on
        # them would hide a provider that works again a second later.
        for code in (
            "PROVIDER_UNAVAILABLE",
            "PROVIDER_RATE_LIMITED",
            "PROVIDER_QUOTA_EXHAUSTED",
            "AUTH_INVALID",
        ):
            self.assertEqual(blocks.reason_for("failed", {"code": code}), "")


class BlockStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_records_and_reads_a_block(self) -> None:
        blocks.record_block(self.root, "codex", "cli_outdated", now=1000.0)
        block = blocks.active_block(self.root, "codex", now=1001.0)
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(block["reason"], "cli_outdated")
        self.assertEqual(block["scope"], "all")
        self.assertTrue(blocks.is_blocked(self.root, "codex", now=1001.0))

    def test_cli_outdated_names_the_exact_update_command(self) -> None:
        blocks.record_block(self.root, "codex", "cli_outdated", now=1000.0)
        message = blocks.block_message(self.root, "codex", now=1001.0)
        self.assertIn("npm install -g @openai/codex", message)

    def test_edit_scoped_block_only_applies_to_editing_turns(self) -> None:
        blocks.record_block(self.root, "copilot", "no_scoped_edits", now=1000.0)
        self.assertTrue(
            blocks.is_blocked(self.root, "copilot", needs_edit=True, now=1001.0)
        )
        # Ask and Plan still work: the provider can read and explain, it just
        # cannot be handed write access.
        self.assertFalse(
            blocks.is_blocked(self.root, "copilot", needs_edit=False, now=1001.0)
        )
        self.assertEqual(blocks.block_message(self.root, "copilot", now=1001.0), "")

    def test_block_expires_so_an_out_of_band_fix_is_rediscovered(self) -> None:
        blocks.record_block(self.root, "codex", "cli_outdated", now=1000.0)
        ttl = blocks.BLOCK_REASONS["cli_outdated"]["ttl_seconds"]
        self.assertTrue(blocks.is_blocked(self.root, "codex", now=1000.0 + ttl - 1))
        self.assertFalse(blocks.is_blocked(self.root, "codex", now=1000.0 + ttl + 1))

    def test_success_clears_the_block_immediately(self) -> None:
        blocks.record_block(self.root, "codex", "cli_outdated", now=1000.0)
        blocks.clear_block(self.root, "codex")
        self.assertFalse(blocks.is_blocked(self.root, "codex", now=1001.0))

    def test_unknown_reason_is_never_persisted(self) -> None:
        # The store must not become a place raw provider text can land.
        blocks.record_block(self.root, "codex", "sk-secret-looking-text", now=1000.0)
        self.assertIsNone(blocks.active_block(self.root, "codex", now=1001.0))

    def test_store_contains_only_closed_vocabulary_values(self) -> None:
        blocks.record_block(self.root, "codex", "cli_outdated", now=1000.0)
        path = self.root / ".opaihub" / "health" / "provider_blocks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(data["codex"]), {"reason", "at"})
        self.assertIn(data["codex"]["reason"], blocks.BLOCK_REASONS)

    def test_local_and_auto_are_never_blocked(self) -> None:
        blocks.record_block(self.root, "local", "cli_outdated", now=1000.0)
        blocks.record_block(self.root, "auto", "cli_outdated", now=1000.0)
        self.assertFalse(blocks.is_blocked(self.root, "local", now=1001.0))
        self.assertFalse(blocks.is_blocked(self.root, "auto", now=1001.0))


class ChainExclusionTests(unittest.TestCase):
    """Auto must not spend a fallback step on a guaranteed refusal."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.catalog = {
            "models": [
                _free("free:gemini:3.1-flash-lite", "gemini"),
                _account("account:codex", "codex"),
                _account("account:copilot", "copilot"),
            ]
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _ids(self, **kwargs) -> list[str]:
        chain = auto_router.resolve_auto_chain(
            self.root, "fix the bug", self.catalog, **kwargs
        )
        return [entry["id"] for entry in chain]

    def test_outdated_cli_provider_leaves_the_chain_entirely(self) -> None:
        self.assertIn("account:codex", self._ids())
        blocks.record_block(self.root, "codex", "cli_outdated")
        self.assertNotIn("account:codex", self._ids())
        # Everything else still routes — the block removes one provider, not
        # the user's ability to work.
        self.assertIn("free:gemini:3.1-flash-lite", self._ids())

    def test_write_incapable_provider_is_excluded_only_for_editing_turns(self) -> None:
        blocks.record_block(self.root, "copilot", "no_scoped_edits")
        self.assertNotIn("account:copilot", self._ids(needs_edit=True))
        self.assertIn("account:copilot", self._ids(needs_edit=False))

    def test_diagnostics_explain_why_a_provider_is_missing(self) -> None:
        blocks.record_block(self.root, "codex", "cli_outdated")
        diagnostics = auto_router.routing_diagnostics(
            self.root, "fix the bug", self.catalog
        )
        self.assertEqual(diagnostics["skipped_blocked"], {"codex": "cli_outdated"})


class TransientRetryTests(unittest.TestCase):
    """A transport blip earns one re-attempt, not a provider change."""

    def test_transient_codes_retry_once_then_fall_through(self) -> None:
        blip = {"code": "PROVIDER_UNAVAILABLE"}
        self.assertTrue(auto_router.should_retry_same_provider(blip, 0))
        self.assertFalse(auto_router.should_retry_same_provider(blip, 1))

    def test_deterministic_failures_never_retry_the_same_provider(self) -> None:
        for code in (
            "PROVIDER_CLI_OUTDATED",
            "PROVIDER_QUOTA_EXHAUSTED",
            "AUTH_INVALID",
            "CONFIG_INVALID",
        ):
            self.assertFalse(
                auto_router.should_retry_same_provider({"code": code}, 0), code
            )

    def test_missing_or_unstructured_errors_do_not_retry(self) -> None:
        self.assertFalse(auto_router.should_retry_same_provider(None, 0))
        self.assertFalse(auto_router.should_retry_same_provider("boom", 0))


class BestAlternativeTests(unittest.TestCase):
    """A failure names the next usable model, so it is never a dead end."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_offers_the_cheapest_usable_model_from_another_provider(self) -> None:
        catalog = {
            "models": [
                _account("account:codex", "codex"),
                _free("free:gemini:3.1-flash-lite", "gemini"),
            ]
        }
        offer = auto_router.best_alternative(
            self.root, catalog, exclude_providers={"codex"}
        )
        self.assertIsNotNone(offer)
        assert offer is not None
        self.assertEqual(offer["id"], "free:gemini:3.1-flash-lite")
        self.assertFalse(offer["paid"])

    def test_never_offers_a_provider_that_is_itself_blocked(self) -> None:
        catalog = {
            "models": [
                _account("account:codex", "codex"),
                _account("account:copilot", "copilot"),
            ]
        }
        blocks.record_block(self.root, "copilot", "no_scoped_edits")
        offer = auto_router.best_alternative(
            self.root, catalog, exclude_providers={"codex"}, needs_edit=True
        )
        self.assertIsNone(offer)
        # Read-only work can still use it, so the same call offers it back.
        offer = auto_router.best_alternative(
            self.root, catalog, exclude_providers={"codex"}, needs_edit=False
        )
        assert offer is not None
        self.assertEqual(offer["id"], "account:copilot")

    def test_returns_none_when_nothing_is_runnable(self) -> None:
        catalog = {"models": [_account("account:codex", "codex")]}
        offer = auto_router.best_alternative(
            self.root, catalog, exclude_providers={"codex"}
        )
        self.assertIsNone(offer)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
