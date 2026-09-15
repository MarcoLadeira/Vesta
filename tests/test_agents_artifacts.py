from pathlib import Path
import json
import subprocess
import threading
from unittest import mock

import pytest

from _helpers import make_repo
from vesta.agents_bridge import inspect_objective_artifact, objective_worktree_path
from vestahub.agent_objectives import ObjectiveStore
from vestahub.objective_execution import ObjectiveExecutor


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def artifact(tmp_path):
    make_repo(tmp_path, files={"a.txt": "old\n"}, commit=True)
    store = ObjectiveStore(tmp_path)
    obj = store.create(
        "Update",
        [{"name": "a", "objective": "Update a", "intended_paths": ["."]}],
        mode="plan",
    )

    def worker(packet, cancel, activity):
        target = Path(packet["worktree"])
        (target / "a.txt").write_text("new\n", encoding="utf-8")
        (target / "added.txt").write_text(
            '<script>alert("untrusted")</script>', encoding="utf-8"
        )
        return {
            "status": "completed",
            "answer": "Done",
            "url": "https://evil.invalid/pr",
            "worktree": "/outside",
        }

    executor = ObjectiveExecutor(tmp_path, store=store, worker=worker)
    row = store.claim_next(obj["objective_id"], executor.owner)
    executor._assignment(obj["objective_id"], row, threading.Event())
    item = store.snapshot(obj["objective_id"])["assignments"][0]
    assert item["result"].get("git_evidence"), item
    return (
        tmp_path,
        {
            "objective_id": obj["objective_id"],
            "assignment_id": item["assignment_id"],
            "kind": "diff",
        },
        Path(item["worktree"]),
        item,
    )


def test_diff_uses_journal_evidence_including_untracked_content(artifact):
    root, request, target, item = artifact
    result = inspect_objective_artifact(root, request)
    assert result["ok"] and not result["truncated"]
    assert "-old" in result["text"] and "+new" in result["text"]
    assert "Untracked addition: added.txt" in result["text"]
    assert '<script>alert("untrusted")</script>' in result["text"]
    assert result["head_sha"] == item["result"]["git_evidence"]["head_sha"]
    assert objective_worktree_path(root, request) == target.resolve()


@pytest.mark.parametrize(
    "extra",
    [
        {"path": "/outside"},
        {"url": "https://evil.invalid"},
        {"assignment_id": []},
        {"assignment_id": ""},
    ],
)
def test_artifact_rejects_noncanonical_request(artifact, extra):
    root, request, _, _ = artifact
    with pytest.raises(ValueError):
        inspect_objective_artifact(root, {**request, **extra})


def test_changed_worktree_and_branch_fail_closed(artifact):
    root, request, target, _ = artifact
    (target / "a.txt").write_text("later", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        inspect_objective_artifact(root, request)
    git(target, "checkout", "-b", "codex/other")
    with pytest.raises(ValueError, match="identity"):
        objective_worktree_path(root, request)


def test_worker_inspection_has_a_file_read_budget(artifact):
    root, request, target, _ = artifact
    with (target / "a.txt").open("wb") as stream:
        stream.truncate(17 * 1024 * 1024)
    with pytest.raises(ValueError, match="inspection limit"):
        inspect_objective_artifact(root, request)


def test_inspection_never_runs_configured_git_helpers(artifact):
    root, request, target, _ = artifact
    sentinel = root / "helper-was-run"
    # A shell helper makes actual invocation observable without trusting a
    # mock of the safe argv. Git itself supplies its configured helper shell.
    helper = 'echo invoked > "' + sentinel.as_posix() + '"'
    git(target, "config", "core.fsmonitor", helper)
    git(target, "config", "diff.external", helper)
    try:
        result = inspect_objective_artifact(root, request)
        assert "+new" in result["text"]
        assert not sentinel.exists()
    finally:
        git(target, "config", "--unset", "core.fsmonitor")
        git(target, "config", "--unset", "diff.external")


def test_pr_is_read_only_and_resolved_from_canonical_origin_and_branch(artifact):
    from vesta import agents_bridge

    root, request, target, item = artifact
    git(root, "remote", "add", "origin", "https://github.com/example/project.git")
    pr = {
        "number": 12,
        "title": "<script>inert</script>",
        "state": "OPEN",
        "url": "https://github.com/example/project/pull/12",
        "headRefName": item["branch"],
        "headRefOid": item["result"]["git_evidence"]["head_sha"],
        "baseRefName": "main",
    }
    original = agents_bridge._artifact_command
    commands = []

    def command(cwd, argv, **kwargs):
        if argv[0] != "gh":
            return original(cwd, argv, **kwargs)
        commands.append(argv)
        return json.dumps(pr), False

    with mock.patch.object(agents_bridge, "_artifact_command", side_effect=command):
        result = inspect_objective_artifact(root, {**request, "kind": "pr"})
        assert result["pull_request"] == pr
        assert commands[0][:7] == [
            "gh",
            "pr",
            "view",
            item["branch"],
            "--repo",
            "example/project",
            "--json",
        ]
        pr["url"] = "https://evil.invalid/pull/12"
        with pytest.raises(ValueError, match="matches"):
            inspect_objective_artifact(root, {**request, "kind": "pr"})
