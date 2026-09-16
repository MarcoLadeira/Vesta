"""Cross-process durability regressions for background automation state (#439)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from vestahub import background_runs
from vestahub.background_runs import (
    enqueue_automation,
    list_runs,
    load_run,
    request_cancel,
    schedule_automation,
)
from vestahub.run_state import TERMINAL_STATES, RunState


ROOT = Path(__file__).resolve().parents[1]

_WORKER = r"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from vestahub.background_runs import BackgroundRunner, load_run, request_cancel, schedule_automation, tick_automations
from vestahub.run_state import RunState

root = Path(sys.argv[1])
barrier = Path(sys.argv[2])
kind = sys.argv[3]
worker = sys.argv[4]
run_id = sys.argv[5] if len(sys.argv) > 5 else ""
(barrier / f"{kind}-{worker}.ready").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 20
while not (barrier / "go").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("timed out waiting for test barrier")
    time.sleep(0.01)

if kind == "schedule":
    schedule_automation(root, "bug_fix", f"task {worker}", cadence="daily")
elif kind == "cancel":
    request_cancel(root, run_id)
elif kind == "finish":
    current = load_run(root, run_id)
    BackgroundRunner(root, executor=lambda *_args: {})._finish(
        current,
        run_state=RunState.COMPLETED,
        reason_code="background_completed",
        message="Background run completed",
        payload={"status": "answered"},
    )
elif kind == "tick":
    tick_automations(root, now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc))
else:
    raise ValueError(f"unknown worker kind: {kind}")
"""


def _wait_for_ready(barrier: Path, *, count: int) -> None:
    deadline = time.monotonic() + 20
    while len(list(barrier.glob("*.ready"))) < count:
        if time.monotonic() >= deadline:
            pytest.fail("worker processes did not reach the start barrier")
        time.sleep(0.01)


def _run_workers(
    root: Path,
    *,
    kinds: list[str],
    run_id: str = "",
) -> None:
    barrier = root / "background-persistence-barrier"
    barrier.mkdir()
    processes = [
        subprocess.Popen(  # nosec B603 - fixed hermetic Python argv
            [
                sys.executable,
                "-c",
                _WORKER,
                str(root),
                str(barrier),
                kind,
                str(index),
                run_id,
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index, kind in enumerate(kinds)
    ]
    try:
        _wait_for_ready(barrier, count=len(kinds))
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


def test_background_run_and_schedule_writes_use_shared_atomic_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    real_write = background_runs.atomic_write_text

    def record_write(target: Path, text: str) -> None:
        calls.append(Path(target))
        real_write(target, text)

    monkeypatch.setattr(background_runs, "atomic_write_text", record_write)
    run = enqueue_automation(tmp_path, "bug_fix", "task")
    schedule_automation(tmp_path, "bug_fix", "nightly sweep", cadence="daily")

    state_dir = tmp_path / ".vestahub" / "agent" / "background"
    assert calls == [
        state_dir / "runs" / f"{run.run_id}.json",
        state_dir / "schedules.json",
    ]


def test_concurrent_schedule_mutations_preserve_every_schedule(tmp_path: Path) -> None:
    _run_workers(tmp_path, kinds=["schedule"] * 8)

    schedules = background_runs.list_automation_schedules(tmp_path)
    assert len(schedules) == 8
    assert {item["task"] for item in schedules} == {
        f"task {index}" for index in range(8)
    }
    path = tmp_path / ".vestahub" / "agent" / "background" / "schedules.json"
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_concurrent_scheduler_ticks_enqueue_once_per_window(tmp_path: Path) -> None:
    schedule_automation(tmp_path, "bug_fix", "nightly sweep", cadence="daily")

    _run_workers(tmp_path, kinds=["tick"] * 6)

    runs = list_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].schedule_id


def test_concurrent_cancel_and_finalization_leave_one_immutable_terminal(
    tmp_path: Path,
) -> None:
    run = enqueue_automation(tmp_path, "bug_fix", "task")
    background_runs._transition_run(
        tmp_path,
        run.run_id,
        target=RunState.PREPARING,
        reason_code="preparing_execution",
    )
    background_runs._transition_run(
        tmp_path,
        run.run_id,
        target=RunState.RUNNING,
        reason_code="execution_started",
    )

    _run_workers(tmp_path, kinds=["cancel", "finish"], run_id=run.run_id)

    final = load_run(tmp_path, run.run_id)
    assert RunState(final.run_state) in TERMINAL_STATES
    assert final.state_history[-1]["state"] == final.run_state
    snapshot = final.to_dict()
    assert request_cancel(tmp_path, run.run_id).to_dict() == snapshot
    path = (
        tmp_path / ".vestahub" / "agent" / "background" / "runs" / f"{run.run_id}.json"
    )
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []
