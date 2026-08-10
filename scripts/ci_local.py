"""Run OPai's fail-closed qualification profiles and emit exact-SHA evidence.

`fast` is the aggregate pull-request contract. Hosted jobs use `--component` to
run its Python, hostile-environment, and web portions in parallel. `full`,
`native`, `provider-canary`, and `release` are the corresponding scheduled,
platform, protected-provider, and release-candidate contracts.

Only ``qualified`` exits zero. A required missing tool, timeout, stale candidate,
deleted manifest check, or absent protected prerequisite is typed and exits
non-zero. Command diagnostics are redacted and bounded before they enter JSON.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess  # nosec B404 - fixed argv, never a shell
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
# Direct script execution puts ``scripts/`` first on sys.path, where
# ``scripts/opai.py`` would shadow the real ``opai`` package. Qualification
# imports the canonical redactor, so make the repository package authoritative.
if sys.path[0] != str(ROOT):
    sys.path.insert(0, str(ROOT))
SCHEMA_VERSION = 2
PROFILE_VERSION = 2
MAX_DIAGNOSTIC_CHARS = 4_000
DEFAULT_TIMEOUT_SECONDS = 30 * 60
DEFAULT_MANIFEST = ROOT / ".opaihub" / "ci-evidence" / "local.json"
REQUIRED_CHECKS_MANIFEST = ROOT / ".github" / "required-checks.json"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


@dataclass(frozen=True)
class EnvRequirement:
    """One explicit environment gate; values are never copied into evidence."""

    name: str
    expected: str | None = None
    failure_class: str = "credential"


@dataclass(frozen=True)
class Step:
    """One required or explicitly optional qualification command."""

    name: str
    argv: list[str]
    required_modules: tuple[str, ...] = ()
    required_executables: tuple[str, ...] = ()
    required_env: tuple[EnvRequirement, ...] = ()
    network: bool = False
    required: bool = True
    failure_class: str = "test"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    env: tuple[tuple[str, str], ...] = ()
    infrastructure_patterns: tuple[str, ...] = ()
    infrastructure_returncodes: tuple[int, ...] = ()
    credential_returncodes: tuple[int, ...] = ()


_NETWORK_INFRASTRUCTURE_PATTERNS = (
    "connection refused",
    "connection reset",
    "could not resolve",
    "eai_again",
    "econnrefused",
    "enetwork",
    "enetunreach",
    "name resolution",
    "network is unreachable",
    "service unavailable",
    "temporary failure",
)

PYTHON_STEPS = (
    Step(
        "ruff-format",
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        required_modules=("ruff",),
        failure_class="policy",
    ),
    Step(
        "ruff-lint",
        [sys.executable, "-m", "ruff", "check", "."],
        required_modules=("ruff",),
        failure_class="policy",
    ),
    Step(
        "lifecycle-projection-drift",
        [sys.executable, "scripts/generate_lifecycle.py", "--check"],
        failure_class="policy",
    ),
    Step(
        "lifecycle-test-collection",
        [sys.executable, "scripts/check_test_collection.py"],
        failure_class="policy",
    ),
    Step(
        "python-unittest",
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        failure_class="test",
    ),
    # `unittest discover` collects 1,605 of the suite's 4,040 tests: 139 of 237
    # files are bare pytest functions it cannot see. Before #621 added a pytest
    # step those 2,435 tests had never run in CI at all -- which is how
    # test_run_status_adoption.py shipped an assertion that never executed.
    #
    # That step lives in the *hostile* lane, so today the only run of 60% of
    # the suite happens with fake provider credentials and a hostile keyring
    # injected. Normal-environment coverage of those tests is what this step
    # restores; hostile then means what it should -- an additional adversarial
    # pass, not the only pass. `lifecycle-test-collection` above fails if this
    # step is ever removed.
    Step(
        "python-pytest",
        [sys.executable, "-m", "pytest", "tests", "-q"],
        required_modules=("pytest",),
        failure_class="test",
    ),
    Step(
        "registry-validation",
        [sys.executable, "-m", "opaihub", "validate"],
        required_modules=("opaihub",),
        failure_class="policy",
    ),
    Step(
        "bandit",
        [
            sys.executable,
            "-m",
            "bandit",
            "-r",
            "opai",
            "opaihub",
            "opcoding",
            "scripts",
            "-q",
        ],
        required_modules=("bandit",),
        failure_class="security",
    ),
    Step(
        "detect-secrets",
        [
            sys.executable,
            "scripts/check_secrets.py",
            "--baseline",
            ".secrets.baseline",
        ],
        required_modules=("detect_secrets",),
        failure_class="security",
        infrastructure_returncodes=(3,),
    ),
)

_HOSTILE_ENV = (
    ("PYTHONPATH", "tests/fixtures/hostile_keyring"),
    ("MOONSHOT_API_KEY", "hostile-moonshot"),
    ("GOOGLE_API_KEY", "hostile-google"),
    ("GROQ_API_KEY", "hostile-groq"),
    ("MISTRAL_API_KEY", "hostile-mistral"),
)

HOSTILE_STEPS = (
    Step(
        "hostile-unittest",
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        env=_HOSTILE_ENV,
    ),
    Step(
        "hostile-pytest",
        [sys.executable, "-m", "pytest", "tests", "-q"],
        required_modules=("pytest",),
        env=_HOSTILE_ENV,
    ),
)

WEB_STEPS = (
    Step(
        "npm-ci",
        ["npm", "ci"],
        required_executables=("node", "npm"),
        network=True,
        failure_class="build",
        infrastructure_patterns=_NETWORK_INFRASTRUCTURE_PATTERNS,
    ),
    Step(
        "npm-audit",
        ["npm", "audit", "--audit-level=high"],
        required_executables=("node", "npm"),
        network=True,
        failure_class="security",
        infrastructure_patterns=_NETWORK_INFRASTRUCTURE_PATTERNS,
    ),
    Step(
        "web-design-tokens",
        ["npm", "run", "test:tokens"],
        required_executables=("node", "npm"),
        failure_class="policy",
    ),
    Step(
        "web-unit",
        ["npm", "run", "test:unit"],
        required_executables=("node", "npm"),
    ),
    Step(
        "playwright-browser-install",
        ["npx", "playwright", "install", "chromium"],
        required_executables=("node", "npm", "npx"),
        network=True,
        failure_class="infrastructure",
        infrastructure_patterns=_NETWORK_INFRASTRUCTURE_PATTERNS,
    ),
    Step(
        "web-e2e",
        ["npm", "run", "test:e2e", "--", "--workers=2"],
        required_executables=("node", "npm"),
        timeout_seconds=45 * 60,
    ),
)

SUPPLY_CHAIN_STEPS = (
    Step(
        "python-pytest",
        [sys.executable, "-m", "pytest", "tests", "-q"],
        required_modules=("pytest",),
    ),
    Step(
        "pip-audit",
        [
            sys.executable,
            "-m",
            "pip_audit",
            ".",
            "--progress-spinner",
            "off",
            "--skip-editable",
        ],
        required_modules=("pip_audit",),
        network=True,
        failure_class="security",
        infrastructure_patterns=_NETWORK_INFRASTRUCTURE_PATTERNS,
    ),
)

NATIVE_STEPS = (
    Step(
        "isolated-wheel-smoke",
        [sys.executable, "scripts/smoke-install.py"],
        required_modules=("setuptools",),
        network=True,
        failure_class="build",
        infrastructure_patterns=_NETWORK_INFRASTRUCTURE_PATTERNS,
    ),
)

PROVIDER_STEPS = (
    Step(
        "provider-canary",
        [sys.executable, "scripts/run_provider_canary.py"],
        required_env=(
            EnvRequirement("OPAI_LIVE_PROVIDER_SMOKE", "1", "credential"),
            EnvRequirement("OPAI_CONFIRM_CLOUD_TESTS", "YES", "credential"),
            EnvRequirement("OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS", None, "credential"),
            EnvRequirement("OPAI_LIVE_MODELS", None, "credential"),
            EnvRequirement("OPAI_PROVIDER_CANARY_MAX_USD", None, "credential"),
            EnvRequirement("OPAI_PROVIDER_CANARY_NON_PRODUCTION", "YES", "credential"),
        ),
        network=True,
        timeout_seconds=15 * 60,
        infrastructure_patterns=_NETWORK_INFRASTRUCTURE_PATTERNS,
        infrastructure_returncodes=(3,),
        credential_returncodes=(4,),
    ),
)

PROFILE_COMPONENT_STEPS: dict[str, dict[str, tuple[Step, ...]]] = {
    "fast": {"python": PYTHON_STEPS, "hostile": HOSTILE_STEPS, "web": WEB_STEPS},
    "full": {
        "python": PYTHON_STEPS,
        "hostile": HOSTILE_STEPS,
        "web": WEB_STEPS,
        "supply-chain": SUPPLY_CHAIN_STEPS,
    },
    "native": {"native": NATIVE_STEPS},
    "provider-canary": {"provider": PROVIDER_STEPS},
    "release": {
        "python": PYTHON_STEPS,
        "hostile": HOSTILE_STEPS,
        "web": WEB_STEPS,
        "supply-chain": SUPPLY_CHAIN_STEPS,
        "native": NATIVE_STEPS,
        "provider": PROVIDER_STEPS,
    },
}


def _flatten(components: dict[str, tuple[Step, ...]]) -> tuple[Step, ...]:
    return tuple(step for steps in components.values() for step in steps)


PROFILE_STEPS: dict[str, tuple[Step, ...]] = {
    profile: _flatten(components)
    for profile, components in PROFILE_COMPONENT_STEPS.items()
}
_DEFAULT_PROFILE_STEPS = PROFILE_STEPS

_MODULE_DISTRIBUTIONS = {
    "bandit": "bandit",
    "detect_secrets": "detect-secrets",
    "opaihub": "opai",
    "pip_audit": "pip-audit",
    "pytest": "pytest",
    "ruff": "ruff",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_revision() -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [git, "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = completed.stdout.strip()
    return (
        revision
        if completed.returncode == 0 and SHA_PATTERN.fullmatch(revision)
        else None
    )


def _git_workspace_clean() -> bool | None:
    """Return whether tracked and untracked candidate inputs match ``HEAD``."""

    git = shutil.which("git")
    if git is None:
        return None
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [git, "status", "--porcelain=v1", "--untracked-files=normal"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return not completed.stdout.strip()


def _git_parent_shas() -> tuple[str, ...] | None:
    """Return the checked-out commit's direct parent identities."""

    git = shutil.which("git")
    if git is None:
        return None
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [git, "show", "-s", "--format=%P", "HEAD"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    parents = tuple(completed.stdout.strip().split())
    if not all(SHA_PATTERN.fullmatch(parent) for parent in parents):
        return None
    return parents


def _tool_versions(steps: tuple[Step, ...]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {
        "python": sys.version.split()[0],
        "python_executable": str(Path(sys.executable).resolve()),
    }
    for module in sorted(
        {module for step in steps for module in step.required_modules}
    ):
        distribution = _MODULE_DISTRIBUTIONS.get(module, module)
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    for executable in sorted(
        {executable for step in steps for executable in step.required_executables}
    ):
        path = shutil.which(executable)
        if path is None:
            versions[executable] = None
            continue
        try:
            completed = subprocess.run(  # nosec B603 - fixed argv, no shell
                [path, "--version"],
                cwd=str(ROOT),
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            versions[executable] = "unavailable"
        else:
            versions[executable] = (
                completed.stdout.strip() or completed.stderr.strip() or "unknown"
            )[:200]
    return versions


def _missing_modules(step: Step) -> list[str]:
    return [
        module
        for module in step.required_modules
        if importlib.util.find_spec(module) is None
    ]


def _missing_executables(step: Step) -> list[str]:
    return [name for name in step.required_executables if shutil.which(name) is None]


def _launch_argv(step: Step) -> list[str]:
    """Absolute-path the launcher so a Windows ``.CMD``/``.BAT`` shim can start.

    ``shutil.which`` honours ``PATHEXT``, so it resolves ``npm`` to ``npm.CMD``.
    ``CreateProcess`` -- what ``subprocess`` uses without ``shell=True`` -- does
    not: it only ever appends ``.exe``. A bare ``"npm"`` therefore raises
    ``FileNotFoundError`` on Windows even when npm is installed, on PATH and
    working, and this runner would report that as "required executable
    unavailable" -- an infrastructure verdict about a tool that is present.
    Every web check was unqualifiable on Windows for that reason alone.

    Resolving the launcher also removes a PATH ambiguity: evidence then names
    the exact binary that ran, rather than a name re-resolved by the OS.
    """

    argv = list(step.argv)
    if not argv:
        return argv
    resolved = shutil.which(argv[0])
    if resolved:
        argv[0] = resolved
    return argv


def _missing_environment(step: Step) -> list[str]:
    missing: list[str] = []
    for requirement in step.required_env:
        value = os.environ.get(requirement.name)
        if not value or (
            requirement.expected is not None and value != requirement.expected
        ):
            missing.append(requirement.name)
    return missing


def _redact_and_bound(value: str) -> str:
    from opai.provider_contract import redact_secrets

    safe = redact_secrets(value).strip()
    if len(safe) <= MAX_DIAGNOSTIC_CHARS:
        return safe
    marker = "\n...[diagnostic truncated]...\n"
    budget = MAX_DIAGNOSTIC_CHARS - len(marker)
    before = budget // 2
    return safe[:before] + marker + safe[-(budget - before) :]


def _check_record(
    step: Step,
    *,
    execution: str,
    outcome: str,
    duration_seconds: float,
    message: str | None = None,
    returncode: int | None = None,
    failure_class: str | None = None,
    diagnostic: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": step.name,
        "required": step.required,
        "network": step.network,
        "command": step.argv,
        "timeout_seconds": step.timeout_seconds,
        "status": {"execution": execution, "outcome": outcome},
        "duration_seconds": round(duration_seconds, 3),
    }
    if message:
        record["message"] = message
    if returncode is not None:
        record["returncode"] = returncode
    if failure_class is not None:
        record["failure_class"] = failure_class
    if diagnostic:
        record["diagnostic"] = diagnostic
    return record


def _unavailable_record(
    step: Step, message: str, *, failure_class: str = "infrastructure"
) -> dict[str, Any]:
    print(f"  ! {step.name}: {message}", flush=True)
    return _check_record(
        step,
        execution="unavailable",
        outcome="failed" if step.required else "skipped",
        duration_seconds=0.0,
        message=message,
        failure_class=failure_class,
    )


def _run(step: Step) -> dict[str, Any]:
    """Execute one check without ever converting required absence into success."""

    missing_modules = _missing_modules(step)
    if missing_modules:
        return _unavailable_record(
            step, f"required module unavailable: {', '.join(missing_modules)}"
        )
    missing_executables = _missing_executables(step)
    if missing_executables:
        return _unavailable_record(
            step, f"required executable unavailable: {', '.join(missing_executables)}"
        )
    missing_environment = _missing_environment(step)
    if missing_environment:
        return _unavailable_record(
            step,
            "protected prerequisite unavailable: " + ", ".join(missing_environment),
            failure_class="credential",
        )

    child_environment = os.environ.copy()
    child_environment.update(dict(step.env))
    start = time.monotonic()
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            _launch_argv(step),
            cwd=str(ROOT),
            env=child_environment,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=step.timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        duration = time.monotonic() - start
        diagnostic = _redact_and_bound(
            "\n".join(
                part.decode(errors="replace")
                if isinstance(part, bytes)
                else str(part or "")
                for part in (error.stdout, error.stderr)
            )
        )
        return _check_record(
            step,
            execution="timed_out",
            outcome="failed",
            duration_seconds=duration,
            message=f"required check exceeded {step.timeout_seconds:g}s timeout",
            failure_class="infrastructure",
            diagnostic=diagnostic,
        )
    except FileNotFoundError:
        return _unavailable_record(
            step, f"required executable unavailable: {step.argv[0]}"
        )
    except OSError as error:
        return _unavailable_record(
            step, f"infrastructure error starting check: {error}"
        )

    duration = time.monotonic() - start
    diagnostic = _redact_and_bound(
        "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    )
    if completed.returncode == 0:
        return _check_record(
            step,
            execution="executed",
            outcome="passed",
            duration_seconds=duration,
            returncode=0,
        )

    failure_class = step.failure_class
    lowered = diagnostic.lower()
    if completed.returncode in step.credential_returncodes:
        failure_class = "credential"
    elif completed.returncode in step.infrastructure_returncodes or any(
        pattern.lower() in lowered for pattern in step.infrastructure_patterns
    ):
        failure_class = "infrastructure"
    return _check_record(
        step,
        execution="executed",
        outcome="failed",
        duration_seconds=duration,
        returncode=completed.returncode,
        failure_class=failure_class,
        diagnostic=diagnostic,
    )


def _meta_record(
    check_id: str,
    *,
    execution: str,
    outcome: str,
    message: str,
    failure_class: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": check_id,
        "required": True,
        "network": False,
        "command": [],
        "status": {"execution": execution, "outcome": outcome},
        "duration_seconds": 0.0,
        "message": message,
    }
    if failure_class:
        value["failure_class"] = failure_class
    return value


def _candidate_record(
    candidate_sha: str | None,
    revision: str | None,
    workspace_clean: bool | None,
    source_sha: str | None,
    parent_shas: tuple[str, ...] | None,
    *,
    explicit_required: bool,
) -> tuple[dict[str, Any], str | None]:
    if candidate_sha is None and explicit_required:
        return (
            _meta_record(
                "candidate-sha",
                execution="unavailable",
                outcome="failed",
                message="CI qualification requires an explicit --candidate-sha",
                failure_class="infrastructure",
            ),
            None,
        )
    expected = candidate_sha or revision
    if expected is None or not SHA_PATTERN.fullmatch(expected):
        return (
            _meta_record(
                "candidate-sha",
                execution="unavailable",
                outcome="failed",
                message="could not determine a valid immutable candidate SHA",
                failure_class="infrastructure",
            ),
            expected,
        )
    if revision is None:
        return (
            _meta_record(
                "candidate-sha",
                execution="unavailable",
                outcome="failed",
                message="could not determine the checked-out commit SHA",
                failure_class="infrastructure",
            ),
            expected,
        )
    if expected.lower() != revision.lower():
        return (
            _meta_record(
                "candidate-sha",
                execution="executed",
                outcome="failed",
                message=f"candidate SHA {expected} does not match checked-out SHA {revision}",
                failure_class="infrastructure",
            ),
            expected,
        )
    if candidate_sha is not None and workspace_clean is not True:
        message = (
            "could not verify that candidate workspace matches HEAD"
            if workspace_clean is None
            else "explicit candidate workspace has tracked or untracked changes"
        )
        return (
            _meta_record(
                "candidate-sha",
                execution="unavailable" if workspace_clean is None else "executed",
                outcome="failed",
                message=message,
                failure_class="infrastructure",
            ),
            expected,
        )
    source = source_sha or expected
    if SHA_PATTERN.fullmatch(source) is None:
        return (
            _meta_record(
                "candidate-sha",
                execution="unavailable",
                outcome="failed",
                message="source SHA is not a valid immutable commit identity",
                failure_class="infrastructure",
            ),
            expected,
        )
    if source.lower() != expected.lower():
        if parent_shas is None:
            return (
                _meta_record(
                    "candidate-sha",
                    execution="unavailable",
                    outcome="failed",
                    message="could not verify source SHA against candidate parents",
                    failure_class="infrastructure",
                ),
                expected,
            )
        if source.lower() not in {parent.lower() for parent in parent_shas}:
            return (
                _meta_record(
                    "candidate-sha",
                    execution="executed",
                    outcome="failed",
                    message="source SHA is not a direct parent of the tested merge candidate",
                    failure_class="infrastructure",
                ),
                expected,
            )
    source = "explicit" if candidate_sha else "inferred-local"
    return (
        _meta_record(
            "candidate-sha",
            execution="executed",
            outcome="passed",
            message=f"{source} candidate SHA matches checked-out SHA",
        ),
        expected,
    )


def _required_checks_manifest() -> dict[str, Any]:
    try:
        value = json.loads(REQUIRED_CHECKS_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"required-check manifest is unreadable: {error}") from error
    if value.get("schema_version") != 2 or not isinstance(
        value.get("required_checks"), list
    ):
        raise ValueError("required-check manifest schema_version 2 is required")
    return value


def _expected_check_ids(profile: str, component: str | None = None) -> list[str]:
    """Return the immutable declared inventory for one invocation."""

    manifest = _required_checks_manifest()
    raw_contracts = manifest.get("component_contracts")
    if not isinstance(raw_contracts, dict):
        raise ValueError("required-check manifest has no component_contracts")
    by_component: dict[str, list[str]] = {}
    for component_name, raw_ids in raw_contracts.items():
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError(f"component {component_name!r} has no required check IDs")
        values = [str(value).strip() for value in raw_ids]
        if any(not value for value in values) or len(values) != len(set(values)):
            raise ValueError(f"component {component_name!r} check IDs are invalid")
        by_component[str(component_name)] = values
    if profile == "fast":
        components = [component] if component else ["python", "hostile", "web"]
    elif profile == "full":
        components = (
            [component]
            if component
            else [
                "python",
                "hostile",
                "web",
                "supply-chain",
            ]
        )
    elif profile == "native":
        components = ["native"]
    elif profile == "provider-canary":
        components = ["provider"]
    else:
        components = (
            [component] if component else list(PROFILE_COMPONENT_STEPS[profile])
        )

    expected: list[str] = ["candidate-sha"]
    for selected in components:
        if selected not in by_component:
            raise ValueError(f"component {selected!r} has no immutable check contract")
        expected.extend(by_component[selected])
    return list(dict.fromkeys(expected))


def _inventory_record(expected: list[str], actual: list[str]) -> dict[str, Any]:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing declared checks: " + ", ".join(missing))
        if extra:
            details.append("undeclared checks: " + ", ".join(extra))
        return _meta_record(
            "check-inventory",
            execution="executed",
            outcome="failed",
            message="; ".join(details),
            failure_class="infrastructure",
        )
    return _meta_record(
        "check-inventory",
        execution="executed",
        outcome="passed",
        message="executed check inventory matches the required manifest",
    )


def _write_manifest(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as temporary_file:
        json.dump(evidence, temporary_file, indent=2, sort_keys=True)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    temporary_path.replace(path)


def _verdict(checks: list[dict[str, Any]]) -> tuple[str, str, str]:
    failures = [
        check
        for check in checks
        if check["required"] and check["status"]["outcome"] == "failed"
    ]
    if not failures:
        return "qualified", "all_required_checks_passed", "none"

    candidate = next(
        (check for check in failures if check["id"] == "candidate-sha"), None
    )
    if candidate is not None:
        if "explicit --candidate-sha" in candidate.get("message", ""):
            return "infrastructure_blocked", "candidate_sha_missing", "infrastructure"
        if "could not verify" in candidate.get("message", ""):
            if "source SHA" in candidate.get("message", ""):
                return (
                    "infrastructure_blocked",
                    "candidate_source_unverified",
                    "infrastructure",
                )
            return (
                "infrastructure_blocked",
                "candidate_workspace_unverified",
                "infrastructure",
            )
        if "workspace" in candidate.get("message", ""):
            return (
                "infrastructure_blocked",
                "candidate_workspace_dirty",
                "infrastructure",
            )
        if "source SHA" in candidate.get("message", ""):
            return (
                "infrastructure_blocked",
                "candidate_source_mismatch",
                "infrastructure",
            )
        return "infrastructure_blocked", "candidate_sha_mismatch", "infrastructure"
    if any(check["id"] == "check-inventory" for check in failures):
        return "infrastructure_blocked", "check_inventory_mismatch", "infrastructure"
    if any(check["status"]["execution"] == "timed_out" for check in failures):
        return "infrastructure_blocked", "required_check_timeout", "infrastructure"
    if any(check.get("failure_class") == "credential" for check in failures):
        return (
            "credential_unavailable",
            "protected_prerequisite_unavailable",
            "credential",
        )
    if any(
        check["status"]["execution"] == "unavailable"
        or check.get("failure_class") == "infrastructure"
        for check in failures
    ):
        return "infrastructure_blocked", "required_check_unavailable", "infrastructure"
    for classification, verdict in (
        ("security", "security_failed"),
        ("build", "build_failed"),
        ("policy", "policy_failed"),
        ("test", "test_failed"),
    ):
        if any(check.get("failure_class") == classification for check in failures):
            return verdict, f"required_{classification}_failed", classification
    return "test_failed", "required_test_failed", "test"


def _select_steps(profile: str, component: str | None) -> tuple[Step, ...]:
    if component is None:
        return PROFILE_STEPS[profile]
    try:
        return PROFILE_COMPONENT_STEPS[profile][component]
    except KeyError as error:
        choices = ", ".join(PROFILE_COMPONENT_STEPS[profile])
        raise ValueError(
            f"component {component!r} is not part of {profile!r}; choose: {choices}"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a fail-closed OPai qualification profile."
    )
    parser.add_argument("--profile", choices=sorted(PROFILE_STEPS), default="fast")
    parser.add_argument("--component", help="Run one declared component of a profile")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--candidate-sha",
        help="Exact checked-out commit. Required when CI is set; inferred locally.",
    )
    parser.add_argument(
        "--source-sha",
        help="Source head SHA; when different, it must parent the tested merge.",
    )
    compatibility = parser.add_mutually_exclusive_group()
    compatibility.add_argument("--fast", action="store_true", help=argparse.SUPPRESS)
    compatibility.add_argument("--full", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    profile = "fast" if args.fast else "full" if args.full else args.profile
    try:
        steps = _select_steps(profile, args.component)
    except ValueError as error:
        parser.error(str(error))
    started_at = _now()
    started = time.monotonic()
    revision = _git_revision()
    workspace_clean = _git_workspace_clean()
    parent_shas = _git_parent_shas()
    candidate_check, candidate_sha = _candidate_record(
        args.candidate_sha,
        revision,
        workspace_clean,
        args.source_sha,
        parent_shas,
        explicit_required=bool(os.environ.get("CI")),
    )
    source_sha = args.source_sha or candidate_sha

    # Unit drills inject a temporary step topology. Production invocations use
    # the repository manifest; an injected _expected_check_ids mock can still
    # explicitly exercise inventory mismatch behavior.
    expected_function_is_original = (
        getattr(_expected_check_ids, "__module__", None) == __name__
    )
    if PROFILE_STEPS is not _DEFAULT_PROFILE_STEPS and expected_function_is_original:
        expected_checks = ["candidate-sha", *(step.name for step in steps)]
    else:
        try:
            expected_checks = _expected_check_ids(profile, args.component)
        except (KeyError, TypeError, ValueError) as error:
            expected_checks = ["candidate-sha"]
            manifest_error = _meta_record(
                "check-inventory",
                execution="unavailable",
                outcome="failed",
                message=str(error),
                failure_class="infrastructure",
            )
        else:
            manifest_error = None

    actual_checks = ["candidate-sha", *(step.name for step in steps)]
    inventory = (
        manifest_error
        if "manifest_error" in locals() and manifest_error is not None
        else _inventory_record(expected_checks, actual_checks)
    )
    checks: list[dict[str, Any]] = [candidate_check, inventory]
    identity_or_inventory_failed = any(
        check["status"]["outcome"] == "failed" for check in checks
    )
    if identity_or_inventory_failed:
        for step in steps:
            checks.append(
                _check_record(
                    step,
                    execution="blocked",
                    outcome="failed",
                    duration_seconds=0.0,
                    message="not executed because candidate identity or inventory failed",
                    failure_class="infrastructure",
                )
            )
    else:
        for step in steps:
            print(f"\n=== {step.name} ===", flush=True)
            checks.append(_run(step))

    verdict, reason, classification = _verdict(checks)
    component = args.component or "all"
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "profile": {
            "name": profile,
            "version": PROFILE_VERSION,
            "component": component,
        },
        "commit_sha": revision,
        "candidate_sha": candidate_sha,
        "source_sha": source_sha,
        "candidate": {
            "expected_sha": candidate_sha,
            "checked_out_sha": revision,
            "matches": candidate_check["status"]["outcome"] == "passed",
            "source": "explicit" if args.candidate_sha else "inferred-local",
            "source_sha": source_sha,
            "parent_shas": list(parent_shas or ()),
            "tests_merge_candidate": bool(
                source_sha
                and candidate_sha
                and source_sha.lower() != candidate_sha.lower()
            ),
            "workspace_clean": workspace_clean,
            "promotable": bool(
                args.candidate_sha
                and workspace_clean is True
                and candidate_check["status"]["outcome"] == "passed"
            ),
        },
        "run": {
            "id": os.environ.get("GITHUB_RUN_ID"),
            "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "event": os.environ.get("GITHUB_EVENT_NAME"),
            "workflow": os.environ.get("GITHUB_WORKFLOW"),
        },
        "started_at": started_at,
        "finished_at": _now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "environment": {
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
            "python_implementation": platform.python_implementation(),
            "ci": bool(os.environ.get("CI")),
        },
        "tool_versions": _tool_versions(steps),
        "expected_checks": expected_checks,
        "checks": checks,
        "summary": {
            "executed": [
                check["id"]
                for check in checks
                if check["status"]["execution"] == "executed"
            ],
            "passed": [
                check["id"]
                for check in checks
                if check["status"]["outcome"] == "passed"
            ],
            "failed": [
                check["id"]
                for check in checks
                if check["status"]["outcome"] == "failed"
            ],
            "unavailable": [
                check["id"]
                for check in checks
                if check["status"]["execution"] == "unavailable"
            ],
            "skipped": [
                check["id"]
                for check in checks
                if check["status"]["outcome"] == "skipped"
            ],
        },
        "artifacts": {"manifest": str(args.manifest.resolve())},
        "verdict": verdict,
        "reason": reason,
        "classification": classification,
    }
    _write_manifest(args.manifest, evidence)

    print("\n" + "=" * 80)
    for check in checks:
        status = check["status"]
        print(
            f"  {status['execution'].upper():<11} {status['outcome'].upper():<7} "
            f"{check['id']:<30} {check['duration_seconds']:7.1f}s"
        )
    print("=" * 80)
    print(f"  {verdict.upper()} - {reason}; evidence: {args.manifest.resolve()}")
    return 0 if verdict == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
