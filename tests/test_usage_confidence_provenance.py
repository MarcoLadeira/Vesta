"""OPai-tracked usage must report how its numbers were produced (#381).

The Settings → Model Usage row renders `confidence` verbatim next to the token
total. Until 2026-08-04 that field was hardcoded to ``"measured"`` whenever any
matching ledger event existed — including windows built entirely from tokens
OPai had *estimated* itself, because the provider returned no usage data.

That is the precise failure #381 exists to prevent ("make the cost/savings
ledger truthful and consistent across every surface") and it violates the
release gate "cost records distinguish estimates from confirmed or unknown
charges". The provenance was already recorded on every event via the
``measurement`` field; the snapshot simply ignored it.

A window is only as trustworthy as its weakest contributing number, so a window
mixing provider-reported and estimated calls reports ``mixed`` rather than
rounding up to ``measured``.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from opaihub.ledger import EVENT_MODEL_CALL, record_model_call
from opaihub.usage import build_usage_snapshots

MODELS = [{"id": "free:groq:m", "provider": "groq"}]


def _record(root: Path, measurement: str, *, tokens: int = 100) -> None:
    record_model_call(
        root,
        "task",
        model_tier="L2",
        provider_type="free_api",
        tokens=tokens,
        confirmed=True,
        model_id="free:groq:m",
        provider_id="groq",
        measurement=measurement,
    )


def _confidence(root: Path) -> str:
    return str(build_usage_snapshots(root, MODELS)[0]["confidence"])


class TrackedUsageConfidenceTests(unittest.TestCase):
    def test_estimated_tokens_are_never_reported_as_measured(self) -> None:
        # The original defect: OPai guessed the token count, the dashboard
        # called it "measured".
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _record(root, "estimated")
            self.assertEqual(_confidence(root), "estimated")

    def test_provider_reported_tokens_are_measured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _record(root, "provider")
            self.assertEqual(_confidence(root), "measured")

    def test_actual_cost_provenance_also_counts_as_measured(self) -> None:
        # Account runs record measurement="actual" when the provider returns a
        # real spend figure; that is a measured number, not an estimate.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _record(root, "actual")
            self.assertEqual(_confidence(root), "measured")

    def test_a_mixed_window_is_reported_as_mixed(self) -> None:
        # A total is only as trustworthy as its weakest contributing number, so
        # one estimated call must stop the whole window claiming "measured".
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _record(root, "provider")
            _record(root, "estimated")
            self.assertEqual(_confidence(root), "mixed")

    def test_no_recorded_calls_is_no_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_confidence(Path(tmp)), "no-data")

    def test_events_without_a_measurement_field_are_treated_as_estimated(self) -> None:
        # Older ledger rows predate the field. Absent provenance is not
        # evidence of measurement — fail closed to the weaker claim.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _record(root, "")
            self.assertEqual(_confidence(root), "estimated")

    def test_the_token_total_itself_is_unchanged_by_provenance(self) -> None:
        # This change makes the label honest; it must not alter the arithmetic.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _record(root, "estimated", tokens=300)
            _record(root, "provider", tokens=200)
            snapshot = build_usage_snapshots(root, MODELS)[0]
            self.assertEqual(snapshot["used"], 500)
            self.assertEqual(snapshot["confidence"], "mixed")

    def test_invalid_token_values_do_not_inflate_or_crash_the_snapshot(self) -> None:
        """A hand-edited/corrupt JSONL row must not break Settings → Model Usage."""
        event = {
            "event_type": EVENT_MODEL_CALL,
            "model_id": "free:groq:m",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tokens": float("nan"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = build_usage_snapshots(Path(tmp), MODELS, events=[event])[0]

        self.assertEqual(snapshot["used"], 0)


if __name__ == "__main__":
    unittest.main()
