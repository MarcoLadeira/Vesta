"""Deterministic evidence contracts for source-free OPai desktop artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess  # nosec B404 - fixed Git executable and arguments only
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .proc import no_window_kwargs


EVIDENCE_SCHEMA_VERSION = 1
CHECKSUMS_NAME = "SHA256SUMS.txt"
PROVENANCE_NAME = "provenance.json"
SIGNING_STATUS_NAME = "signing-status.json"
BUILD_REQUIREMENTS_PATH = Path("requirements") / "desktop-build.txt"
REQUIRED_BUILD_PINS = {"PySide6": "6.11.1", "Nuitka": "4.0"}
REQUIRED_WEB_ASSETS = (
    "index.html",
    "app.js",
    "styles.css",
    "activity.js",
    "message-state.js",
)
GUI_QT_MODULES = (
    "Core",
    "Gui",
    "Widgets",
    "WebChannel",
    "WebEngineCore",
    "WebEngineWidgets",
)
ARTIFACT_ENVIRONMENT_BLOCKLIST = frozenset(
    {
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "OPAI_HUB_ROOT",
        "LOCAL_MODEL_URL",
        "LOCAL_MODEL_NAME",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }
)
_TEXT_ARTIFACT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".conf",
        ".css",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".log",
        ".md",
        ".plist",
        ".py",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_SECRET_PATTERN = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{20,}\b|\bghp_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b|\bAIza[A-Za-z0-9_-]{20,}\b)",
    flags=re.IGNORECASE,
)
_PRIVATE_URL_PATTERN = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|[a-z0-9-]+\.local)(?::\d+)?(?:[/?#]|$)",
    flags=re.IGNORECASE,
)
_DEVELOPMENT_OVERRIDE_PATTERN = re.compile(
    r"\b(?:OPAI_HUB_ROOT|LOCAL_MODEL_URL|LOCAL_MODEL_NAME|PYTHONPATH)\s*=",
    flags=re.IGNORECASE,
)
_EVIDENCE_FILENAMES = frozenset({CHECKSUMS_NAME, PROVENANCE_NAME, SIGNING_STATUS_NAME})


class ArtifactReleaseError(RuntimeError):
    """Raised when a desktop artifact cannot meet the release contract."""


@dataclass(frozen=True)
class ReleaseRef:
    """An immutable source reference embedded in a desktop artifact bundle."""

    tag: str
    commit: str
    rehearsal: bool = False


@dataclass(frozen=True)
class DeploymentSpec:
    """One native component deployment with explicit runtime inclusions."""

    component: str
    tool: str
    name: str
    project_root: Path
    entrypoint: Path
    output_dir: Path
    qt_modules: tuple[str, ...]
    extra_args: tuple[str, ...]


@dataclass(frozen=True)
class DeploymentSpecs:
    """The independently deployable GUI and CLI artifact inputs."""

    gui: DeploymentSpec
    cli: DeploymentSpec


def load_build_pins(project_root: Path) -> dict[str, str]:
    """Load the exact deployment tool pins required for reproducible rehearsals."""
    path = project_root.expanduser().resolve() / BUILD_REQUIREMENTS_PATH
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ArtifactReleaseError(f"desktop build pins are missing: {path}") from exc
    pins: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise ArtifactReleaseError(f"desktop build pin is not exact: {line}")
        name, version = (part.strip() for part in line.split("==", 1))
        if not name or not version or name in pins:
            raise ArtifactReleaseError(f"invalid desktop build pin: {line}")
        pins[name] = version
    if pins != REQUIRED_BUILD_PINS:
        raise ArtifactReleaseError(
            "desktop build pins must be exactly PySide6==6.11.1 and Nuitka==4.0"
        )
    return pins


def _data_file_arg(root: Path, relative: Path) -> str:
    source = root / relative
    if not source.is_file():
        raise ArtifactReleaseError(
            f"required desktop runtime file is missing: {relative}"
        )
    return f"--include-data-files={source.as_posix()}={relative.as_posix()}"


def _data_dir_arg(root: Path, relative: Path) -> str:
    source = root / relative
    if not source.is_dir():
        raise ArtifactReleaseError(
            f"required desktop runtime directory is missing: {relative}"
        )
    return f"--include-data-dir={source.as_posix()}={relative.as_posix()}"


def deployment_specs(project_root: Path, output_dir: Path) -> DeploymentSpecs:
    """Return deploy inputs that keep GUI and CLI payloads deliberately separate."""
    root = project_root.expanduser().resolve()
    output = output_dir.expanduser().resolve()
    load_build_pins(root)
    gui_entry = root / "scripts" / "desktop_gui_entry.py"
    cli_entry = root / "scripts" / "desktop_cli_entry.py"
    if not gui_entry.is_file() or not cli_entry.is_file():
        raise ArtifactReleaseError("desktop artifact entry points are missing")
    gui_args = [
        "--include-package=opai",
        "--include-package=opaihub",
        "--include-package=opcoding",
        _data_dir_arg(root, Path("opai") / "assets" / "fonts"),
        _data_file_arg(root, Path("opai") / "assets" / "opai-icon.png"),
        _data_file_arg(root, Path("opai") / "assets" / "opai-mascot.png"),
        _data_dir_arg(root, Path("opaihub") / "data"),
    ]
    gui_args.extend(
        _data_file_arg(root, Path("opai") / "assets" / "web" / asset)
        for asset in REQUIRED_WEB_ASSETS
    )
    cli_args = (
        "--include-package=opai",
        "--include-package=opaihub",
        "--include-package=opcoding",
        _data_dir_arg(root, Path("opaihub") / "data"),
    )
    return DeploymentSpecs(
        gui=DeploymentSpec(
            component="gui",
            tool="pyside6-deploy",
            name="OPai",
            project_root=root,
            entrypoint=gui_entry,
            output_dir=output / "gui",
            qt_modules=GUI_QT_MODULES,
            extra_args=tuple(gui_args),
        ),
        cli=DeploymentSpec(
            component="cli",
            tool="python -m nuitka",
            name="opai",
            project_root=root,
            entrypoint=cli_entry,
            output_dir=output / "cli",
            qt_modules=(),
            extra_args=cli_args,
        ),
    )


def render_pyside_deploy_spec(spec: DeploymentSpec, *, build_python: Path) -> str:
    """Render a self-contained PySide6 Deploy config for the GUI component."""
    if spec.tool != "pyside6-deploy":
        raise ArtifactReleaseError("only the GUI component may use PySide6 Deploy")
    root = spec.project_root
    icon = root / "opai" / "assets" / "opai-icon.png"
    if not icon.is_file():
        raise ArtifactReleaseError("desktop icon is missing")
    pins = load_build_pins(root)
    extra_args = shlex.join(spec.extra_args)
    return "\n".join(
        (
            "[app]",
            f"title = {spec.name}",
            f"project_dir = {root.as_posix()}",
            f"input_file = {spec.entrypoint.as_posix()}",
            f"exec_directory = {spec.output_dir.as_posix()}",
            f"icon = {icon.as_posix()}",
            "",
            "[python]",
            f"python_path = {build_python.as_posix()}",
            f"packages = Nuitka=={pins['Nuitka']}",
            "",
            "[qt]",
            f"modules = {','.join(spec.qt_modules)}",
            "",
            "[nuitka]",
            "mode = standalone",
            f"extra_args = {extra_args}",
            "",
        )
    )


def build_commands(
    specs: DeploymentSpecs,
    *,
    build_python: Path,
    deploy_script: Path,
    spec_dir: Path,
) -> tuple[list[str], list[str]]:
    """Return exact GUI/CLI build commands without executing or downloading."""
    nuitka_version = REQUIRED_BUILD_PINS["Nuitka"]
    gui_spec_path = spec_dir / "gui-pyside6-deploy.spec"
    gui_command = [
        str(build_python),
        str(deploy_script),
        "--config-file",
        str(gui_spec_path),
        "--force",
        f"--nuitka-version={nuitka_version}",
    ]
    cli_command = [
        str(build_python),
        "-m",
        "nuitka",
        str(specs.cli.entrypoint),
        "--standalone",
        "--follow-imports",
        f"--output-dir={specs.cli.output_dir}",
        f"--output-filename={specs.cli.name}",
        *specs.cli.extra_args,
    ]
    return (gui_command, cli_command)


def _component_executable(bundle: Path, component: str, name: str) -> Path:
    directory = bundle / component
    candidates = (
        directory / f"{name}.exe",
        directory / name,
        directory / f"{name}.app" / "Contents" / "MacOS" / name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ArtifactReleaseError(f"artifact is missing the {component} executable")


def smoke_commands(
    bundle: Path, home: Path, result_path: Path
) -> tuple[list[str], ...]:
    """Return source-free CLI and real-GUI smoke commands for one bundle."""
    root = bundle.expanduser().resolve()
    destination = result_path.expanduser().resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        pass
    else:
        raise ArtifactReleaseError(
            "artifact smoke results must be written outside the bundle"
        )
    cli = _component_executable(root, "cli", "opai")
    gui = _component_executable(root, "gui", "OPai")
    fixture = home.expanduser().resolve() / "fixture-project"
    return (
        [str(cli), "--help"],
        [str(cli), "doctor", "--project", str(fixture)],
        [
            str(gui),
            "--artifact-smoke",
            "--project",
            str(fixture),
            "--result",
            str(destination),
        ],
    )


def isolated_artifact_environment(
    home: Path, base: dict[str, str] | None = None
) -> dict[str, str]:
    """Build a hostile clean-user environment for native artifact smoke runs."""
    root = home.expanduser().resolve()
    environment = dict(os.environ if base is None else base)
    for name in ARTIFACT_ENVIRONMENT_BLOCKLIST:
        environment.pop(name, None)
    locations = {
        "HOME": root,
        "USERPROFILE": root,
        "XDG_CONFIG_HOME": root / ".config",
        "XDG_DATA_HOME": root / ".local" / "share",
        "APPDATA": root / "AppData" / "Roaming",
        "LOCALAPPDATA": root / "AppData" / "Local",
        "TEMP": root / "Temp",
        "TMP": root / "Temp",
    }
    environment.update({name: str(path) for name, path in locations.items()})
    if os.name == "nt":
        system_root = environment.get("SystemRoot") or environment.get("WINDIR")
        if system_root:
            environment["PATH"] = os.pathsep.join(
                [str(Path(system_root) / "System32"), system_root]
            )
    else:
        environment["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    return environment


def scan_artifact_text(bundle: Path) -> list[dict[str, str]]:
    """Find sensitive-looking text that must not ship in a desktop artifact."""
    root = bundle.expanduser().resolve()
    findings: list[dict[str, str]] = []
    if not root.is_dir():
        raise ArtifactReleaseError(f"artifact bundle does not exist: {root}")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _TEXT_ARTIFACT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = _relative_path(root, path)
        for kind, pattern in (
            ("secret", _SECRET_PATTERN),
            ("private_url", _PRIVATE_URL_PATTERN),
            ("development_override", _DEVELOPMENT_OVERRIDE_PATTERN),
        ):
            if pattern.search(text):
                findings.append({"kind": kind, "path": relative})
    return findings


def _git(root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(  # nosec B603 - args are constant internal Git calls
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
            **no_window_kwargs(),
        )
    except OSError as exc:
        raise ArtifactReleaseError(
            "Git is required to identify the release commit"
        ) from exc
    if completed.returncode != 0:
        if args == ["describe", "--exact-match", "--tags", "HEAD"]:
            raise ArtifactReleaseError("HEAD is not exactly tagged")
        detail = (
            completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        )
        raise ArtifactReleaseError(f"Git command failed: {detail}")
    return completed.stdout.strip()


def release_ref(
    root: Path,
    *,
    allow_untagged: bool = False,
    run_git: Callable[[list[str]], str] | None = None,
) -> ReleaseRef:
    """Return an exact v-tag reference, or an explicit local rehearsal reference."""
    project_root = root.expanduser().resolve()
    invoke = run_git or (lambda args: _git(project_root, args))
    commit = invoke(["rev-parse", "HEAD"]).strip()
    if len(commit) != 40 or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise ArtifactReleaseError(
            "Git did not return a full lowercase commit identifier"
        )
    try:
        tag = invoke(["describe", "--exact-match", "--tags", "HEAD"]).strip()
    except ArtifactReleaseError:
        if allow_untagged:
            return ReleaseRef(tag="untagged-rehearsal", commit=commit, rehearsal=True)
        raise ArtifactReleaseError(
            "desktop artifacts must be built from a commit exactly tagged with v*"
        ) from None
    if not tag.startswith("v"):
        raise ArtifactReleaseError("desktop artifact tags must begin with v")
    return ReleaseRef(tag=tag, commit=commit)


def _relative_path(bundle: Path, path: Path) -> str:
    try:
        return path.relative_to(bundle).as_posix()
    except ValueError as exc:
        raise ArtifactReleaseError("artifact entry is outside the bundle") from exc


def _hash_entry(bundle: Path, path: Path) -> str:
    if path.is_symlink():
        target = path.resolve()
        try:
            target.relative_to(bundle)
        except ValueError as exc:
            raise ArtifactReleaseError(
                f"artifact symlink escapes the bundle: {_relative_path(bundle, path)}"
            ) from exc
        return hashlib.sha256(
            b"opai-artifact-symlink-v1\0" + str(path.readlink()).encode("utf-8")
        ).hexdigest()
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                hasher.update(chunk)
    except OSError as exc:
        raise ArtifactReleaseError(
            f"could not read artifact entry: {_relative_path(bundle, path)}"
        ) from exc
    return hasher.hexdigest()


def _distributable_entries(bundle: Path) -> dict[str, str]:
    if not bundle.is_dir():
        raise ArtifactReleaseError(f"artifact bundle does not exist: {bundle}")
    entries: dict[str, str] = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = _relative_path(bundle, path)
        if relative in _EVIDENCE_FILENAMES:
            continue
        entries[relative] = _hash_entry(bundle, path)
    if not entries:
        raise ArtifactReleaseError("artifact bundle has no distributable files")
    return entries


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_bundle_evidence(
    bundle: Path,
    release: ReleaseRef,
    *,
    platform: str,
    signing_status: str = "unsigned-prealpha",
) -> dict[str, Path]:
    """Write deterministic provenance, checksums, and explicit signing state."""
    root = bundle.expanduser().resolve()
    entries = _distributable_entries(root)
    if signing_status not in {"unsigned-prealpha", "signed", "signed-and-notarized"}:
        raise ArtifactReleaseError(f"unsupported signing status: {signing_status}")
    provenance = root / PROVENANCE_NAME
    checksums = root / CHECKSUMS_NAME
    signing = root / SIGNING_STATUS_NAME
    _write_json(
        provenance,
        {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "tag": release.tag,
            "commit": release.commit,
            "rehearsal": release.rehearsal,
            "platform": platform,
        },
    )
    checksums.write_text(
        "".join(f"{digest}  {relative}\n" for relative, digest in entries.items()),
        encoding="utf-8",
    )
    _write_json(
        signing,
        {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "status": signing_status,
            "production_ready": signing_status == "signed-and-notarized",
            "reason": (
                "Signing and notarisation evidence was not supplied."
                if signing_status == "unsigned-prealpha"
                else "External signing evidence must be retained with the release."
            ),
        },
    )
    return {"provenance": provenance, "checksums": checksums, "signing": signing}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_checksums(path: Path) -> dict[str, str] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    entries: dict[str, str] = {}
    for line in lines:
        try:
            digest, relative = line.split("  ", 1)
        except ValueError:
            return None
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not relative
            or relative in entries
        ):
            return None
        entries[relative] = digest
    return entries


def verify_bundle(bundle: Path) -> dict[str, Any]:
    """Verify local evidence and return machine-readable release diagnostics."""
    root = bundle.expanduser().resolve()
    problems: list[str] = []
    provenance = _read_json(root / PROVENANCE_NAME)
    signing = _read_json(root / SIGNING_STATUS_NAME)
    expected = _read_checksums(root / CHECKSUMS_NAME)
    if (
        provenance is None
        or provenance.get("schema_version") != EVIDENCE_SCHEMA_VERSION
    ):
        problems.append("invalid provenance")
    if signing is None or signing.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        problems.append("invalid signing status")
    if expected is None:
        problems.append("invalid checksums")
        actual: dict[str, str] = {}
    else:
        try:
            actual = _distributable_entries(root)
        except ArtifactReleaseError:
            actual = {}
            problems.append("unreadable distributable entries")
        if expected != actual:
            problems.append("hash mismatch")
    return {
        "ok": not problems,
        "problems": problems,
        "tag": provenance.get("tag") if provenance else None,
        "commit": provenance.get("commit") if provenance else None,
        "platform": provenance.get("platform") if provenance else None,
        "rehearsal": bool(provenance.get("rehearsal")) if provenance else None,
        "signing_status": signing.get("status") if signing else None,
        "production_ready": bool(signing.get("production_ready")) if signing else False,
        "file_count": len(actual),
    }
