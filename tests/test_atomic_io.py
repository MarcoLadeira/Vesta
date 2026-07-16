"""Cross-process tests for the shared atomic state I/O primitives."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

_WRITER = r"""
import json
import sys
import time
from pathlib import Path

target = Path(sys.argv[1])
barrier = Path(sys.argv[2])
worker = sys.argv[3]
(barrier / f"{worker}.ready").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 20
while not (barrier / "go").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("timed out waiting for multiprocess test barrier")
    time.sleep(0.01)

from opaihub.atomic_io import atomic_write_text, interprocess_transaction

with interprocess_transaction(target):
    state = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"writes": 0}
    result = int(state["writes"])
    time.sleep(0.02)
    atomic_write_text(target, json.dumps({"writes": result + 1}))
print(result, flush=True)
"""

_LOCK_HOLDER = r"""
import sys
import time
from pathlib import Path

from opaihub.atomic_io import interprocess_transaction

target = Path(sys.argv[1])
ready = Path(sys.argv[2])
release = Path(sys.argv[3])
with interprocess_transaction(target):
    ready.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting to release test lock")
        time.sleep(0.01)
"""


def _wait_for_files(pattern: str, directory: Path, count: int) -> None:
    deadline = time.monotonic() + 20
    while len(list(directory.glob(pattern))) < count:
        if time.monotonic() >= deadline:
            pytest.fail("worker processes did not reach the start barrier")
        time.sleep(0.01)


def run_writers(workers: int, target: Path) -> list[int]:
    barrier = target.parent / "barrier"
    barrier.mkdir()
    processes = [
        subprocess.Popen(  # nosec B603 - fixed hermetic Python argv
            [sys.executable, "-c", _WRITER, str(target), str(barrier), str(index)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(workers)
    ]
    try:
        _wait_for_files("*.ready", barrier, workers)
        (barrier / "go").write_text("go", encoding="utf-8")
        completed = [process.communicate(timeout=30) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    failures = [
        {
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        for process, (stdout, stderr) in zip(processes, completed, strict=True)
        if process.returncode
    ]
    assert failures == []
    return [int(stdout.strip()) for stdout, _stderr in completed]


def test_eight_processes_serialize_transactions(tmp_path: Path) -> None:
    target = tmp_path / "state.json"

    results = run_writers(8, target)

    assert sorted(results) == list(range(8))
    assert json.loads(target.read_text(encoding="utf-8"))["writes"] == 8
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_lock_contention_raises_typed_timeout(tmp_path: Path) -> None:
    from opaihub.atomic_io import InterprocessLockTimeout, interprocess_transaction

    target = tmp_path / "state.json"
    ready = tmp_path / "holder.ready"
    release = tmp_path / "holder.release"
    holder = subprocess.Popen(  # nosec B603 - fixed hermetic Python argv
        [sys.executable, "-c", _LOCK_HOLDER, str(target), str(ready), str(release)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_files("holder.ready", tmp_path, 1)
        with pytest.raises(InterprocessLockTimeout, match="state.json"):
            with interprocess_transaction(target, timeout_seconds=0.05):
                pytest.fail("contended transaction unexpectedly acquired its lock")
    finally:
        release.write_text("release", encoding="utf-8")
        stdout, stderr = holder.communicate(timeout=5)
        if holder.returncode:
            pytest.fail(f"lock holder failed:\nstdout:\n{stdout}\nstderr:\n{stderr}")


def test_atomic_write_text_uses_unique_sibling_temps_and_cleans_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opaihub import atomic_io

    target = tmp_path / "state.txt"
    target.write_text("old", encoding="utf-8")
    sources: list[Path] = []
    real_replace = atomic_io.os.replace

    def record_replace(source: Path, destination: Path) -> None:
        sources.append(Path(source))
        real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", record_replace)

    atomic_io.atomic_write_text(target, "first")
    atomic_io.atomic_write_text(target, "caf\N{LATIN SMALL LETTER E WITH ACUTE}")

    assert target.read_text(encoding="utf-8") == "caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    assert len(sources) == 2
    assert sources[0] != sources[1]
    assert all(source.parent == target.parent for source in sources)
    assert all(source.name.startswith(f".{target.name}.") for source in sources)
    assert all(not source.exists() for source in sources)
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_failed_replace_preserves_previous_bytes_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opaihub import atomic_io

    target = tmp_path / "state.json"
    previous = b'{"writes": 1}'
    target.write_bytes(previous)
    sources: list[Path] = []

    def deny_replace(source: Path, destination: Path) -> None:
        sources.append(Path(source))
        raise PermissionError(f"sharing violation: {source} -> {destination}")

    monkeypatch.setattr(atomic_io.os, "replace", deny_replace)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError, match="sharing violation"):
        atomic_io.atomic_write_text(target, '{"writes": 2}')

    assert 1 < len(sources) < 100
    assert len(set(sources)) == 1
    assert sources[0].parent == target.parent
    assert not sources[0].exists()
    assert target.read_bytes() == previous
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_atomic_writes_never_expose_partial_json_to_readers(tmp_path: Path) -> None:
    from opaihub.atomic_io import atomic_write_text

    target = tmp_path / "state.json"
    atomic_write_text(target, json.dumps({"write": -1, "payload": "seed"}))
    stop = threading.Event()
    ready = threading.Event()
    observed: list[int] = []
    failures: list[str] = []

    def read_repeatedly() -> None:
        ready.set()
        while not stop.is_set():
            try:
                state = json.loads(target.read_text(encoding="utf-8"))
                observed.append(int(state["write"]))
                time.sleep(0.001)
            except PermissionError:
                # Windows can briefly reject an open while os.replace retries a
                # sharing violation. No bytes were exposed, so retry the read.
                time.sleep(0.001)
                continue
            except (OSError, TypeError, ValueError, KeyError) as error:
                failures.append(repr(error))
                stop.set()

    reader = threading.Thread(target=read_repeatedly)
    reader.start()
    assert ready.wait(timeout=5)
    try:
        for write in range(100):
            atomic_write_text(
                target,
                json.dumps({"write": write, "payload": "x" * 32_000}),
            )
    finally:
        stop.set()
        reader.join(timeout=5)

    assert not reader.is_alive()
    assert failures == []
    assert observed
    assert json.loads(target.read_text(encoding="utf-8"))["write"] == 99
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []
