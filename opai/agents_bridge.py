"""Shared desktop/CLI boundary for canonical engineering objectives."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import hashlib
import os
import re
import stat
import subprocess  # nosec B404
import threading
import uuid

from opaihub.agent_objectives import ObjectiveStore
from opaihub.objective_execution import ObjectiveExecutor
from opaihub.gui_preferences import MODES
from opaihub.process_tree import objective_runtime_support


def create_objective_payload(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Expected an objective request")
    runtime = objective_runtime_support()
    if not runtime["supported"]:
        raise ValueError(runtime["reason"])
    text = payload.get("text")
    mode = payload.get("mode", "safe-auto")
    model = payload.get("model", "auto")
    limit = payload.get("maxParallel", 2)
    if not isinstance(text, str) or not text.strip() or len(text) > 16000:
        raise ValueError("An objective must contain between 1 and 16000 characters")
    if mode not in MODES or not isinstance(model, str) or not model or len(model) > 200:
        raise ValueError("Choose a supported mode and model")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4:
        raise ValueError("Concurrency must be between 1 and 4")
    request = str(payload.get("requestId") or uuid.uuid4().hex)
    identity = uuid.uuid5(uuid.NAMESPACE_URL, f"{root.resolve()}:{request}").hex
    return ObjectiveStore(root).create(
        text.strip(),
        [],
        task_id=f"objective-{identity}",
        run_id=f"objective-run-{identity}",
        mode=mode,
        model=model,
        max_parallel=limit,
        budget_usd=payload.get("budgetUsd"),
        shared_context="",
        allow_cloud=payload.get("allowCloud") is True,
        bypass_permissions=payload.get("bypassPermissions") is True,
    )


def control_objective_payload(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("objective_id"), str
    ):
        raise ValueError("A canonical objective ID is required")
    action = payload.get("action")
    actions = {
        "rename": "rename",
        "run": "run",
        "cancel": "stop",
        "stop": "stop",
        "pause": "pause",
        "resume": "resume",
        "sequential": "sequential",
        "set_budget": "budget",
        "budget": "budget",
        "prioritize": "prioritize",
        "reroute": "reroute",
        "reconcile": "reconcile",
        "verify": "verify",
        "approve": "approve",
        "retry": "retry",
        "request_review": "request_review",
    }
    from opaihub.agent_team import TEAM_ACTIONS

    actions.update({name: name for name in TEAM_ACTIONS})
    if action not in actions:
        raise ValueError("Unsupported objective control")
    value = payload.get("value")
    for field in {
        "set_budget": ["budget_usd"],
        "prioritize": ["priority"],
        "reroute": ["model"],
    }.get(action, []):
        value = payload.get(field, value)
    if action == "reroute" and isinstance(value, str):
        value = {"model": value}
    result = ObjectiveExecutor(root).control(
        payload["objective_id"],
        actions[action],
        assignment_id=payload.get("assignment_id"),
        value=value,
    )
    return {
        "ok": True,
        "objective": result,
        "workspaceRoot": str(root.resolve()),
        "control": {
            "action": action,
            "assignment_id": payload.get("assignment_id"),
            "revision": value.get("revision") if isinstance(value, dict) else None,
        },
    }


def objectives_payload(root: Path) -> dict[str, Any]:
    store = ObjectiveStore(root)
    store.recover_expired()
    return {
        "objectives": store.list_objectives(),
        "agentsRuntime": objective_runtime_support(),
        "workspaceRoot": str(root.resolve()),
    }


def _objective_artifact(root: Path, payload: dict[str, Any]):
    from opaihub.worktree_leases import WorktreeManager

    if not isinstance(payload, dict) or not isinstance(
        payload.get("objective_id"), str
    ):
        raise ValueError("A canonical objective ID is required")
    if payload.get("assignment_id") is not None and (
        not isinstance(payload["assignment_id"], str) or not payload["assignment_id"]
    ):
        raise ValueError("A canonical assignment ID is required")
    objective = ObjectiveStore(root).snapshot(payload["objective_id"])
    if payload.get("assignment_id"):
        item = next(
            (
                row
                for row in objective["assignments"]
                if row["assignment_id"] == payload["assignment_id"]
            ),
            None,
        )
        if item is None:
            raise ValueError("Assignment does not belong to this objective")
        task_id, run_id = item["task_id"], item["run_id"]
    else:
        item = objective["integration"]
        task_id, run_id = objective["task_id"], objective["run_id"] + "-integration"
    if not item.get("worktree"):
        raise ValueError("No worktree has been recorded yet")
    target = Path(item["worktree"]).resolve()
    lease = next(
        (
            row
            for row in WorktreeManager(root).list()
            if row.task_id == task_id
            and row.run_id == run_id
            and Path(row.path).resolve() == target
            and row.branch == item["branch"]
            and (
                not payload.get("assignment_id") or row.lease_id == item.get("lease_id")
            )
        ),
        None,
    )
    if lease is None or not target.is_dir():
        raise ValueError("The recorded worktree is no longer available")
    info = target.stat()
    if lease.filesystem_id and tuple(lease.filesystem_id) != (info.st_dev, info.st_ino):
        raise ValueError("The recorded worktree was replaced")
    # A retained directory and lease alone do not prove that Git still belongs
    # to this objective (the .git marker or checked-out branch can change).
    common = Path(
        _artifact_git(
            target, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).strip()
    ).resolve()
    branch = _artifact_git(target, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    root_common = Path(
        _artifact_git(
            root, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).strip()
    ).resolve()
    if (
        common != root_common
        or common != Path(lease.common_git_dir).resolve()
        or branch != lease.branch
    ):
        raise ValueError("The recorded worktree Git identity changed")
    return objective, item, target, lease


def objective_worktree_path(root: Path, payload: dict[str, Any]) -> Path:
    return _objective_artifact(root, payload)[2]


def _artifact_command(root: Path, command: list[str], *, limit=262144):
    """Fixed read-only argv, with bounded output and execution time."""
    from opaihub.process_tree import adopt, isolated_group_kwargs, terminate_tree

    if command[0] == "git":
        # Even read-only Git commands can invoke a configured fsmonitor hook.
        # Artifact inspection must never execute repository-provided helpers.
        command = ["git", "-c", "core.fsmonitor=false", *command[1:]]
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1")
    with subprocess.Popen(  # nosec B603
        command,
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        **isolated_group_kwargs(),
    ) as process:
        adopt(process)
        timer = threading.Timer(15, terminate_tree, args=(process,))
        timer.start()
        try:
            output = process.stdout.read(limit + 1)
            truncated = len(output) > limit
            if truncated:
                terminate_tree(process)
            code = process.wait()
            if code and not truncated:
                raise ValueError(
                    "Artifact inspection failed or timed out; the recorded evidence may no longer be available"
                )
            return output[:limit].decode("utf-8", errors="replace"), truncated
        finally:
            timer.cancel()
            timer.join()
            terminate_tree(process)


def _artifact_git(root: Path, *args: str) -> str:
    output, truncated = _artifact_command(
        root, ["git", "--no-pager", *args], limit=16384
    )
    if truncated:
        raise ValueError("Git identity exceeds the inspection limit")
    return output


def _artifact_changes(root: Path, base: str) -> dict:
    """A bounded, inert observation matching the executor's canonical evidence."""
    tracked = _artifact_git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-only",
        "--no-renames",
        "-z",
        base,
        "--",
    )
    untracked = _artifact_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    paths = sorted(
        set(filter(None, tracked.split("\0")))
        | {p for p in untracked.split("\0") if p and not p.startswith(".opaihub/")}
    )
    remaining = 16 * 1024 * 1024
    files = {}
    for relative in paths:
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Changed path escapes worktree or is a symlink")
        if not path.exists():
            files[relative] = {
                "deleted": True,
                "digest": "deleted",
                "executable": False,
            }
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Changed path is not a regular file")
        if info.st_size > remaining:
            raise ValueError(
                "Worker content exceeds the inspection limit; open the retained worktree"
            )
        with path.open("rb") as stream:
            data = stream.read(remaining + 1)
        remaining -= len(data)
        if remaining < 0:
            raise ValueError(
                "Worker content exceeds the inspection limit; open the retained worktree"
            )
        files[relative] = {
            "deleted": False,
            "digest": hashlib.sha256(data).hexdigest(),
            "executable": bool(info.st_mode & stat.S_IXUSR),
        }
    return {
        "base_sha": base,
        "head_sha": _artifact_git(root, "rev-parse", "HEAD").strip(),
        "changed_files": paths,
        "files": files,
    }


