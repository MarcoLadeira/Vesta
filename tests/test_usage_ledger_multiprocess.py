"""Cross-process sequencing and idempotency for the usage ledger."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vestahub.ledger import ledger_head_path, read_events


ROOT = Path(__file__).resolve().parents[1]
MODEL = "account:claude:opus-4.8"

_CHILD = r"""
import sys
import time
from pathlib import Path

from vestahub.ledger import record_model_call_finalized, record_model_call_started, reset_usage_baseline
from vestahub.usage_report import ProviderTurnUsage

root = Path(sys.argv[1])
barrier = Path(sys.argv[2])
worker = int(sys.argv[3])
mode = sys.argv[4]
(barrier / f"{worker}.ready").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 20
while not (barrier / "go").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("timed out waiting for multiprocess test barrier")
    time.sleep(0.01)

if mode == "unique":
    call_id = f"run-{worker}:{worker + 1}"
    run_id = f"run-{worker}"
    turn_index = worker + 1
else:
    call_id = "shared-run:1"
    run_id = "shared-run"
    turn_index = 1

record_model_call_started(
    root,
    "private task",
    call_id=call_id,
    run_id=run_id,
    turn_index=turn_index,
    model_id="account:claude:opus-4.8",
    provider_id="claude",
    model_tier="L3",
    provider_type="account",
    confirmed=True,
)
if mode == "unique" and worker % 2 == 0:
    reset_usage_baseline(root, "account:claude:opus")
record_model_call_finalized(
    root,
    "private task",
    call_id=call_id,
    usage=ProviderTurnUsage.from_provider(
        turn_index=turn_index,
        total=worker + 1 if mode == "unique" else 1,
    ),
)
"""


def _run_workers(tmp_path: Path, *, workers: int, mode: str) -> list[dict]:
    root = tmp_path / "repo"
    barrier = tmp_path / "barrier"
    root.mkdir()
    barrier.mkdir()
    processes = [
        subprocess.Popen(  # nosec B603 - fixed hermetic Python argv
            [sys.executable, "-c", _CHILD, str(root), str(barrier), str(index), mode],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(workers)
    ]
    completed: list[tuple[str, str]] = []
    try:
        deadline = time.monotonic() + 20
        while len(list(barrier.glob("*.ready"))) < workers:
            if time.monotonic() >= deadline:
                pytest.fail("worker processes did not reach the start barrier")
            time.sleep(0.01)
        (barrier / "go").write_text("go", encoding="utf-8")
        completed = [process.communicate(timeout=30) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    failures = [
        {"returncode": process.returncode, "stdout": stdout, "stderr": stderr}
        for process, (stdout, stderr) in zip(processes, completed, strict=True)
        if process.returncode
    ]
    assert failures == []
    return read_events(root)


def test_eight_process_start_reset_finalize_race_loses_no_events(
    tmp_path: Path,
) -> None:
    events = _run_workers(tmp_path, workers=8, mode="unique")
    root = tmp_path / "repo"

    assert len(events) == 20
    assert [event["ledger_sequence"] for event in events] == list(range(1, 21))
    starts = {
        event["call_id"]: event
        for event in events
        if event["event_type"] == "model_call_started"
    }
    finals = {
        event["call_id"]: event
        for event in events
        if event["event_type"] == "model_call"
    }
    resets = [
        event for event in events if event["event_type"] == "usage_baseline_reset"
    ]
    assert len(starts) == len(finals) == 8
    assert len(resets) == 4
    assert all(
        finals[call_id]["usage_epoch"] == start["usage_epoch"]
        for call_id, start in starts.items()
    )
    head = json.loads(ledger_head_path(root).read_text(encoding="utf-8"))
    assert head["last_sequence"] == 20
    assert head["active_calls"] == {}
    assert "finalized_calls" not in head


def test_cross_process_retries_of_one_call_are_idempotent(tmp_path: Path) -> None:
    events = _run_workers(tmp_path, workers=8, mode="shared")

    assert [event["event_type"] for event in events] == [
        "model_call_started",
        "model_call",
    ]
    assert [event["ledger_sequence"] for event in events] == [1, 2]
