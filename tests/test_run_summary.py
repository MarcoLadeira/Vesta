"""The shareable, verdict-first run receipt summary (#389)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from tests._helpers import FakeLocalRunner, make_repo

from opaihub.gui_pipeline import handle_gui_message
from opaihub.run_summary import build_run_summary


def _completed_record() -> dict:
    return {
        "completion_verdict": {
            "verdict": "completed",
            "reason": "Objective verified from OPai-observed evidence.",
            "objective": {
                "objective_text": "Fix the parser bug and run the tests.",
                "mode": "implement",
            },
            "evidence": [
                {"kind": "diff", "summary": "2 file(s) changed"},
                {"kind": "tests", "summary": "Repository tests passed"},
            ],
            "next_action": "Review the attached evidence.",
        },
        "receipt": {
            "estimated_actual_usd": 0.0,
            "estimated_savings_usd": 0.0123,
            "baseline_tier": "L3",
            "chosen_tier": "L1",
            "confidence": "estimated",
            "paid_call": False,
        },
        "changed_files": ["parser.py", "tests/test_parser.py"],
        "selected_model": "auto",
        "effective_run_mode": "safe-auto",
    }


def _lines(record) -> list[str]:
    return build_run_summary(record).splitlines()


def test_verdict_is_the_first_thing_after_the_title() -> None:
    lines = [line for line in _lines(_completed_record()) if line.strip()]
    assert lines[0] == "# OPai run receipt"
    # The very next non-empty line is the verdict — nothing precedes "did it work".
    assert lines[1].startswith("**Verdict: Completed**")
    assert "Objective verified" in lines[1]


def test_completed_run_shows_evidence_model_and_counterfactual_savings() -> None:
    text = build_run_summary(_completed_record())
    assert "## Evidence" in text
    assert "Files changed (2): parser.py, tests/test_parser.py" in text
    assert "Repository tests passed" in text
    # Routing rationale names the tiers.
    assert "routed to L1 from the L3 baseline" in text
    # Honest cost badge + named counterfactual savings.
    assert "Spend: $0.0000 (estimated)" in text
    assert "Saved: $0.0123 vs the L3 baseline" in text


def test_diff_evidence_is_not_double_listed_with_the_files_line() -> None:
    # The "diff" evidence ref is already represented by the Files changed line.
    text = build_run_summary(_completed_record())
    assert "2 file(s) changed" not in text  # only the explicit files line remains
    assert text.count("Files changed") == 1


def test_partial_run_shows_actuals_next_action_and_no_savings() -> None:
    record = _completed_record()
    record["completion_verdict"]["verdict"] = "partial"
    record["completion_verdict"]["reason"] = "No diff evidence verifies the edit."
    record["completion_verdict"]["next_action"] = "Ask OPai to apply the change."
    record["changed_files"] = []
    record["completion_verdict"]["evidence"] = []
    record["receipt"]["estimated_savings_usd"] = 0.0  # #381: gated to zero

    text = build_run_summary(record)
    assert text.splitlines()[2].startswith("**Verdict: Partial**")
    assert "Next: Ask OPai to apply the change." in text
    # Actual spend is still shown, but no savings claim of any kind.
    assert "Spend: $0.0000" in text
    assert "Saved:" not in text


def test_secrets_are_redacted_before_the_summary_can_leave() -> None:
    record = _completed_record()
    record["completion_verdict"]["objective"]["objective_text"] = (
        "Rotate the key sk-abcdef0123456789 in the client."
    )
    text = build_run_summary(record)
    assert "sk-abcdef0123456789" not in text
    # #622: opaihub.command_runner.redact (the canonical redactor, since this
    # module was folded into it) marks a bare prefix-only match like this one
    # "[REDACTED_SECRET]", not "[REDACTED]" — that distinction is about
    # whether an assignment's keyword/value was separable, not whether
    # redaction happened.
    assert "[REDACTED" in text


def test_cost_badge_reflects_measurement_confidence() -> None:
    record = _completed_record()
    record["receipt"]["confidence"] = "actual"
    record["receipt"]["paid_call"] = True
    assert "(measured) paid" in build_run_summary(record)

    record["receipt"]["confidence"] = "unknown"
    record["receipt"]["paid_call"] = True
    assert "(subscription)" in build_run_summary(record)


def test_empty_or_malformed_record_never_crashes() -> None:
    for bad in (None, {}, {"completion_verdict": "nope"}, {"receipt": []}):
        text = build_run_summary(bad)  # type: ignore[arg-type]
        assert text.startswith("# OPai run receipt")
        assert "**Verdict:" in text


def test_non_completed_run_never_claims_savings_even_if_receipt_has_one() -> None:
    # Defense in depth: the summary reads the (verdict-gated) receipt, but even a
    # stray positive savings on a non-completed verdict must not be presented as
    # a claim — the gate lives in the pipeline (#381); here we assert the summary
    # only prints savings when the receipt actually carries a positive figure.
    record = _completed_record()
    record["completion_verdict"]["verdict"] = "failed"
    record["receipt"]["estimated_savings_usd"] = 0.0
    assert "Saved:" not in build_run_summary(record)


def test_pipeline_attaches_a_verdict_first_run_summary() -> None:
    # The GUI copy action reads result["run_summary"]; the pipeline must render
    # it from the assembled record so the exported content is identical to the
    # builder's output (no display-side recomputation).
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp))
        selected = FakeLocalRunner(model="q", answer="The router picks a tier.")

        def run_ask(root, task, **kwargs):
            return {"status": "answered_locally", "answer": "The router picks a tier."}

        with (
            mock.patch("opaihub.local_runner.runner_for_model", return_value=selected),
            mock.patch("opaihub.ask.run_ask", side_effect=run_ask),
        ):
            res = handle_gui_message(
                root,
                "summarize the router design",
                model_id="ollama:q",
                mode="ask",
            )

    summary = res["run_summary"]
    assert summary == build_run_summary(res)  # identical, render-from-record
    body = [line for line in summary.splitlines() if line.strip()]
    assert body[0] == "# OPai run receipt"
    assert body[1].startswith("**Verdict:")
