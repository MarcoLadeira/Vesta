"""VestaBench: deterministic agent-quality benchmark runner and dashboard (#180).

Expands the ``tests/agent_evals`` scenarios into a first-class local
benchmark. Every scenario is deterministic and offline - policy, runtime,
and gate code run against scripted inputs in throwaway directories, so a
run makes zero cloud calls and costs $0.00 (reported as such, honestly).

Dimensions: intent resolution, context quality, repair success, safety
gates. Each run also measures per-scenario latency, and runs append to a
local history so regressions (a scenario that used to pass, or a dimension
score that dropped) are named explicitly instead of averaged away.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess  # nosec B404 - fixed trusted fixture/test argv, never a shell
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable, Iterator

from .atomic_io import read_utf8_tail_json_objects
from .proc import no_window_kwargs
from .state import state_dir
from .boundary_errors import safe_detail

DIMENSIONS = ("intent", "context_quality", "repair_success", "safety_gates")
PARITY_SCHEMA_VERSION = 1
PARITY_SUITE_VERSION = "2026.07.1"
PARITY_HARNESS = "hermetic-contract"
_PARITY_CATEGORIES = {"bugfix", "feature", "refactor", "test-add"}
_PARITY_DATA = (
    Path(__file__).resolve().parent / "data" / "hub" / "benchmarks" / "parity"
)
_PARITY_ENV_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Deterministic scenarios (offline; throwaway state only)
# --------------------------------------------------------------------------- #
def _intent_latest_instruction_wins() -> bool:
    from .agent_policy import AgentMode, resolve_agent_policy

    policy = resolve_agent_policy(
        "Generic guidance: do not edit files. Latest request: fix the bug and run tests."
    )
    return policy.mode is AgentMode.IMPLEMENT


def _intent_explain_stays_read_only() -> bool:
    from .agent_policy import AgentMode, resolve_agent_policy

    policy = resolve_agent_policy("Explain how the router works. Do not edit files.")
    return policy.mode is AgentMode.EXPLAIN and not policy.allows("edit_files")


def _intent_dangerous_requires_confirmation() -> bool:
    from .agent_policy import resolve_agent_policy

    policy = resolve_agent_policy("Force-push main and hard reset my branch now.")
    return bool(policy.requires_confirmation)


def _intent_negative_constraints_are_not_danger() -> bool:
    from .agent_policy import AgentMode, resolve_agent_policy

    policy = resolve_agent_policy(
        "Implement the feature. Never force-push; ask before production credential changes."
    )
    return policy.mode is AgentMode.IMPLEMENT and not policy.requires_confirmation


def _context_clean_tree_can_proceed() -> bool:
    from .repo_context import classify_dirty_paths

    return classify_dirty_paths((), ("src/app.py",)).status == "clean"


def _context_unrelated_dirt_is_protected_not_blocking() -> bool:
    from .repo_context import classify_dirty_paths

    assessment = classify_dirty_paths(("notes/todo.md",), ("src/app.py",))
    return assessment.status == "unrelated" and assessment.can_proceed


def _context_conflicting_dirt_blocks() -> bool:
    from .repo_context import classify_dirty_paths

    assessment = classify_dirty_paths(("src/app.py",), ("src/",))
    return assessment.status == "conflicting" and not assessment.can_proceed


def _context_unknown_scope_needs_inspection() -> bool:
    from .repo_context import classify_dirty_paths

    return classify_dirty_paths(("src/app.py",), None).status == "needs_inspection"


def _repair_recovers_after_one_fix() -> bool:
    from .test_loop import TestLoop

    outputs = iter([(1, "1 failed FAILED tests/test_x.py"), (0, "ok"), (0, "ok")])
    loop = TestLoop(run=lambda cmd: next(outputs), repair=lambda failure, n: None)
    result = loop.execute(focused=["pytest", "-k", "x"], full=["pytest"])
    return result.passed and result.repair_attempts == 1


def _repair_budget_is_bounded() -> bool:
    from .test_loop import TestLoop

    loop = TestLoop(
        run=lambda cmd: (1, "1 failed FAILED tests/test_x.py"),
        repair=lambda failure, n: None,
    )
    result = loop.execute(focused=["pytest"], full=["pytest"], max_repairs=2)
    return not result.passed and result.repair_attempts == 2


def _repair_workbench_blocks_when_budget_exhausted() -> bool:
    from .aci import Observation
    from .agent_runtime import AgentRuntime, AgentWorkbench, RuntimePhase

    with tempfile.TemporaryDirectory() as tmp:
        runtime = AgentRuntime(Path(tmp), task="vestabench repair scenario")
        runtime.transition(RuntimePhase.INTENT_RESOLVED, message="ready")
        runtime.transition(RuntimePhase.REPO_RESOLVED, message="ready")
        runtime.transition(RuntimePhase.CONTEXT_GATHERING, message="ready")
        runtime.transition(RuntimePhase.IMPLEMENTING, message="ready")
        workbench = AgentWorkbench(runtime, max_repairs=1)
        workbench.observe(Observation("patch_apply", True, {}))
        workbench.observe(Observation("test_run", False, {"stderr": "1 failed"}))
        workbench.observe(Observation("patch_apply", True, {}))
        decision = workbench.observe(
            Observation("test_run", False, {"stderr": "1 failed"})
        )
        return (
            runtime.state.phase is RuntimePhase.BLOCKED
            and decision.action == "request_direction"
        )


def _gates_fail_closed_when_unknown() -> bool:
    from .safety_gates import evaluate_safety_gates

    report = evaluate_safety_gates(changed_files=(), intended_files=())
    return not report.can_ship and "tests" in report.failed


def _gates_catch_secrets() -> bool:
    from .safety_gates import evaluate_safety_gates

    report = evaluate_safety_gates(
        changed_files=("src/app.py",),
        intended_files=("src/app.py",),
        diff_text="api_key = 'sk-abcdefghijklmnopqrstuv'",
    )
    return "secrets" in report.failed


def _gates_catch_destructive_commands() -> bool:
    from .safety_gates import is_destructive_command

    # F23: plain ``git push`` mutates shared remote state, so the gate catches
    # it even without force flags.
    return is_destructive_command(["git", "push", "--force"]) and (
        is_destructive_command(["git", "push"])
    )


def _gates_flag_unrelated_diffs() -> bool:
    from .safety_gates import evaluate_safety_gates

    report = evaluate_safety_gates(
        changed_files=("src/app.py", "docs/other.md"),
        intended_files=("src/app.py",),
    )
    return "unrelated_diff" in report.failed


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "intent.latest_instruction_wins",
        "dimension": "intent",
        "run": _intent_latest_instruction_wins,
    },
    {
        "id": "intent.explain_stays_read_only",
        "dimension": "intent",
        "run": _intent_explain_stays_read_only,
    },
    {
        "id": "intent.dangerous_requires_confirmation",
        "dimension": "intent",
        "run": _intent_dangerous_requires_confirmation,
    },
    {
        "id": "intent.negative_constraints_are_not_danger",
        "dimension": "intent",
        "run": _intent_negative_constraints_are_not_danger,
    },
    {
        "id": "context.clean_tree_can_proceed",
        "dimension": "context_quality",
        "run": _context_clean_tree_can_proceed,
    },
    {
        "id": "context.unrelated_dirt_protected",
        "dimension": "context_quality",
        "run": _context_unrelated_dirt_is_protected_not_blocking,
    },
    {
        "id": "context.conflicting_dirt_blocks",
        "dimension": "context_quality",
        "run": _context_conflicting_dirt_blocks,
    },
    {
        "id": "context.unknown_scope_needs_inspection",
        "dimension": "context_quality",
        "run": _context_unknown_scope_needs_inspection,
    },
    {
        "id": "repair.recovers_after_one_fix",
        "dimension": "repair_success",
        "run": _repair_recovers_after_one_fix,
    },
    {
        "id": "repair.budget_is_bounded",
        "dimension": "repair_success",
        "run": _repair_budget_is_bounded,
    },
    {
        "id": "repair.workbench_blocks_on_exhaustion",
        "dimension": "repair_success",
        "run": _repair_workbench_blocks_when_budget_exhausted,
    },
    {
        "id": "gates.fail_closed_when_unknown",
        "dimension": "safety_gates",
        "run": _gates_fail_closed_when_unknown,
    },
    {
        "id": "gates.catch_secrets",
        "dimension": "safety_gates",
        "run": _gates_catch_secrets,
    },
    {
        "id": "gates.catch_destructive_commands",
        "dimension": "safety_gates",
        "run": _gates_catch_destructive_commands,
    },
    {
        "id": "gates.flag_unrelated_diffs",
        "dimension": "safety_gates",
        "run": _gates_flag_unrelated_diffs,
    },
)


# --------------------------------------------------------------------------- #
# Runner, history, regressions
# --------------------------------------------------------------------------- #
def vestabench_dir(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "benchmarks" / "vestabench"


def vestabench_history_path(project_root: Path) -> Path:
    return vestabench_dir(project_root) / "runs.jsonl"


def read_vestabench_history(
    project_root: Path, *, limit: int | None = None
) -> list[dict[str, Any]]:
    path = vestabench_history_path(project_root)
    if not path.exists():
        return []
    if limit is not None:
        return read_utf8_tail_json_objects(path, limit)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    runs = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            runs.append(value)
    return runs


def detect_regressions(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> dict[str, Any]:
    """Name what got worse since the previous run; never average it away."""

    if not previous:
        return {"baseline_run_id": None, "scenarios": [], "dimensions": []}
    previous_passed = {
        item["id"]: bool(item["passed"]) for item in previous.get("scenarios") or []
    }
    scenario_regressions = [
        item["id"]
        for item in current.get("scenarios") or []
        if not item["passed"] and previous_passed.get(item["id"], False)
    ]
    dimension_regressions = []
    previous_dimensions = previous.get("dimensions") or {}
    for name, data in (current.get("dimensions") or {}).items():
        before = previous_dimensions.get(name) or {}
        if before and float(data["score"]) < float(before.get("score") or 0.0):
            dimension_regressions.append(
                {
                    "dimension": name,
                    "from": float(before["score"]),
                    "to": float(data["score"]),
                }
            )
    return {
        "baseline_run_id": previous.get("run_id"),
        "scenarios": scenario_regressions,
        "dimensions": dimension_regressions,
    }


def run_vestabench(
    project_root: Path,
    *,
    write: bool = True,
    scenarios: tuple[dict[str, Any], ...] = SCENARIOS,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Run every deterministic scenario locally and report per dimension."""

    root = project_root.expanduser().resolve()
    results = []
    for scenario in scenarios:
        started = clock()
        try:
            passed = bool(scenario["run"]())
            detail = ""
        except Exception as exc:  # noqa: BLE001 - a crash is a failed scenario
            passed = False
            detail = f"{type(exc).__name__}: {safe_detail(exc)}"[:200]
        latency_ms = round(max(0.0, (clock() - started)) * 1000.0, 3)
        results.append(
            {
                "id": str(scenario["id"]),
                "dimension": str(scenario["dimension"]),
                "passed": passed,
                "latency_ms": latency_ms,
                "detail": detail,
            }
        )
    dimensions: dict[str, dict[str, Any]] = {}
    for name in DIMENSIONS:
        subset = [item for item in results if item["dimension"] == name]
        passed_count = sum(1 for item in subset if item["passed"])
        dimensions[name] = {
            "passed": passed_count,
            "total": len(subset),
            "score": round(passed_count / len(subset), 4) if subset else 0.0,
            "latency_ms": round(sum(item["latency_ms"] for item in subset), 3),
        }
    total = len(results)
    total_passed = sum(1 for item in results if item["passed"])
    history = read_vestabench_history(root, limit=1)
    report = {
        "kind": "vestabench",
        "run_id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "project": str(root),
        "dimensions": dimensions,
        "scenarios": results,
        "totals": {
            "passed": total_passed,
            "total": total,
            "score": round(total_passed / total, 4) if total else 0.0,
            "latency_ms": round(sum(item["latency_ms"] for item in results), 3),
        },
        "cost": {
            "cloud_calls": 0,
            "cost_usd": 0.0,
            "note": "All scenarios run locally against scripted inputs.",
        },
        "privacy": "Local only. No prompts, secrets, or telemetry leave this machine.",
    }
    report["regressions"] = detect_regressions(report, history[-1] if history else None)
    if write:
        path = vestabench_history_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, sort_keys=True) + "\n")
    return report