def inspect_objective_artifact(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Inspect canonical evidence; callers cannot supply paths, commands or URLs."""
    if not isinstance(payload, dict) or set(payload) - {
        "objective_id",
        "assignment_id",
        "kind",
    }:
        raise ValueError(
            "Artifact inspection accepts canonical IDs and an artifact kind only"
        )
    if payload.get("kind") not in {"diff", "pr"}:
        raise ValueError("Choose a diff or pull request")
    objective, item, target, lease = _objective_artifact(root, payload)
    result = item.get("result") or {}
    evidence = (
        result.get("git_evidence", {}) if payload.get("assignment_id") else result
    )
    base, head = evidence.get("base_sha"), evidence.get("head_sha")
    if not all(
        isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40,64}", sha)
        for sha in (base, head)
    ):
        raise ValueError("No canonical base and head have been recorded yet")
    canonical_base = (
        item.get("base_sha") if payload.get("assignment_id") else lease.base_sha
    )
    if (
        base != canonical_base
        or _artifact_git(target, "rev-parse", "HEAD").strip() != head
    ):
        raise ValueError("The worktree no longer matches the recorded base and head")
    response = {
        "ok": True,
        "kind": payload["kind"],
        "objective_id": objective["objective_id"],
        "assignment_id": payload.get("assignment_id"),
        "base_sha": base,
        "head_sha": head,
        "workspaceRoot": str(root.resolve()),
    }
    if payload["kind"] == "diff":
        # Workers commonly leave uncommitted changes. Compare their observed
        # filesystem evidence before and after reading, so stale changes are
        # never displayed as the recorded result.
        worker = bool(payload.get("assignment_id"))
        if worker and _artifact_changes(target, base) != evidence:
            raise ValueError(
                "Worker files changed after the recorded result; inspect the retained worktree"
            )
        command = [
            "git",
            "--no-pager",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--no-color",
            base,
        ]
        if not worker:
            command.append(head)
        output, truncated = _artifact_command(target, [*command, "--"])
        # Untracked additions have no git diff entry. Include a bounded inert
        # preview from the already-validated observed file inventory.
        if worker and not truncated:
            untracked = set(
                _artifact_git(
                    target, "ls-files", "--others", "--exclude-standard", "-z"
                ).split("\0")
            )
            for relative in evidence.get("changed_files", []):
                if relative not in untracked:
                    continue
                path = target / relative
                if (
                    path.is_symlink()
                    or not path.resolve().is_relative_to(target)
                    or not path.is_file()
                ):
                    raise ValueError(
                        "Untracked path is no longer a regular worktree file"
                    )
                remaining = 262144 - len(output.encode("utf-8"))
                if remaining <= 0:
                    truncated = True
                    break
                with path.open("rb") as stream:
                    data = stream.read(remaining + 1)
                output += (
                    "\nUntracked addition: "
                    + relative
                    + "\n"
                    + data[:remaining].decode("utf-8", errors="replace")
                )
                truncated = len(data) > remaining
                if truncated:
                    break
        if worker and _artifact_changes(target, base) != evidence:
            raise ValueError(
                "Worker files changed during inspection; retry after work stops"
            )
        _, current, _, _ = _objective_artifact(root, payload)
        if (
            current.get("result") != result
            or _artifact_git(target, "rev-parse", "HEAD").strip() != head
        ):
            raise ValueError("Recorded evidence changed during inspection")
        return {
            **response,
            "text": output[:262144],
            "truncated": truncated,
            "summary": "Recorded worker changes"
            if worker
            else "Recorded integration commit diff",
        }
    remote = _artifact_git(root, "remote", "get-url", "origin").strip()
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?",
        remote,
    )
    if not match:
        raise ValueError("The canonical origin is not a supported GitHub repository")
    repo = match[1]
    output, truncated = _artifact_command(
        target,
        [
            "gh",
            "pr",
            "view",
            lease.branch,
            "--repo",
            repo,
            "--json",
            "number,title,state,url,headRefName,headRefOid,baseRefName",
        ],
        limit=32768,
    )
    if truncated:
        raise ValueError("Pull request evidence exceeds the inspection limit")
    pr = json.loads(output)
    if (
        not isinstance(pr, dict)
        or pr.get("headRefName") != lease.branch
        or pr.get("headRefOid") != head
        or not re.fullmatch(
            r"https://github\.com/" + re.escape(repo) + r"/pull/[1-9][0-9]*",
            str(pr.get("url", "")),
        )
    ):
        raise ValueError(
            "No existing pull request matches this canonical branch and head"
        )
    _, current, _, _ = _objective_artifact(root, payload)
    if (
        current.get("result") != result
        or _artifact_git(target, "rev-parse", "HEAD").strip() != head
    ):
        raise ValueError("The worktree changed during pull request inspection")
    return {
        **response,
        "pull_request": pr,
        "text": json.dumps(pr, indent=2),
        "summary": "Existing GitHub pull request",
        "truncated": False,
    }
