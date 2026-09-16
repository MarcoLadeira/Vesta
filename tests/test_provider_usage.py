"""Tests for per-provider account-usage windows (Settings → Model Usage).

Covers the window models, rate-limit header parsing (durations, seconds,
timestamps), observed-quota extraction from the ledger, Vesta-tracked window
counts, staleness, the balance-backed credit path, the live header probe, and
the overview assembly — the full path from "a real call returned rate-limit
headers" to "the Model Usage page shows honest, provider-reported usage".
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from vestahub import provider_usage as pu


class _Root:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _model_call(provider: str, *, created_at: str, quota=None, calls=1, tokens=0):
    event = {
        "event_type": pu.EVENT_MODEL_CALL,
        "provider_id": provider,
        "created_at": created_at,
        "model_calls": calls,
        "tokens": tokens,
    }
    if quota is not None:
        event["quota_snapshot"] = quota
    return event


class ResetParsingTests(unittest.TestCase):
    def test_duration_string(self):
        self.assertAlmostEqual(
            pu.parse_reset("2m59.5s", observed_at=1000) - 1000, 179.5, places=1
        )
        self.assertAlmostEqual(
            pu.parse_reset("1h30m", observed_at=1000) - 1000, 5400, places=1
        )
        self.assertAlmostEqual(
            pu.parse_reset("45s", observed_at=1000) - 1000, 45, places=1
        )

    def test_bare_seconds_vs_epoch(self):
        self.assertEqual(pu.parse_reset("60", observed_at=1000), 1060)
        self.assertEqual(pu.parse_reset("1784800000", observed_at=1000), 1784800000)

    def test_iso_timestamp(self):
        self.assertEqual(
            pu.parse_reset("2026-07-24T00:00:00Z", observed_at=1000),
            datetime(2026, 7, 24, tzinfo=timezone.utc).timestamp(),
        )

    def test_unparseable_is_none(self):
        self.assertIsNone(pu.parse_reset("n/a", observed_at=1000))
        self.assertIsNone(pu.parse_reset("", observed_at=1000))
        self.assertIsNone(pu.parse_reset(None, observed_at=1000))


class HeaderParsingTests(unittest.TestCase):
    def test_request_window_preferred(self):
        quota = pu.parse_quota_headers(
            {
                "x-ratelimit-limit-requests": "1500",
                "x-ratelimit-remaining-requests": "1230",
                "x-ratelimit-reset-requests": "2h",
                "x-ratelimit-limit-tokens": "1000000",
            },
            observed_at=1000,
        )
        self.assertEqual(quota["metric"], "requests")
        self.assertEqual(quota["limit"], 1500)
        self.assertEqual(quota["remaining"], 1230)
        self.assertEqual(quota["used"], 270)
        self.assertAlmostEqual(quota["resetsAt"] - 1000, 7200, places=1)

    def test_falls_back_to_token_window(self):
        quota = pu.parse_quota_headers(
            {
                "x-ratelimit-limit-tokens": "6000",
                "x-ratelimit-remaining-tokens": "5400",
            },
            observed_at=1000,
        )
        self.assertEqual(quota["metric"], "tokens")
        self.assertEqual(quota["used"], 600)

    def test_no_usable_headers_is_none(self):
        self.assertIsNone(pu.parse_quota_headers({"content-type": "application/json"}))
        self.assertIsNone(pu.parse_quota_headers(None))
        self.assertIsNone(pu.parse_quota_headers({"x-ratelimit-limit-requests": "0"}))


class WindowModelTests(unittest.TestCase):
    def test_every_provider_has_a_verifiable_window_and_check_url(self):
        for provider, model in pu.USAGE_MODELS.items():
            self.assertTrue(model.get("windowLabel"), provider)
            self.assertTrue(model.get("checkUrl"), provider)
            self.assertIn(model.get("kind"), {"account", "free"}, provider)

    def test_account_providers_have_no_live_source(self):
        for provider in ("claude", "codex", "copilot"):
            self.assertIsNone(pu.USAGE_MODELS[provider]["liveSource"])
            self.assertFalse(pu.supports_live_usage(provider))

    def test_free_providers_have_a_safe_live_source(self):
        for provider in ("gemini", "groq", "mistral", "kimi"):
            self.assertTrue(pu.supports_live_usage(provider))


class SnapshotTests(unittest.TestCase):
    def test_unsupported_provider_is_honest(self):
        with _Root() as root:
            snap = pu.usage_snapshot(root, "weirdo", events=[], now=1000)
        self.assertEqual(snap["status"], "unsupported")
        self.assertFalse(snap["official"]["available"])

    def test_account_provider_is_unavailable_with_window_and_link(self):
        with _Root() as root:
            snap = pu.usage_snapshot(root, "claude", events=[], now=1000)
        self.assertEqual(snap["status"], "unavailable")
        self.assertFalse(snap["official"]["available"])
        self.assertEqual(snap["window"]["label"], "5-hour session window")
        self.assertEqual(snap["checkUrl"], "https://claude.ai/settings/usage")

    def test_not_configured_provider(self):
        with _Root() as root:
            snap = pu.usage_snapshot(
                root, "gemini", configured=False, events=[], now=1000
            )
        self.assertEqual(snap["status"], "not_configured")

    def test_observed_quota_from_ledger_yields_live_usage(self):
        now = 1_784_800_000.0
        events = [
            _model_call(
                "gemini",
                created_at=_iso(now - 120),
                quota={
                    "metric": "requests",
                    "limit": 1500,
                    "remaining": 1230,
                    "window": "day",
                    "resetsAt": "1h30m",
                },
                calls=3,
                tokens=1200,
            )
        ]
        with _Root() as root:
            snap = pu.usage_snapshot(root, "gemini", events=events, now=now)
        official = snap["official"]
        self.assertEqual(snap["status"], "live")
        self.assertTrue(official["available"])
        self.assertEqual(official["source"], "provider")
        self.assertEqual(official["used"], 270)
        self.assertEqual(official["percent"], 18.0)
        # Reset was observed 120s ago as "1h30m out", so ~5280s remain now: the
        # countdown correctly accounts for time elapsed since the observation.
        self.assertAlmostEqual(official["resetsInSeconds"], 5280, delta=2)
        self.assertFalse(official["stale"])

    def test_observed_quota_is_stale_after_window_rolls_over(self):
        now = 1_784_800_000.0
        events = [
            _model_call(
                "groq",
                created_at=_iso(now - 2 * 24 * 3600),  # 2 days ago
                quota={
                    "metric": "requests",
                    "limit": 1000,
                    "remaining": 500,
                    "window": "day",
                    "resetsAt": "60s",
                },
            )
        ]
        with _Root() as root:
            snap = pu.usage_snapshot(root, "groq", events=events, now=now)
        self.assertEqual(snap["status"], "stale")
        self.assertTrue(snap["official"]["stale"])

    def test_vesta_tracked_counts_the_provider_window_only(self):
        now = 1_784_800_000.0
        events = [
            _model_call(
                "gemini", created_at=_iso(now - 60), calls=2, tokens=100
            ),  # today
            _model_call(
                "gemini", created_at=_iso(now - 60), calls=1, tokens=50
            ),  # today
            _model_call(
                "gemini", created_at=_iso(now - 5 * 24 * 3600), calls=9, tokens=999
            ),  # old, excluded
            _model_call(
                "claude", created_at=_iso(now - 60), calls=7, tokens=7
            ),  # other provider
        ]
        with _Root() as root:
            snap = pu.usage_snapshot(root, "gemini", events=events, now=now)
        tracked = snap["vestaTracked"]
        self.assertEqual(tracked["calls"], 3)  # 2 + 1, old one excluded
        self.assertEqual(tracked["tasks"], 2)
        self.assertEqual(tracked["tokens"], 150)
        self.assertEqual(tracked["windowLabel"], "Today")

    def test_vesta_tracked_ignores_invalid_token_values(self):
        """Malformed ledger values must not inflate or crash the usage page."""
        now = 1_784_800_000.0
        events = [
            _model_call("gemini", created_at=_iso(now - 60), tokens=True),
            _model_call("gemini", created_at=_iso(now - 60), tokens=float("nan")),
            _model_call("gemini", created_at=_iso(now - 60), tokens=-500),
        ]
        with _Root() as root:
            snap = pu.usage_snapshot(root, "gemini", events=events, now=now)

        self.assertEqual(snap["vestaTracked"]["tokens"], 0)

    def test_account_provider_tracked_count_is_all_time_not_window_bound(self):
        # Regression: Claude's rolling 5-hour window almost never has any
        # Vesta-routed activity in it (most usage goes through the bare CLI,
        # which never touches Vesta's ledger) — so bounding the *tracked*
        # count to that same narrow window made it read as "no activity"
        # for real users with real historical activity. Account providers
        # now count all-time instead, since Vesta can't verify the real
        # window boundaries for them anyway.
        now = 1_784_800_000.0
        eighteen_days_ago = now - 18 * 24 * 3600
        events = [
            _model_call(
                "claude", created_at=_iso(eighteen_days_ago), calls=1, tokens=53
            ),
            _model_call(
                "claude", created_at=_iso(eighteen_days_ago + 3600), calls=1, tokens=76
            ),
        ]
        with _Root() as root:
            snap = pu.usage_snapshot(root, "claude", events=events, now=now)
        tracked = snap["vestaTracked"]
        self.assertEqual(tracked["calls"], 2)
        self.assertEqual(tracked["tasks"], 2)
        self.assertEqual(tracked["windowLabel"], "All time via Vesta")
        self.assertAlmostEqual(tracked["lastUsedAt"], eighteen_days_ago + 3600, delta=1)

    def test_account_provider_note_clarifies_vesta_only_counts_its_own_routing(self):
        model = pu.usage_model("claude")
        self.assertIn("not the claude CLI used directly", model["note"])

    def test_kimi_credit_uses_the_balance_snapshot(self):
        now = 1_784_800_000.0
        with _Root() as root:
            from vestahub import provider_balance as pb

            pb.set_manual_balance(root, "kimi", 8.42, currency="USD")
            snap = pu.usage_snapshot(root, "kimi", events=[], now=now)
        official = snap["official"]
        self.assertEqual(snap["status"], "live")
        self.assertEqual(official["metric"], "credit")
        self.assertEqual(official["remaining"], 8.42)
        self.assertEqual(official["currency"], "USD")
        # No fabricated limit/percent for balance-based providers.
        self.assertIsNone(official["limit"])


class ProbeTests(unittest.TestCase):
    def test_header_probe_populates_and_caches(self):
        now = 1_784_800_000.0
        probed = {
            "metric": "requests",
            "limit": 1000,
            "remaining": 900,
            "used": 100,
            "window": "day",
            "resetsAt": now + 3600,
            "observedAt": now,
        }
        with _Root() as root:
            with (
                mock.patch.object(pu, "_probe_headers", return_value=probed) as probe,
                mock.patch(
                    "vestahub.credentials.CredentialStore.get", return_value="key"
                ),
            ):
                first = pu.probe_usage(root, "groq", force=True, now=now)
                self.assertEqual(first, probed)
                # Within the TTL, the cache is used — no second network probe.
                second = pu.probe_usage(root, "groq", now=now + 10)
                self.assertEqual(second, probed)
                self.assertEqual(probe.call_count, 1)

    def test_probe_failure_never_raises_and_returns_none(self):
        with _Root() as root:
            with (
                mock.patch.object(pu, "_probe_headers", return_value=None),
                mock.patch(
                    "vestahub.credentials.CredentialStore.get", return_value="key"
                ),
            ):
                self.assertIsNone(pu.probe_usage(root, "groq", force=True))

    def test_account_provider_has_nothing_to_probe(self):
        with _Root() as root:
            self.assertIsNone(pu.probe_usage(root, "claude", force=True))


class OverviewTests(unittest.TestCase):
    def test_one_unsupported_provider_never_breaks_the_rest(self):
        now = 1_784_800_000.0
        with _Root() as root:
            overview = pu.usage_overview(
                root,
                [
                    {"provider": "claude", "configured": True},
                    {"provider": "gemini", "configured": True},
                    {
                        "provider": "weirdo",
                        "configured": True,
                    },  # dropped, not in USAGE_MODELS
                    {"provider": "claude", "configured": True},  # de-duped
                ],
                now=now,
            )
        self.assertEqual([s["provider"] for s in overview], ["claude", "gemini"])

    def test_overview_reads_the_ledger_once_and_shares_it(self):
        now = 1_784_800_000.0
        with _Root() as root:
            with mock.patch.object(pu, "_model_call_events", return_value=[]) as reader:
                pu.usage_overview(
                    root,
                    [
                        {"provider": "claude", "configured": True},
                        {"provider": "gemini", "configured": True},
                    ],
                    now=now,
                )
                self.assertEqual(reader.call_count, 1)


if __name__ == "__main__":
    unittest.main()