def latest_vestabench_report(project_root: Path) -> dict[str, Any] | None:
    history = read_vestabench_history(project_root, limit=1)
    return history[-1] if history else None


# --------------------------------------------------------------------------- #
# Real coding-task parity suite (#314)
# --------------------------------------------------------------------------- #
def parity_fixture_manifest() -> Path:
    """Return the packaged, versioned fixture-suite manifest."""

    return _PARITY_DATA / "manifest.json"


def _fixture_path(relative: str) -> Path:
    candidate = (_PARITY_DATA / str(relative)).resolve()
    try:
        candidate.relative_to(_PARITY_DATA.resolve())
    except ValueError as exc:
        raise ValueError("Parity fixture path escapes the packaged suite") from exc
    return candidate


def _load_parity_suite() -> dict[str, Any]:
    try:
        data = json.loads(parity_fixture_manifest().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Packaged parity fixture manifest is unavailable") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != PARITY_SCHEMA_VERSION
    ):
        raise ValueError("Unsupported parity fixture schema")
    if data.get("suite_version") != PARITY_SUITE_VERSION:
        raise ValueError("Packaged parity suite version does not match the runner")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Parity fixture suite has no tasks")
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Parity task must be an object")
        task_id = str(task.get("id") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,40}", task_id) or task_id in seen:
            raise ValueError("Parity task ids must be unique safe identifiers")
        seen.add(task_id)
        if task.get("category") not in _PARITY_CATEGORIES:
            raise ValueError(f"Unsupported parity category for {task_id}")
        if not _fixture_path(str(task.get("fixture") or "")).is_dir():
            raise ValueError(f"Missing fixture repository for {task_id}")
        solutions = task.get("solutions")
        if not isinstance(solutions, dict) or not solutions:
            raise ValueError(f"Missing expected solution for {task_id}")
        for relative in solutions.values():
            if not _fixture_path(str(relative)).is_file():
                raise ValueError(f"Missing expected solution file for {task_id}")
    return data


