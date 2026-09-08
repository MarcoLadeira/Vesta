import json
from pathlib import Path
import sys
import threading
import time
from unittest import mock

import pytest

from _helpers import make_repo
from opaihub.agent_objectives import ObjectiveStore
from opaihub.objective_execution import ObjectiveExecutor, parse_plan


def test_packaged_workers_use_internal_entrypoint_instead_of_python_module():
    from opaihub.objective_execution import worker_command

    with mock.patch("opai.bootstrap._packaged_runtime", return_value=True):
        assert worker_command(Path("request"), Path("response")) == [
            sys.executable,
            "--opai-objective-worker",
            "request",
            "response",
        ]


def fixture(tmp_path, assignments, *, check="assert True", **options):
    root = tmp_path / "repo"
    root.mkdir()
    policy = {
        "schema_version": 1,
        "checks": [
            {
                "id": "static_analysis",
                "kind": "static_analysis",
                "requirement": "required",
                "reason": "Validate the integrated fixture",
                "command": [sys.executable, "-c", check],
            }
        ],
    }
    make_repo(
        root,
        files={
            "a.txt": "old",
            "b.txt": "old",
            ".gitignore": ".opaihub/\n",
            "opai-verification-policy.yaml": json.dumps(policy),
        },
        commit=True,
    )
    store = ObjectiveStore(root)
    obj = store.create("Update documentation", assignments, **options)
    return root, store, obj["objective_id"]


def assignment(name, **extra):
    return {
        "name": name,
        "objective": "Update " + name,
        "intended_paths": [name + ".txt"],
        **extra,
    }


def completed():
    return {"status": "completed", "cost_usd": "0", "measurement_kind": "actual"}


def test_dependency_receives_observed_changes_and_only_owns_its_delta(tmp_path):
    root, store, oid = fixture(
        tmp_path,
        [assignment("a"), assignment("b", dependencies=["a"])],
        check="from pathlib import Path; assert Path('b.txt').read_text() == 'first second'",
    )

    def worker(packet, cancel, activity):
        path = Path(packet["worktree"])
        if packet["assignment"]["name"] == "a":
            (path / "a.txt").write_text("first")
        else:
            assert (path / "a.txt").read_text() == "first"
            (path / "b.txt").write_text((path / "a.txt").read_text() + " second")
        return completed()

    result = ObjectiveExecutor(
        root, store=store, worktree_root=root.parent.parent / "workers", worker=worker
    ).run(oid)
    assert result["status"] == "completed", result["integration"]
    assert result["assignments"][1]["changed_files"] == ["b.txt"]
    assert (root / "a.txt").read_text() == "old"


def test_planner_cannot_authorize_paid_model_and_review_role_is_read_only(tmp_path):
    packets = []
    executor = ObjectiveExecutor(
        tmp_path, worker=lambda packet, *_: packets.append(packet) or completed()
    )
    objective = {
        "objective_id": "o",
        "objective": "Review",
        "mode": "full-auto",
        "model": "auto",
        "allow_cloud": False,
    }
    row = {
        "assignment_id": "a",
        "task_id": "t",
        "run_id": "r",
        "fence": 1,
        "role": "reviewer",
        "model": "account:paid",
    }
    executor._invoke(objective, row, tmp_path, threading.Event(), None)
    assert packets[0]["model_id"] == "auto"
    assert packets[0]["mode"] == "plan"
    assert packets[0]["allow_cloud"] is False
    assert (
        str(
            parse_plan(
                '{"assignments":[{"name":"a","budget_usd":0.100000000000000001}]}'
            )[0]["budget_usd"]
        )
        == "0.100000000000000001"
    )


def test_external_stop_cancels_live_planner_without_creating_assignments(tmp_path):
    root, store, oid = fixture(tmp_path, [])
    entered = threading.Event()

    def worker(packet, cancel, activity):
        entered.set()
        assert cancel.wait(10)
        return {"status": "cancelled"}

    thread = threading.Thread(
        target=lambda: (entered.wait(10), ObjectiveStore(root).control(oid, "stop"))
    )
    thread.start()
    try:
        result = ObjectiveExecutor(
            root,
            store=store,
            worktree_root=root.parent.parent / "workers",
            worker=worker,
        ).run(oid)
    finally:
        thread.join(12)
    assert not thread.is_alive()
    assert result["status"] == "cancelled"
    assert result["assignments"] == []
    assert result["planning"]["owner"] == ""


@pytest.mark.parametrize(
    "check",
    [
        "assert False",
        "from pathlib import Path; Path('a.txt').write_text('changed by check')",
    ],
)
def test_failed_or_mutating_verification_never_completes_objective(tmp_path, check):
    root, store, oid = fixture(tmp_path, [assignment("a")], check=check)
    result = ObjectiveExecutor(
        root,
        store=store,
        worktree_root=root.parent.parent / "workers",
        worker=lambda *_: completed(),
    ).run(oid)
    assert result["status"] == "needs-attention"
    assert result["integration"]["owner"] == ""
    assert "reconcile" in result["allowed_actions"]


def test_stop_verification_terminates_check_before_terminal_state(tmp_path):
    beat = tmp_path / "check.beat"
    check = (
        "import pathlib,time; p=pathlib.Path("
        + repr(str(beat))
        + "); exec(\"while True:\\n with p.open('a') as f: f.write('.')\\n time.sleep(.02)\")"
    )
    root, store, oid = fixture(tmp_path, [assignment("a")], check=check)

    def stop_when_running():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not beat.exists():
            time.sleep(0.05)
        ObjectiveStore(root).control(oid, "stop")

    thread = threading.Thread(target=stop_when_running)
    thread.start()
    try:
        result = ObjectiveExecutor(
            root,
            store=store,
            worktree_root=root.parent.parent / "workers",
            worker=lambda *_: completed(),
        ).run(oid)
    finally:
        thread.join(22)
    assert beat.exists()
    assert result["status"] == "cancelled"
    size = beat.stat().st_size
    time.sleep(0.2)
    assert beat.stat().st_size == size


def test_incomplete_provider_answer_does_not_complete_assignment(tmp_path):
    root, store, oid = fixture(tmp_path, [assignment("a")])
    result = ObjectiveExecutor(
        root,
        store=store,
        worktree_root=root.parent.parent / "workers",
        worker=lambda *_: {
            "status": "answered",
            "completion_state": "needs_consent",
            "answer": "Please approve",
        },
    ).run(oid)
    assert result["assignments"][0]["status"] == "failed"
    assert result["status"] == "needs-attention"
