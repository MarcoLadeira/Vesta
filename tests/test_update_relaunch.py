"""OPai finishing its own update by restarting into it.

Claude Code and Codex both stop one step short: the new version is on disk
and you are told to run the command again. The failure mode this file guards
is the one that makes that caution look wise -- an app that closes itself and
does not come back.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from opai.update.relaunch import relaunch_command, schedule_relaunch


PY_LAUNCH = [str(Path("/opt/opai/opai/__main__.py")), "gui"]


def test_module_launch_is_rerun_through_the_same_interpreter():
    """`pythonw -m opai gui` must come back as `pythonw -m opai gui`.

    Reusing ``sys.executable`` is what keeps a windowed process windowed on
    Windows, and what keeps a virtualenv's OPai from being replaced by some
    other Python's.
    """
    command = relaunch_command(
        executable="/usr/bin/pythonw", argv=PY_LAUNCH, frozen=False
    )

    assert command == ["/usr/bin/pythonw", "-m", "opai", "gui"]


def test_existing_launcher_is_rerun_as_itself(tmp_path: Path):
    launcher = tmp_path / "opai-gui.exe"
    launcher.write_bytes(b"stub")

    command = relaunch_command(
        executable="/usr/bin/pythonw",
        argv=[str(launcher), "--project", "X"],
        frozen=False,
    )

    assert command == [str(launcher), "--project", "X"]


def test_a_launcher_is_never_rewritten_as_a_module_launch(tmp_path: Path):
    """The regression this file exists for.

    ``opai-gui.exe`` carries its subcommand *inside* the entry point, not in
    argv. Falling back to ``-m opai`` with argv[1:] silently drops ``gui`` and
    relaunches the CLI with no command -- an app that closed itself and did
    not come back.
    """
    command = relaunch_command(
        executable="/usr/bin/pythonw",
        argv=[str(tmp_path / "gone.exe"), "--project", "X"],
        frozen=False,
    )

    assert command is None


def test_frozen_bundles_rerun_the_bundle():
    command = relaunch_command(
        executable="/Apps/OPai.exe", argv=["/Apps/OPai.exe", "gui"], frozen=True
    )

    assert command == ["/Apps/OPai.exe", "gui"]


@pytest.mark.parametrize(
    ("executable", "argv"),
    [
        ("", PY_LAUNCH),  # no interpreter to reuse
        ("/usr/bin/pythonw", []),  # nothing to read the shape from
        ("/usr/bin/pythonw", ["opai"]),  # neither a launcher nor a module
    ],
    ids=["no-executable", "no-argv", "unrecognised"],
)
def test_an_unreadable_launch_shape_offers_no_restart(executable, argv):
    assert relaunch_command(executable=executable, argv=argv, frozen=False) is None


def test_arming_reports_failure_instead_of_pretending(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("no processes left")

    assert schedule_relaunch(["/usr/bin/pythonw"], spawn=refuse) is False


def test_nothing_is_armed_for_an_empty_command():
    calls: list[object] = []

    assert schedule_relaunch([], spawn=lambda *a, **k: calls.append(a)) is False
    assert calls == []


def test_a_frozen_bundle_cannot_host_the_supervisor(monkeypatch):
    """``sys.executable -c`` is how the supervisor runs, and a bundle has no -c.

    Saying so is better than spawning a second copy of the whole application
    to babysit the first.
    """
    calls: list[object] = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    armed = schedule_relaunch(["/Apps/OPai.exe"], spawn=lambda *a, **k: calls.append(a))

    assert armed is False
    assert calls == []


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


@pytest.mark.skipif(
    not Path(sys.executable).is_file(), reason="needs a real interpreter to spawn"
)
def test_the_replacement_starts_only_after_this_process_is_gone(tmp_path: Path):
    """The whole reason the restart is indirect.

    A new instance started while the old one is still alive races it for the
    QtWebEngine profile lock and the updater's own operation lease. The
    supervisor must sit on its hands until the process it was given exits.
    """
    marker = tmp_path / "restarted.txt"
    standin = subprocess.Popen(  # nosec B603 - fixed argv, no shell
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    try:
        armed = schedule_relaunch(
            [sys.executable, "-c", f"open(r'{marker}', 'w').write('restarted')"],
            pid=standin.pid,
            give_up_after_seconds=30,
        )
        assert armed
        time.sleep(1.5)
        assert not marker.exists()  # still waiting, because we are still alive
    finally:
        standin.terminate()
        standin.wait(timeout=30)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.2)
    assert marker.read_text(encoding="utf-8") == "restarted"


@pytest.mark.skipif(
    not Path(sys.executable).is_file(), reason="needs a real interpreter to spawn"
)
def test_a_window_that_never_closes_does_not_leave_a_process_spinning(tmp_path: Path):
    marker = tmp_path / "should-not-exist.txt"
    standin = subprocess.Popen(  # nosec B603 - fixed argv, no shell
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    try:
        assert schedule_relaunch(
            [sys.executable, "-c", f"open(r'{marker}', 'w').write('wrong')"],
            pid=standin.pid,
            give_up_after_seconds=1,
        )
        time.sleep(5)
        assert not marker.exists()
    finally:
        standin.terminate()
        standin.wait(timeout=30)

    time.sleep(2)
    assert not marker.exists()  # gave up; the late exit starts nothing