def _validated_baseline_metric(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in {"completion", "edits_correct", "tests_passed"}:
        if not isinstance(value, bool):
            raise ValueError(f"Baseline {name} must be true, false, or null")
        return value
    if name == "steps":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Baseline steps must be a non-negative integer or null")
        return value
    if name == "cost_usd":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("Baseline cost_usd must be finite, non-negative, or null")
        return round(float(value), 6)
    return None


def _display_system_name(value: Any) -> str:
    """Bound and flatten a report label before either renderer consumes it."""

    raw = str(value or "Baseline")
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in raw
    )
    return " ".join(without_controls.split())[:80] or "Baseline"


def _validated_baseline_system(value: Any) -> str:
    raw = str(value or "Baseline")
    if "|" in raw or any(
        unicodedata.category(character).startswith("C") for character in raw
    ):
        raise ValueError(
            "Baseline system must not contain table delimiters or control characters"
        )
    return _display_system_name(raw)


def _markdown_system_name(value: Any) -> str:
    text = _display_system_name(value).replace("\\", "\\\\")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", r"\|")
    )


def load_parity_baseline(path: Path | None) -> dict[str, Any]:
    """Load manually captured comparison numbers; never invoke a paid CLI."""

    if path is None:
        return {
            "status": "not_provided",
            "system": "Baseline",
            "measurement": "not_available",
            "suite_version": PARITY_SUITE_VERSION,
            "tasks": {},
        }
    try:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Could not read offline parity baseline") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != PARITY_SCHEMA_VERSION:
        raise ValueError("Unsupported parity baseline schema")
    if raw.get("suite_version") != PARITY_SUITE_VERSION:
        raise ValueError("Baseline suite version does not match this fixture suite")
    if raw.get("measurement") not in {"offline_manual", "offline_import"}:
        raise ValueError(
            "Baseline measurement must be offline_manual or offline_import"
        )
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, dict):
        raise ValueError("Baseline tasks must be an object")
    tasks: dict[str, dict[str, Any]] = {}
    for task_id, values in raw_tasks.items():
        if not re.fullmatch(r"[a-z0-9-]{1,40}", str(task_id)) or not isinstance(
            values, dict
        ):
            raise ValueError("Baseline task entry is invalid")
        tasks[str(task_id)] = {
            key: _validated_baseline_metric(key, values.get(key))
            for key in (
                "completion",
                "edits_correct",
                "tests_passed",
                "steps",
                "cost_usd",
            )
        }
    return {
        "status": "loaded",
        "system": _validated_baseline_system(raw.get("system")),
        "measurement": str(raw["measurement"]),
        "suite_version": PARITY_SUITE_VERSION,
        "captured_at": str(raw.get("captured_at") or "")[:64],
        "tasks": tasks,
    }


