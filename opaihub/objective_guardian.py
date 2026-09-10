"""Independent worker custody: supervisor EOF drains a tree before capacity release."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess  # nosec B404 - trusted internal argv, shell=False
import sys
import threading
import time

from .atomic_io import atomic_write_text
from .objective_capacity import host_slot
from .process_tree import adopt, custody_kind, isolated_group_kwargs, terminate_tree
from .process_tree import terminate_tree_confirmed


def guardian_command(request: Path, response: Path, *, child=False) -> list[str]:
    from opai.bootstrap import _packaged_runtime

    flag = "--opai-objective-child" if child else "--opai-objective-guardian"
    command = (
        [sys.executable, flag]
        if _packaged_runtime()
        else [
            sys.executable,
            "-m",
            "opaihub.objective_guardian",
            *(["--child"] if child else []),
        ]
    )
    return [*command, str(request), str(response)]


def child_main(argv=None) -> int:
    """No worker import or child spawn is permitted until durable custody exists."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    if sys.stdin is None or os.read(sys.stdin.fileno(), 1) != b"G":
        return 3
    if sys.platform != "win32":
        os.environ["OPAI_OBJECTIVE_TREE_CUSTODY"] = "posix-group"
    config = Path(args[0]).parent / "launch.json"
    launch = json.loads(config.read_text(encoding="utf-8"))
    if launch.get("argv"):
        # A test fixture command inherits the already-custodied group/job.
        return subprocess.call(launch["argv"])  # nosec B603
    from .objective_worker import main as worker_main

    return worker_main(args)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--child"]:
        return child_main(args[1:])
    if len(args) != 2:
        return 2
    request, response = map(Path, args)
    packet = json.loads(request.read_text(encoding="utf-8"))
    launch = json.loads((request.parent / "launch.json").read_text(encoding="utf-8"))
    lost_parent = threading.Event()

    def watch_parent():
        try:
            if sys.stdin is not None:
                while os.read(sys.stdin.fileno(), 4096):
                    pass
        except (OSError, ValueError):
            pass
        lost_parent.set()

    # Raw reads avoid the buffered-stdin finalization lock during shutdown.
    threading.Thread(
        target=watch_parent, name="guardian-parent-watch", daemon=True
    ).start()
    store = None
    identity = {}
    if packet.get("objective_id"):
        from .agent_objectives import ObjectiveStore

        store = ObjectiveStore(Path(packet["authority_root"]))
        assignment_id = (packet.get("assignment") or {}).get("assignment_id")
        identity = {
            "assignment_id": assignment_id,
            "phase": None
            if assignment_id
            else (
                "integration"
                if packet.get("operation") == "verification"
                else "planning"
            ),
        }
    proc = None
    registered = False
    kind = "not-started"
    reason = "worker-exited"
    returncode = None
    try:
        with host_slot(lost_parent):
            proc = subprocess.Popen(
                guardian_command(request, response, child=True),
                cwd=packet["worktree"],
                stdin=subprocess.PIPE,
                **isolated_group_kwargs(),
            )  # nosec B603
            try:
                adopt(proc)
                kind = custody_kind(proc)
                if store:
                    store.record_execution_custody(
                        packet["objective_id"],
                        packet["owner"],
                        packet["fence"],
                        launch["execution_id"],
                        guardian_pid=os.getpid(),
                        worker_pid=proc.pid,
                        tree_kind=kind,
                        **identity,
                    )
                registered = True
                if not lost_parent.is_set():
                    proc.stdin.write(b"G")
                    proc.stdin.flush()
                while proc.poll() is None and not lost_parent.wait(0.05):
                    pass
                returncode = proc.poll()
                reason = (
                    "supervisor-disconnected"
                    if lost_parent.is_set()
                    else "worker-exited"
                )
            finally:
                if registered:
                    # Retain both custody and the host slot if the OS cannot
                    # confirm emptiness. A failed query never becomes proof.
                    while not terminate_tree_confirmed(proc):
                        time.sleep(0.2)
                else:
                    # The start gate was never opened, so no provider existed.
                    terminate_tree(proc)
                    proc.wait(timeout=10)
                if proc.stdin:
                    proc.stdin.close()
            proof = {
                "execution_id": launch["execution_id"],
                "owner": packet.get("owner"),
                "dispatch_fence": packet.get("fence"),
                "tree_kind": kind,
                "tree_terminated": True,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            }
            if store:
                store.record_execution_termination(
                    packet["objective_id"],
                    packet["owner"],
                    packet["fence"],
                    launch["execution_id"],
                    proof=proof,
                    **identity,
                )
            atomic_write_text(request.parent / "termination.json", json.dumps(proof))
            atomic_write_text(
                request.parent / "guardian.json",
                json.dumps(
                    {
                        "returncode": returncode,
                        "reason": reason,
                        "tree_terminated": True,
                    }
                ),
            )
    except InterruptedError:
        # Capacity wait was cancelled before any worker process was created.
        atomic_write_text(
            request.parent / "guardian.json",
            json.dumps(
                {
                    "returncode": None,
                    "reason": "cancelled-before-spawn",
                    "tree_terminated": True,
                }
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
