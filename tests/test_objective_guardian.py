"""Real local subprocesses prove custody survives the fixture supervisor."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from _helpers import make_repo
from opaihub.agent_objectives import ObjectiveStore
from opaihub.atomic_io import InterprocessLockTimeout, interprocess_transaction
from opaihub.objective_execution import (
    UnconfirmedTerminationError,
    run_worker_process,
)
from opaihub.process_tree import isolated_group_kwargs


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Fixture did not reach the expected state")


def process_alive(pid):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.fixture
def fixture_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    return home


def fixture_tree(tmp_path, *, root_exits=False, escape=None):
    leaf = tmp_path / "leaf.py"
    leaf.write_text(
        "import os,time\nfrom pathlib import Path\n"
        + (
            "if os.fork(): os._exit(0)\nos.setsid()\nif os.fork(): os._exit(0)\n"
            if escape == "double-fork"
            else ""
        )
        + f"p=Path({str(tmp_path / 'leaf.pid')!r}); p.write_text(str(os.getpid()))\n"
        f"beat=Path({str(tmp_path / 'leaf.beat')!r})\n"
        "while True:\n with beat.open('a') as f: f.write('.')\n time.sleep(.02)\n"
    )
    provider = tmp_path / "provider.py"
    provider.write_text(
        "import os,subprocess,sys,time\nfrom pathlib import Path\n"
        "from opaihub.process_tree import isolated_group_kwargs\n"
        f"Path({str(tmp_path / 'provider.pid')!r}).write_text(str(os.getpid()))\n"
        f"p=subprocess.Popen([sys.executable,{str(leaf)!r}], **"
        + (
            "{'start_new_session':True}"
            if escape == "setsid"
            else "isolated_group_kwargs()"
        )
        + ")\n"
        f"while not Path({str(tmp_path / 'leaf.beat')!r}).exists(): time.sleep(.02)\n"
        + ("" if root_exits else "time.sleep(120)\n")
    )
    return [sys.executable, str(provider)]


def packet_for(tmp_path):
    return {
        "authority_root": str(tmp_path),
        "worktree": str(tmp_path),
        "run_id": "fixture-run",
        "operation_key": "fixture-provider",
    }


def test_guardian_reports_failed_setup_without_claiming_child_custody(
    tmp_path, monkeypatch
):
    from contextlib import nullcontext
    from opaihub import objective_guardian

    request, response = tmp_path / "request.json", tmp_path / "response.json"
    request.write_text(json.dumps(packet_for(tmp_path)))
    (tmp_path / "launch.json").write_text(
        json.dumps({"execution_id": "setup-failure", "argv": None})
    )
    monkeypatch.setattr(objective_guardian, "host_slot", lambda *_: nullcontext())
    monkeypatch.setattr(objective_guardian.sys, "stdin", None)
    monkeypatch.setattr(
        objective_guardian,
        "prepare_guardian_custody",
        lambda: (_ for _ in ()).throw(RuntimeError("Unsupported custody")),
    )
    monkeypatch.setattr(
        objective_guardian.subprocess,
        "Popen",
        lambda *_a, **_k: pytest.fail("No child may spawn after failed setup"),
    )
    assert objective_guardian.main([str(request), str(response)]) == 0
    result = json.loads(response.read_text())
    assert (
        result["status"] == "blocked" and result["dispatch_state"] == "not-dispatched"
    )
    assert result["objective_cost_events"][0]["amount_usd"] == "0"
    guardian = json.loads((tmp_path / "guardian.json").read_text())
    assert guardian["reason"] == "failed-before-spawn"
    assert guardian["execution_id"] == "setup-failure"
    assert not (tmp_path / "termination.json").exists()


@pytest.mark.parametrize("escape", [None, "setsid", "double-fork"])
def test_normal_exit_kills_descendants_before_return(tmp_path, fixture_home, escape):
    if escape and sys.platform != "linux":
        pytest.skip("Linux subreaper qualification requires a real Linux kernel")
    argv = fixture_tree(tmp_path, root_exits=True, escape=escape)
    result = run_worker_process(
        packet_for(tmp_path), tmp_path / "worker", threading.Event(), argv=argv
    )
    assert result["status"] == "failed"  # Fixture intentionally emits no response.
    proof = json.loads((tmp_path / "worker" / "termination.json").read_text())
    assert proof["tree_terminated"] is True
    assert proof["reason"] == "worker-exited"
    assert not process_alive(int((tmp_path / "leaf.pid").read_text()))


@pytest.mark.parametrize("escape", [None, "setsid", "double-fork"])
def test_cancel_kills_descendants_before_terminal_evidence(
    tmp_path, fixture_home, escape
):
    if escape and sys.platform != "linux":
        pytest.skip("Linux subreaper qualification requires a real Linux kernel")
    argv = fixture_tree(tmp_path, escape=escape)
    cancel = threading.Event()

    def stop():
        try:
            wait_until(lambda: (tmp_path / "leaf.beat").exists())
        finally:
            cancel.set()

    thread = threading.Thread(target=stop)
    thread.start()
    try:
        result = run_worker_process(
            packet_for(tmp_path), tmp_path / "worker", cancel, argv=argv
        )
    finally:
        thread.join(25)
    assert result["status"] == "cancelled"
    assert result["cancellation"]["phase"] == "terminated"
    for name in ("provider", "leaf"):
        assert not process_alive(int((tmp_path / (name + ".pid")).read_text()))


@pytest.mark.parametrize("escape", [None, "setsid", "double-fork"])
def test_supervisor_loss_retains_slot_until_canonical_tree_proof(
    tmp_path, fixture_home, escape
):
    if escape and sys.platform != "linux":
        pytest.skip("Linux subreaper qualification requires a real Linux kernel")
    root = tmp_path / "repo"
    root.mkdir()
    make_repo(root, files={"a.txt": "old"}, commit=True)
    store = ObjectiveStore(root)
    objective = store.create(
        "Fixture recovery",
        [{"name": "a", "objective": "Observe", "intended_paths": ["a.txt"]}],
    )
    oid = objective["objective_id"]
    owner = "fixture-supervisor"
    assignment = store.claim_next(oid, owner)
    packet = {
        **packet_for(root),
        "objective_id": oid,
        "owner": owner,
        "fence": assignment["fence"],
        "assignment": assignment,
        "run_id": assignment["run_id"],
    }
    argv = fixture_tree(tmp_path, escape=escape)
    evidence = tmp_path / "worker"
    draining, release = tmp_path / "draining", tmp_path / "release"
    wrapper = tmp_path / "guardian_fixture.py"
    wrapper.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "from opaihub import objective_guardian as g\n"
        "original=g.terminate_tree_confirmed\n"
        "def drain(proc):\n"
        f" Path({str(draining)!r}).touch()\n"
        f" while not Path({str(release)!r}).exists(): time.sleep(.02)\n"
        " return original(proc)\n"
        "g.terminate_tree_confirmed=drain\nraise SystemExit(g.main())\n"
    )
    config = tmp_path / "supervisor.json"
    config.write_text(json.dumps({"packet": packet, "argv": argv}))
    supervisor = tmp_path / "supervisor.py"
    supervisor.write_text(
        "import json,sys,threading\nfrom pathlib import Path\n"
        "from opaihub import objective_guardian as g\n"
        "from opaihub.objective_execution import run_worker_process\n"
        f"g.guardian_command=lambda req,res: [sys.executable,{str(wrapper)!r},str(req),str(res)]\n"
        f"config=json.loads(Path({str(config)!r}).read_text())\n"
        f"run_worker_process(config['packet'],Path({str(evidence)!r}),threading.Event(),argv=config['argv'])\n"
    )
    with (tmp_path / "supervisor.log").open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, str(supervisor)],
            stdout=log,
            stderr=log,
            **isolated_group_kwargs(),
        )
        try:
            wait_until(lambda: (tmp_path / "leaf.beat").exists())
            custody = store.snapshot(oid)["assignments"][0]["execution"]
            assert custody["termination_proof"] is None
            proc.kill()  # Kill only the fixture supervisor, leaving its guardian.
            proc.wait(timeout=10)
            wait_until(draining.exists)
            assert process_alive(custody["guardian_pid"])
            assert process_alive(int((tmp_path / "leaf.pid").read_text()))
            slot = fixture_home / ".opaihub" / "runtime" / "agent-slots" / "worker-0"
            with pytest.raises(InterprocessLockTimeout):
                with interprocess_transaction(slot, timeout_seconds=0):
                    pass
            store.recover_expired(oid, now="9999-01-01T00:00:00+00:00")
            interrupted = store.snapshot(oid)["assignments"][0]
            assert interrupted["owner"] == owner
            assert interrupted["status"] == "needs-attention"
            release.touch()
            wait_until(lambda: not store.snapshot(oid)["assignments"][0]["owner"])
            recovered = store.snapshot(oid)["assignments"][0]
            assert recovered["status"] == "needs-attention"
            assert (
                recovered["execution"]["termination_proof"]["reason"]
                == "supervisor-disconnected"
            )
            assert recovered["run_id"] == assignment["run_id"]
            assert store.claim_next(oid, "replacement") is None
            for name in ("provider", "leaf"):
                assert not process_alive(int((tmp_path / (name + ".pid")).read_text()))
            wait_until(lambda: not process_alive(custody["guardian_pid"]))
            with interprocess_transaction(slot, timeout_seconds=1):
                pass
        finally:
            release.touch()
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)


@pytest.mark.parametrize(
    "proof", [None, "{", "[]", '{"tree_terminated":true,"execution_id":"other"}']
)
def test_missing_or_invalid_proof_is_never_an_ordinary_failure(
    tmp_path, fixture_home, proof
):
    fake = tmp_path / "fake_guardian.py"
    fake.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "p=Path(sys.argv[1]).parent\n"
        "(p/'guardian.json').write_text(json.dumps({'tree_terminated':True,'reason':'worker-exited','returncode':0}))\n"
        + (
            f"(p/'termination.json').write_text({proof!r})\n"
            if proof is not None
            else ""
        )
    )
    with mock.patch(
        "opaihub.objective_guardian.guardian_command",
        side_effect=lambda req, res: [sys.executable, str(fake), str(req), str(res)],
    ):
        with pytest.raises(UnconfirmedTerminationError):
            run_worker_process(
                packet_for(tmp_path), tmp_path / "worker", threading.Event()
            )


@pytest.mark.parametrize(
    "flag,entry",
    [("--opai-objective-guardian", "main"), ("--opai-objective-child", "child_main")],
)
def test_packaged_guardian_entrypoints_preflight_without_qt(flag, entry):
    from opai import bootstrap

    imported = []

    def importer(name):
        imported.append(name)
        return SimpleNamespace(
            **{entry: lambda args: 0 if args == ["request", "response"] else 9}
        )

    assert (
        bootstrap.run_desktop(
            [flag, "request", "response"],
            source_root=Path(__file__).resolve().parents[1],
            spec_finder=lambda name: (
                None if name == "PySide6" else SimpleNamespace(name=name)
            ),
            distribution_lookup=lambda name: "0.2.1a1",
            importer=importer,
        )
        == 0
    )
    assert imported == ["opaihub.objective_guardian"]
    assert bootstrap.run_cli([flag]) == 2


def test_posix_nested_processes_preserve_guardian_group(monkeypatch):
    from opaihub import process_tree

    monkeypatch.setattr(process_tree.sys, "platform", "linux")
    monkeypatch.setenv("OPAI_OBJECTIVE_TREE_CUSTODY", "posix-group")
    assert process_tree.isolated_group_kwargs() == {}


def test_failed_job_query_retains_custody(monkeypatch):
    from opaihub import process_tree

    monkeypatch.setattr(process_tree.sys, "platform", "win32")
    job = process_tree._Job(123)
    proc = SimpleNamespace(pid=42, poll=lambda: 0, _opai_job_handle=job)
    monkeypatch.setattr(
        process_tree,
        "_kernel32",
        lambda: SimpleNamespace(TerminateJobObject=lambda *args: True),
    )
    monkeypatch.setattr(
        process_tree,
        "_job_active_processes",
        mock.Mock(side_effect=OSError("query failed")),
    )
    assert process_tree.terminate_tree_confirmed(proc, timeout=0) is False
    assert job.handle == 123


def test_unreadable_linux_children_never_prove_termination(monkeypatch):
    from opaihub import process_tree

    custody = object.__new__(process_tree._LinuxSubreaper)
    custody.drained = False
    monkeypatch.setattr(
        process_tree, "_linux_children", mock.Mock(side_effect=OSError("no procfs"))
    )
    assert custody.terminate(SimpleNamespace(poll=lambda: 0), timeout=0) is False
    assert custody.drained is False


def test_non_linux_posix_refuses_unproven_group_custody(monkeypatch):
    from opaihub import process_tree

    monkeypatch.setattr(process_tree.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="unavailable"):
        process_tree.prepare_guardian_custody()
    with pytest.raises(RuntimeError, match="could not be established"):
        process_tree.custody_kind(SimpleNamespace(pid=123, _opai_pgid=123))


@pytest.mark.skipif(sys.platform != "linux", reason="Real Linux subreaper API")
def test_failed_subreaper_setup_never_opens_start_gate(monkeypatch):
    from opaihub import process_tree

    monkeypatch.setattr(process_tree, "_linux_children", lambda: set())
    monkeypatch.setattr(process_tree, "_child_subreaper", lambda **kwargs: False)
    with pytest.raises(RuntimeError, match="could not be established"):
        process_tree.prepare_guardian_custody()


def test_guardian_bootstrap_ignores_worktree_python_modules(tmp_path, fixture_home):
    marker = tmp_path / "untrusted-import"
    (tmp_path / "json.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).touch(); raise RuntimeError('untrusted json')"
    )
    result = run_worker_process(
        packet_for(tmp_path),
        tmp_path / "worker",
        threading.Event(),
        argv=[sys.executable, "-c", "pass"],
    )
    assert result["status"] == "failed"
    assert not marker.exists()
    assert json.loads((tmp_path / "worker" / "termination.json").read_text())[
        "tree_terminated"
    ]


def test_supervisor_loss_during_integration_retains_artifact_and_stops_check(
    tmp_path, fixture_home
):
    root = tmp_path / "repo"
    root.mkdir()
    beat = tmp_path / "integration.beat"
    check_pid = tmp_path / "check.pid"
    check = (
        "import os,time; from pathlib import Path; "
        f"Path({str(check_pid)!r}).write_text(str(os.getpid())); "
        f"p=Path({str(beat)!r}); "
        "exec(\"while True:\\n with p.open('a') as f: f.write('.')\\n time.sleep(.02)\")"
    )
    policy = {
        "schema_version": 1,
        "checks": [
            {
                "id": "static_analysis",
                "kind": "static_analysis",
                "requirement": "required",
                "reason": "Fixture integration check",
                "command": [sys.executable, "-c", check],
            }
        ],
    }
    make_repo(
        root,
        files={
            "a.txt": "old",
            ".gitignore": ".opaihub/\n",
            "opai-verification-policy.yaml": json.dumps(policy),
        },
        commit=True,
    )
    store = ObjectiveStore(root)
    oid = store.create(
        "Update documentation",
        [{"name": "a", "objective": "Update docs", "intended_paths": ["a.txt"]}],
    )["objective_id"]
    script = tmp_path / "integration_supervisor.py"
    script.write_text(
        "from pathlib import Path\nfrom opaihub.objective_execution import ObjectiveExecutor\n"
        "def worker(*args): return {'status':'completed','cost_usd':'0','measurement_kind':'actual'}\n"
        f"ObjectiveExecutor(Path({str(root)!r}), worker=worker, worktree_root=Path({str(tmp_path / 'workers')!r})).run({oid!r})\n"
    )
    with (tmp_path / "integration.log").open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            stdout=log,
            stderr=log,
            **isolated_group_kwargs(),
        )
        try:
            wait_until(beat.exists, timeout=40)
            active = store.snapshot(oid)["integration"]
            assert Path(active["worktree"]).is_dir()
            proc.kill()
            proc.wait(timeout=10)
            store.recover_expired(oid, now="9999-01-01T00:00:00+00:00")
            wait_until(lambda: not store.snapshot(oid)["integration"]["owner"])
            result = store.snapshot(oid)
            assert result["status"] == "needs-attention"
            assert result["integration"]["worktree"] == active["worktree"]
            assert (
                result["integration"]["execution"]["termination_proof"][
                    "tree_terminated"
                ]
                is True
            )
            assert not process_alive(int(check_pid.read_text()))
            assert result["cost_complete"] is True
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