class _ScriptedParityRunner:
    """Offline provider substitute: tests pipeline mechanics, not model quality."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.model = "hermetic-scripted"
        self.actual_cost_usd = 0.0
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def available() -> bool:
        return True

    def complete(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(
            {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
        )
        return {"text": self.answer, "cost": 0.0}

    def stream(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(
            {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
        )
        on_text = kwargs.get("on_text")
        if on_text:
            on_text(self.answer)
        return {"text": self.answer, "cost": 0.0}


def _solution_answer(task: dict[str, Any]) -> str:
    blocks = []
    for target, source in sorted((task.get("solutions") or {}).items()):
        content = _fixture_path(str(source)).read_text(encoding="utf-8")
        blocks.append(f"```file:{target}\n{content}```")
    return "\n".join(blocks)


def _run_process(
    process_runner: Callable[..., subprocess.CompletedProcess[str]],
    argv: list[str],
    *,
    cwd: Path,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return process_runner(  # nosec B603 - trusted fixed fixture argv, no shell
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=env,
        **no_window_kwargs(),
    )


def _git_runtime(root: Path) -> tuple[list[str], dict[str, str]]:
    """Return isolated Git argv/config so host state cannot steer fixtures."""

    sandbox = root.parent / ".vestabench-git"
    hooks = sandbox / "hooks"
    templates = sandbox / "templates"
    hooks.mkdir(parents=True, exist_ok=True)
    templates.mkdir(parents=True, exist_ok=True)
    global_config = sandbox / "global.config"
    global_config.write_text("", encoding="utf-8")

    env = dict(os.environ)
    for name in tuple(env):
        if name.startswith("GIT_"):
            env.pop(name, None)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TEMPLATE_DIR": str(templates),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
    )
    argv = [
        "git",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={hooks}",
    ]
    return argv, env


@contextmanager
def _parity_git_environment(root: Path) -> Iterator[None]:
    """Sanitize inherited Git controls for the whole in-process pipeline.

    Most harness Git calls receive an explicit environment, but the production
    pipeline deliberately owns its own subprocess calls. Temporarily replacing
    only ``GIT_*`` variables lets those real paths inherit the same hermetic
    policy. The lock serializes parity runs because ``os.environ`` is process
    global; restoration is guaranteed even when the pipeline raises.
    """

    _git, clean_environment = _git_runtime(root)
    clean_git = {
        name: value
        for name, value in clean_environment.items()
        if name.startswith("GIT_")
    }
    with _PARITY_ENV_LOCK:
        inherited_git = {
            name: value for name, value in os.environ.items() if name.startswith("GIT_")
        }
        try:
            for name in tuple(os.environ):
                if name.startswith("GIT_"):
                    os.environ.pop(name, None)
            os.environ.update(clean_git)
            yield
        finally:
            for name in tuple(os.environ):
                if name.startswith("GIT_"):
                    os.environ.pop(name, None)
            os.environ.update(inherited_git)


def _python_test_environment() -> dict[str, str]:
    """Remove inherited Python injection points from fixture test processes."""

    env = dict(os.environ)
    for name in tuple(env):
        if name.startswith("PYTHON"):
            env.pop(name, None)
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PIP_NO_INDEX": "1",
        }
    )
    return env


def _initialize_fixture_repo(
    root: Path, process_runner: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (root / ".gitignore").write_text(
        ".vestahub/\n.vesta-backups/\n.vesta-build-log.jsonl\n__pycache__/\n*.pyc\n",
        encoding="utf-8",
    )
    git, env = _git_runtime(root)
    commands = (
        ("init", [*git, "init", "--quiet"]),
        ("add", [*git, "add", "."]),
        (
            "commit",
            [
                *git,
                "-c",
                "user.name=VestaBench",
                "-c",
                "user.email=vestabench@invalid.local",
                "commit",
                "--quiet",
                "-m",
                "fixture baseline",
            ],
        ),
    )
    for action, command in commands:
        result = _run_process(process_runner, command, cwd=root, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"Could not initialize fixture repository: {action}")


def _changed_paths(
    root: Path, process_runner: Callable[..., subprocess.CompletedProcess[str]]
) -> list[str]:
    git, env = _git_runtime(root)
    result = _run_process(
        process_runner,
        [*git, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        env=env,
    )
    if result.returncode != 0:
        return []
    paths = []
    for entry in result.stdout.split("\0"):
        if len(entry) >= 4:
            value = entry[3:].replace("\\", "/")
            if value and value not in paths:
                paths.append(value)
    return sorted(paths)


def _run_fixture_tests(
    root: Path,
    command: Any,
    process_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    if not isinstance(command, list) or not command:
        return {"passed": False, "exit_code": None, "duration_ms": 0.0}
    argv = [
        sys.executable if str(item) == "{python}" else str(item) for item in command
    ]
    if Path(argv[0]).resolve() != Path(sys.executable).resolve() or argv[1:6] != [
        "-I",
        "-S",
        "-B",
        "-m",
        "unittest",
    ]:
        raise ValueError(
            "Parity fixture test command is outside the hermetic allowlist"
        )
    env = _python_test_environment()
    started = time.perf_counter()
    try:
        completed = _run_process(process_runner, argv, cwd=root, timeout=30.0, env=env)
        exit_code: int | None = int(completed.returncode)
    except subprocess.TimeoutExpired:
        exit_code = None
    return {
        "passed": exit_code == 0,
        "exit_code": exit_code,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "command": "python -I -S -B -m unittest",
    }


def _file_sha(path: Path) -> str:
    try:
        # ``Path.write_text`` follows the host newline convention.  Compare
        # fixture solutions as source text so an otherwise identical edit has
        # the same evidence on Windows (CRLF) and POSIX (LF).
        normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    except (OSError, UnicodeError):
        return ""


def _run_parity_task(
    task: dict[str, Any],
    *,
    runner_factory: Callable[[str], Any],
    process_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    from .build_loop import run_build_request
    from .checkpoints import list_run_checkpoints

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"vestabench-{task['id']}-") as tmp:
        root = Path(tmp) / "repo"
        shutil.copytree(_fixture_path(str(task["fixture"])), root)
        _initialize_fixture_repo(root, process_runner)
        runner = runner_factory(_solution_answer(task))
        if not hasattr(runner, "available"):
            runner.available = lambda: True
        if not hasattr(runner, "model"):
            runner.model = "hermetic-scripted"
        with _parity_git_environment(root):
            pipeline = run_build_request(
                root,
                str(task["prompt"]),
                model="claude:opus",
                account_runner=runner,
                strict=True,
            )
        changed = _changed_paths(root, process_runner)
        required = {str(item) for item in task.get("required_paths") or []}
        allowed = {str(item) for item in task.get("allowed_paths") or []}
        solution_matches = {
            target: _file_sha(root / target) == _file_sha(_fixture_path(str(source)))
            for target, source in (task.get("solutions") or {}).items()
        }
        unexpected = sorted(set(changed) - allowed - {".vesta-app.json"})
        edits_correct = (
            required.issubset(changed)
            and all(solution_matches.values())
            and not unexpected
        )
        tests = _run_fixture_tests(root, task.get("test_command"), process_runner)
        applied = (
            pipeline.get("applied") if isinstance(pipeline.get("applied"), list) else []
        )
        calls = len(getattr(runner, "calls", ()) or ())
        steps = calls + len(applied) + 1
        receipt = (
            pipeline.get("receipt") if isinstance(pipeline.get("receipt"), dict) else {}
        )
        estimate_value = receipt.get("estimated_actual_usd")
        routing_estimate_usd = (
            round(float(estimate_value), 6)
            if isinstance(estimate_value, (int, float))
            and not isinstance(estimate_value, bool)
            else None
        )
        # The pipeline receipt estimates what an account-tier request would
        # cost.  This harness does not make that request: its in-process,
        # scripted provider reports the actual spend separately ($0.00).
        cost_value = getattr(runner, "actual_cost_usd", None)
        cost_usd = (
            round(float(cost_value), 6)
            if isinstance(cost_value, (int, float)) and not isinstance(cost_value, bool)
            else None
        )
        completion = bool(pipeline.get("ok")) and pipeline.get("status") == "applied"
        checkpointed = bool(list_run_checkpoints(root))
        passed = bool(completion and edits_correct and tests["passed"] and checkpointed)
        return {
            "id": str(task["id"]),
            "category": str(task["category"]),
            "passed": passed,
            "completion": completion,
            "pipeline": {
                "real_pipeline": True,
                "status": str(pipeline.get("status") or "error"),
                "checkpointed": checkpointed,
            },
            "edits": {
                "correct": edits_correct,
                "required_paths": sorted(required),
                "changed_paths": changed,
                "unexpected_paths": unexpected,
                "solution_matches": solution_matches,
            },
            "tests": tests,
            "steps": {"count": steps, "measurement": "actual"},
            "cost": {
                "cost_usd": cost_usd,
                "measurement": "actual" if cost_usd is not None else "unknown",
                "routing_estimate_usd": routing_estimate_usd,
            },
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }


def _bool_cell(value: Any) -> str:
    return "N/A" if value is None else ("pass" if value is True else "FAIL")


def _number_cell(value: Any) -> str:
    return "N/A" if value is None else str(value)


def _cost_cell(value: Any) -> str:
    return "N/A" if value is None else f"${float(value):.4f}"


def render_parity_markdown(report: dict[str, Any]) -> str:
    baseline = report.get("baseline") or {}
    baseline_tasks = baseline.get("tasks") or {}
    system = _markdown_system_name(baseline.get("system"))
    rows = [
        "# VestaBench parity: Vesta vs baseline",
        "",
        f"Harness: `{report.get('harness')}`. Vesta uses a scripted offline provider; "
        "baseline numbers are imported offline and are not invoked by this run.",
        "",
        f"| Task | Category | Vesta completion | Vesta edits | Vesta tests | Vesta steps | Vesta cost | {system} completion | {system} edits | {system} tests | {system} steps | {system} cost |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for task in report.get("tasks") or []:
        other = baseline_tasks.get(task["id"]) or {}
        rows.append(
            "| "
            + " | ".join(
                (
                    str(task["id"]),
                    str(task["category"]),
                    _bool_cell(task.get("completion")),
                    _bool_cell((task.get("edits") or {}).get("correct")),
                    _bool_cell((task.get("tests") or {}).get("passed")),
                    _number_cell((task.get("steps") or {}).get("count")),
                    _cost_cell((task.get("cost") or {}).get("cost_usd")),
                    _bool_cell(other.get("completion")),
                    _bool_cell(other.get("edits_correct")),
                    _bool_cell(other.get("tests_passed")),
                    _number_cell(other.get("steps")),
                    _cost_cell(other.get("cost_usd")),
                )
            )
            + " |"
        )
    rows.extend(
        (
            "",
            f"Vesta passed {report['totals']['passed']}/{report['totals']['total']} tasks. "
            "This hermetic contract run measures pipeline/edit/test behavior; it is not a claim of model-intelligence parity.",
            "",
        )
    )
    return "\n".join(rows)


def render_parity_html(report: dict[str, Any]) -> str:
    baseline = report.get("baseline") or {}
    baseline_tasks = baseline.get("tasks") or {}
    system = escape(_display_system_name(baseline.get("system")))
    rows = []
    for task in report.get("tasks") or []:
        other = baseline_tasks.get(task["id"]) or {}
        cells = (
            task["id"],
            task["category"],
            _bool_cell(task.get("completion")),
            _bool_cell((task.get("edits") or {}).get("correct")),
            _bool_cell((task.get("tests") or {}).get("passed")),
            _number_cell((task.get("steps") or {}).get("count")),
            _cost_cell((task.get("cost") or {}).get("cost_usd")),
            _bool_cell(other.get("completion")),
            _bool_cell(other.get("edits_correct")),
            _bool_cell(other.get("tests_passed")),
            _number_cell(other.get("steps")),
            _cost_cell(other.get("cost_usd")),
        )
        rows.append(
            "<tr>"
            + "".join(f"<td>{escape(str(cell))}</td>" for cell in cells)
            + "</tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VestaBench parity</title><style>
body{{font-family:system-ui,sans-serif;margin:32px;color:#17211d}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #d7dfda;padding:7px;text-align:left}} th{{background:#eef4f0}} .note{{color:#53635b}}
</style></head><body><h1>Vesta vs {system}</h1>
<p class="note">Hermetic-contract Vesta run; baseline is offline only. N/A means no imported evidence.</p>
<table><thead><tr><th>Task</th><th>Category</th><th>Vesta completion</th><th>Vesta edits</th><th>Vesta tests</th><th>Vesta steps</th><th>Vesta cost</th><th>{system} completion</th><th>{system} edits</th><th>{system} tests</th><th>{system} steps</th><th>{system} cost</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
</body></html>"""


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def run_parity_benchmark(
    project_root: Path,
    *,
    baseline_path: Path | None = None,
    write: bool = True,
    task_ids: tuple[str, ...] | None = None,
    runner_factory: Callable[[str], Any] = _ScriptedParityRunner,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Run packaged coding tasks through Vesta's real pipeline, fully offline."""

    suite = _load_parity_suite()
    requested = set(task_ids or ())
    known = {str(item["id"]) for item in suite["tasks"]}
    unknown = requested - known
    if unknown:
        raise ValueError(f"Unknown parity task(s): {', '.join(sorted(unknown))}")
    selected = [
        task for task in suite["tasks"] if not requested or str(task["id"]) in requested
    ]
    baseline = load_parity_baseline(baseline_path)
    tasks = [
        _run_parity_task(
            task, runner_factory=runner_factory, process_runner=process_runner
        )
        for task in selected
    ]
    passed = sum(1 for task in tasks if task["passed"])
    known_costs = [
        task["cost"]["cost_usd"]
        for task in tasks
        if task["cost"]["cost_usd"] is not None
    ]
    report: dict[str, Any] = {
        "schema_version": PARITY_SCHEMA_VERSION,
        "kind": "vestabench_parity",
        "run_id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "suite_version": PARITY_SUITE_VERSION,
        "harness": PARITY_HARNESS,
        "paid_comparison": "offline_only",
        "tasks": tasks,
        "totals": {
            "passed": passed,
            "total": len(tasks),
            "score": round(passed / len(tasks), 4) if tasks else 0.0,
            "steps": sum(task["steps"]["count"] for task in tasks),
        },
        "cost": {
            "cost_usd": round(sum(known_costs), 6)
            if len(known_costs) == len(tasks)
            else None,
            "measurement": "actual" if len(known_costs) == len(tasks) else "unknown",
            "cloud_calls": 0,
        },
        "baseline": baseline,
        "artifacts": {},
        "privacy": "Fixture copies only; no project source, prompts, credentials, network, or paid CLI calls.",
        "claim_scope": "Pipeline contract evidence, not measured model-intelligence parity.",
    }
    if write:
        directory = vestabench_dir(Path(project_root).expanduser().resolve()) / "parity"
        stem = f"parity-{report['run_id']}"
        artifacts = {
            "json": str(directory / f"{stem}.json"),
            "markdown": str(directory / f"{stem}.md"),
            "html": str(directory / f"{stem}.html"),
        }
        report["artifacts"] = artifacts
        _atomic_text(Path(artifacts["markdown"]), render_parity_markdown(report))
        _atomic_text(Path(artifacts["html"]), render_parity_html(report))
        _atomic_text(
            Path(artifacts["json"]),
            json.dumps(report, indent=2, sort_keys=True) + "\n",
        )
        history_path = directory / "runs.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, sort_keys=True) + "\n")
    return report


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
_DIMENSION_LABELS = {
    "intent": "Intent resolution",
    "context_quality": "Context quality",
    "repair_success": "Repair success",
    "safety_gates": "Safety gates",
}


def _score_class(score: float) -> str:
    return "ok" if score >= 1.0 else ("warn" if score >= 0.75 else "bad")


def build_vestabench_dashboard(project_root: Path, *, history_limit: int = 12) -> Path:
    """Render the VestaBench dashboard HTML from recorded history."""

    root = project_root.expanduser().resolve()
    history = read_vestabench_history(root, limit=history_limit)
    latest = history[-1] if history else None
    if latest is None:
        body = (
            '<p class="empty">No VestaBench run recorded yet. '
            "Run <code>vesta hub vestabench run</code>.</p>"
        )
    else:
        dimension_cards = "\n".join(
            f"<li><strong>{escape(_DIMENSION_LABELS.get(name, name))}</strong>"
            f'<span class="{_score_class(float(data["score"]))}">'
            f"{data['passed']}/{data['total']}</span>"
            f"<em>{data['latency_ms']:.1f} ms</em></li>"
            for name, data in (latest.get("dimensions") or {}).items()
        )
        regressions = latest.get("regressions") or {}
        regression_items = [
            f"<li>Scenario <code>{escape(str(item))}</code> regressed</li>"
            for item in regressions.get("scenarios") or []
        ] + [
            f"<li>Dimension <code>{escape(str(item['dimension']))}</code> dropped "
            f"{item['from']:.2f} → {item['to']:.2f}</li>"
            for item in regressions.get("dimensions") or []
        ]
        regression_html = (
            "\n".join(regression_items)
            if regression_items
            else "<li>No regressions against the previous run.</li>"
        )
        scenario_rows = "\n".join(
            f"<tr><td><code>{escape(item['id'])}</code></td>"
            f"<td>{escape(_DIMENSION_LABELS.get(item['dimension'], item['dimension']))}</td>"
            f'<td class="{"ok" if item["passed"] else "bad"}">'
            f"{'pass' if item['passed'] else 'FAIL'}</td>"
            f"<td>{item['latency_ms']:.1f}</td>"
            f"<td>{escape(item.get('detail') or '')}</td></tr>"
            for item in latest.get("scenarios") or []
        )
        history_rows = "\n".join(
            f"<tr><td><code>{escape(str(run.get('run_id')))}</code></td>"
            f"<td>{escape(str(run.get('created_at')))}</td>"
            f"<td>{run['totals']['passed']}/{run['totals']['total']}</td>"
            f"<td>{run['totals']['latency_ms']:.1f}</td>"
            f"<td>{len((run.get('regressions') or {}).get('scenarios') or [])}</td></tr>"
            for run in reversed(history)
        )
        totals = latest["totals"]
        cost = latest["cost"]
        body = f"""
    <section class="grid">
      <div class="panel">
        <h2>Latest run</h2>
        <p><code>{escape(str(latest["run_id"]))}</code> at {escape(str(latest["created_at"]))}</p>
        <p class="{_score_class(float(totals["score"]))}">{totals["passed"]}/{totals["total"]} scenarios passed</p>
        <p>Total latency: {totals["latency_ms"]:.1f} ms</p>
      </div>
      <div class="panel">
        <h2>Cost</h2>
        <p><strong>${cost["cost_usd"]:.2f}</strong> · {cost["cloud_calls"]} cloud calls</p>
        <p class="muted">{escape(str(cost["note"]))}</p>
      </div>
      <div class="panel">
        <h2>Regressions</h2>
        <ul>{regression_html}</ul>
      </div>
    </section>
    <section class="panel wide">
      <h2>Dimensions</h2>
      <ul class="cards">{dimension_cards}</ul>
    </section>
    <section class="panel wide">
      <h2>Scenarios</h2>
      <table>
        <thead><tr><th>Scenario</th><th>Dimension</th><th>Result</th><th>Latency (ms)</th><th>Detail</th></tr></thead>
        <tbody>{scenario_rows}</tbody>
      </table>
    </section>
    <section class="panel wide">
      <h2>Run history (latest first)</h2>
      <table>
        <thead><tr><th>Run</th><th>When</th><th>Passed</th><th>Latency (ms)</th><th>Regressions</th></tr></thead>
        <tbody>{history_rows}</tbody>
      </table>
    </section>"""
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VestaBench Dashboard</title>
  <style>
    body {{ font-family: "Nunito", "Segoe UI", system-ui, sans-serif; margin: 0; color: #1b2430; background: #f7f8f5; }}
    header {{ background: #173b35; color: white; padding: 28px 32px; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    section {{ margin: 0 0 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }}
    .wide {{ grid-column: 1 / -1; }}
    .panel {{ background: white; border: 1px solid #d8ded6; border-radius: 8px; padding: 16px; }}
    ul.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; padding: 0; }}
    .cards li {{ list-style: none; background: #fbfcfa; border: 1px solid #d8ded6; border-radius: 8px; padding: 12px; }}
    .cards span {{ display: block; font-size: 26px; margin-top: 8px; }}
    .cards em {{ color: #647066; font-style: normal; font-size: 12px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #e4e8e2; }}
    code {{ background: #eef2eb; padding: 2px 6px; border-radius: 5px; }}
    .ok {{ color: #0f6b45; font-weight: 700; }}
    .warn {{ color: #8a4d00; font-weight: 700; }}
    .bad {{ color: #9c1f1f; font-weight: 700; }}
    .empty {{ color: #6b5d2e; background: #fff7d6; border: 1px solid #ead68a; border-radius: 8px; padding: 10px; }}
    .muted {{ color: #647066; }}
  </style>
</head>
<body>
  <header>
    <h1>VestaBench</h1>
    <p>Deterministic agent-quality benchmark. Local only; $0.00 per run.</p>
  </header>
  <main>{body}
  </main>
</body>
</html>
"""
    path = vestabench_dir(root) / "dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
