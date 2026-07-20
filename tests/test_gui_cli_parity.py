"""GUI and CLI speak one language for the same run (#396).

The verdict vocabulary lives once in opaihub.completion; the receipt summary, the
CLI outcome block, and the GUI (assets/web/app.js) all render from it, so an
equivalent run never reads as "Timeout" on one surface and "Timed out" on
another. These tests are the sync guard that keeps them from drifting apart.
"""

from __future__ import annotations

import re
from pathlib import Path

from opaihub.completion import VERDICT_LABELS, CompletionVerdict, verdict_label
from opaihub.run_summary import build_run_summary
from opai.cli_stream import _evidence_line

_APP_JS = Path(__file__).resolve().parents[1] / "opai" / "assets" / "web" / "app.js"


def test_every_verdict_has_one_shared_label() -> None:
    # Every terminal verdict is labelled, and the one word that is not naive
    # title-case ("timeout") reads honestly.
    for verdict in CompletionVerdict:
        assert verdict.value in VERDICT_LABELS
    assert verdict_label("timeout") == "Timed out"
    assert verdict_label(CompletionVerdict.COMPLETED) == "Completed"
    assert verdict_label("mystery_state") == "Mystery State"  # graceful fallback


def test_cli_and_receipt_summary_render_identical_verdict_labels() -> None:
    # The exact anti-divergence guarantee: the label the CLI prints for a verdict
    # is byte-identical to the one the shareable receipt summary leads with.
    for verdict in CompletionVerdict:
        record = {
            "completion_verdict": {
                "verdict": verdict.value,
                "reason": "a reason",
                "objective": {"objective_text": "do it", "mode": "ask"},
            },
            "receipt": {},
        }
        summary_head = build_run_summary(record).splitlines()[2]
        assert summary_head.startswith(f"**Verdict: {verdict_label(verdict.value)}**")


def test_cli_outcome_block_reports_changed_file_evidence() -> None:
    assert _evidence_line({"changed_files": ["a.py", "b.py"]}) == (
        "Changed 2 file(s): a.py, b.py"
    )
    assert _evidence_line({"changed_files": []}) is None
    assert _evidence_line({}) is None
    # Long lists are truncated, never dumped.
    line = _evidence_line({"changed_files": [f"f{i}.py" for i in range(12)]})
    assert "(+4 more)" in line


def _app_js_verdict_labels() -> dict[str, str]:
    text = _APP_JS.read_text(encoding="utf-8")
    match = re.search(r"const VERDICT_LABELS = \{(.*?)\};", text, re.DOTALL)
    assert match, "app.js must define a VERDICT_LABELS map"
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', match.group(1)))


def test_app_js_verdict_labels_stay_in_sync_with_python() -> None:
    # The JS mirror is the one place parity can silently rot; assert it matches
    # the Python source of truth exactly.
    assert _app_js_verdict_labels() == VERDICT_LABELS
