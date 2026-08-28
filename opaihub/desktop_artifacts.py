"""Deterministic evidence contracts for source-free OPai desktop artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess  # nosec B404 - fixed Git executable and arguments only
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opai._generated_release import PUBLISHED_TAG
from opai.asset_identity import REQUIRED_WEB_ASSETS, asset_manifest
from opai.release_identity import (
    ReleaseIdentityError,
    artifact_identity_payload,
    validate_artifact_identity,
)
from .command_runner import redact
from .proc import no_window_kwargs
from .boundary_errors import safe_detail


EVIDENCE_SCHEMA_VERSION = 2
CHECKSUMS_NAME = "SHA256SUMS.txt"
PROVENANCE_NAME = "provenance.json"
SIGNING_STATUS_NAME = "signing-status.json"
BUILD_REQUIREMENTS_PATH = Path("requirements") / "desktop-build.txt"
REQUIRED_BUILD_PINS = {"PySide6": "6.11.1", "Nuitka": "4.0"}
GUI_QT_MODULES = (
    "Core",
    "Gui",
    "Widgets",
    "WebChannel",
    "WebEngineCore",
    "WebEngineWidgets",
)
ARTIFACT_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "comspec",
        "lang",
        "lc_all",
        "lc_ctype",
        "number_of_processors",
        "os",
        "pathext",
        "processor_architecture",
        "processor_identifier",
        "systemroot",
        "windir",
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
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
# Checksums cannot contain their own hash, but they must bind every other
# release-evidence file so a plausible signing/provenance rewrite is detected.
_EVIDENCE_FILENAMES = frozenset({CHECKSUMS_NAME})


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
        _data_dir_arg(root, Path("opai") / "assets" / "web" / "icons"),
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


def _completed_detail(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "unknown verifier error").strip()
    return detail[-1000:]


def _is_windows() -> bool:
    """Keep platform selection mockable without changing process-global ``os.name``."""

    return os.name == "nt"


def native_platform_signature_problems(
    bundle: Path,
    platform_name: str,
    *,
    windows_signer_thumbprint: str | None = None,
    macos_team_id: str | None = None,
) -> list[str]:
    """Verify all shipped native code with the current platform trust store.

    This is intentionally separate from checksum validation: only the operating
    system can establish whether a signed native artifact still has a valid
    platform signature after packaging. The expected publisher identity must be
    supplied out of band; a generic trusted certificate is not sufficient.
    """
    root = bundle.expanduser().resolve()
    platform_key = str(platform_name).casefold()
    if platform_key == "windows":
        expected_thumbprint = (
            re.sub(r"\\s+", "", windows_signer_thumbprint).upper()
            if isinstance(windows_signer_thumbprint, str)
            else ""
        )
        if _SHA256_PATTERN.fullmatch(expected_thumbprint) is None and not re.fullmatch(
            r"[0-9A-F]{40}", expected_thumbprint
        ):
            return ["expected Windows signer thumbprint is required"]
        if not _is_windows():
            return ["a Windows artifact can only be signature-verified on Windows"]
        targets = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in {".exe", ".dll", ".pyd"}
        )
        if not targets:
            return ["Windows bundle has no executable code files to verify"]
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return ["PowerShell is required to verify Windows Authenticode"]
        script = (
            "$ErrorActionPreference = 'Stop'; $expected = $args[0]; $invalid = @(); "
            "foreach ($path in $args[1..($args.Length - 1)]) { "
            "$signature = Get-AuthenticodeSignature -LiteralPath $path; "
            "$thumbprint = ($signature.SignerCertificate.Thumbprint -replace '\\s', '').ToUpperInvariant(); "
            "if ($signature.Status -ne 'Valid' -or $thumbprint -ne $expected) { "
            '$invalid += "$path=$($signature.Status):$thumbprint" } }; '
            "if ($invalid.Count -gt 0) { $invalid | Write-Error; exit 1 }"
        )
        try:
            completed = subprocess.run(  # nosec B603 - fixed verifier and bundle paths
                [
                    str(Path(powershell).resolve()),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                    expected_thumbprint,
                    *[str(path) for path in targets],
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return [f"Authenticode verifier could not run: {safe_detail(exc)}"]
        return (
            []
            if completed.returncode == 0
            else [f"Authenticode rejected bundle code: {_completed_detail(completed)}"]
        )
    if platform_key not in {"darwin", "macos"}:
        return [
            f"unsupported artifact platform for signature verification: {platform_name}"
        ]
    if sys.platform != "darwin":
        return ["a macOS artifact can only be signature-verified on macOS"]
    expected_team_id = (
        macos_team_id.strip().upper() if isinstance(macos_team_id, str) else ""
    )
    if not re.fullmatch(r"[A-Z0-9]{10}", expected_team_id):
        return ["expected macOS Team ID is required"]
    try:
        cli = _component_executable(root, "cli", "opai")
    except ArtifactReleaseError as exc:
        return [safe_detail(exc)]
    app = root / "gui" / "OPai.app"
    if not app.is_dir():
        return ["artifact is missing the GUI application bundle"]
    commands = (
        (
            "codesign GUI",
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
            None,
        ),
        (
            "codesign CLI",
            ["/usr/bin/codesign", "--verify", "--strict", str(cli)],
            None,
        ),
        (
            "Gatekeeper GUI",
            ["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)],
            None,
        ),
        (
            "Gatekeeper CLI",
            ["/usr/sbin/spctl", "--assess", "--type", "execute", str(cli)],
            None,
        ),
        (
            "codesign GUI identity",
            ["/usr/bin/codesign", "-d", "--verbose=4", str(app)],
            f"TeamIdentifier={expected_team_id}",
        ),
        (
            "codesign CLI identity",
            ["/usr/bin/codesign", "-d", "--verbose=4", str(cli)],
            f"TeamIdentifier={expected_team_id}",
        ),
        (
            "notarisation staple",
            ["/usr/bin/xcrun", "stapler", "validate", str(app)],
            None,
        ),
    )
    problems: list[str] = []
    for label, command, expected_output in commands:
        try:
            completed = subprocess.run(  # nosec B603 - fixed macOS verifier and bundle paths
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            problems.append(f"{label} verifier could not run: {safe_detail(exc)}")
            continue
        if completed.returncode != 0:
            problems.append(f"{label} failed: {_completed_detail(completed)}")
        elif expected_output is not None and expected_output not in (
            f"{completed.stdout}\n{completed.stderr}"
        ):
            problems.append(f"{label} did not match the expected publisher identity")
    return problems


def isolated_artifact_environment(
    home: Path, base: dict[str, str] | None = None
) -> dict[str, str]:
    """Build a hostile clean-user environment for native artifact smoke runs."""
    root = home.expanduser().resolve()
    source = os.environ if base is None else base
    environment = {
        name: value
        for name, value in source.items()
        if name.casefold() in ARTIFACT_ENVIRONMENT_ALLOWLIST
    }
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
        system_root = next(
            (
                value
                for name, value in environment.items()
                if name.casefold() in {"systemroot", "windir"}
            ),
            None,
        )
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
    git = shutil.which("git")
    if not git:
        raise ArtifactReleaseError("Git is required to identify the release commit")
    git_executable = str(Path(git).resolve())
    try:
        completed = subprocess.run(  # nosec B603 - args are constant internal Git calls
            [git_executable, "-C", str(root), *args],
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
    if tag != PUBLISHED_TAG:
        raise ArtifactReleaseError(
            f"desktop artifact tag {tag!r} does not match canonical {PUBLISHED_TAG!r}"
        )
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


def _valid_signing_status(value: object, *, platform: object = None) -> bool:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != EVIDENCE_SCHEMA_VERSION
    ):
        return False
    status = value.get("status")
    if status == "unsigned-prealpha":
        return (
            value.get("production_ready") is False and value.get("verification") is None
        )
    if status not in {"signed", "signed-and-notarized"}:
        return False
    verification = value.get("verification")
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        return False
    tool = verification.get("tool")
    log_sha256 = verification.get("log_sha256")
    if not isinstance(tool, str) or not tool.strip():
        return False
    if not isinstance(log_sha256, str) or _SHA256_PATTERN.fullmatch(log_sha256) is None:
        return False
    if not isinstance(platform, str):
        return False
    # Native signatures bind executable code but not the complete portable
    # archive. A production claim additionally needs a detached, authenticated
    # archive attestation, which cannot safely live inside that archive.
    return value.get("production_ready") is False


def write_bundle_evidence(
    bundle: Path,
    release: ReleaseRef,
    *,
    platform: str,
    signing_status: str = "unsigned-prealpha",
    signing_evidence: dict[str, Any] | None = None,
    build_metadata: dict[str, Any] | None = None,
    artifact_identity: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Write provenance, checksums, and explicit non-root-of-trust signing state."""
    root = bundle.expanduser().resolve()
    if signing_status not in {"unsigned-prealpha", "signed", "signed-and-notarized"}:
        raise ArtifactReleaseError(f"unsupported signing status: {signing_status}")
    if signing_status != "unsigned-prealpha":
        if (
            not isinstance(signing_evidence, dict)
            or signing_evidence.get("verified") is not True
        ):
            raise ArtifactReleaseError(
                "signed artifact evidence requires an explicit successful verification record"
            )
        if (
            not isinstance(signing_evidence.get("tool"), str)
            or not signing_evidence["tool"].strip()
        ):
            raise ArtifactReleaseError(
                "signed artifact evidence must name its verification tool"
            )
        if (
            not isinstance(signing_evidence.get("log_sha256"), str)
            or _SHA256_PATTERN.fullmatch(signing_evidence["log_sha256"]) is None
        ):
            raise ArtifactReleaseError(
                "signed artifact evidence must contain a verification log SHA-256"
            )
    provenance = root / PROVENANCE_NAME
    checksums = root / CHECKSUMS_NAME
    signing = root / SIGNING_STATUS_NAME
    if build_metadata is not None:
        if not isinstance(build_metadata, dict) or not build_metadata:
            raise ArtifactReleaseError("build metadata must be a non-empty object")
        try:
            normalized_build_metadata = json.loads(
                json.dumps(build_metadata, sort_keys=True, separators=(",", ":"))
            )
        except (TypeError, ValueError) as exc:
            raise ArtifactReleaseError(
                "build metadata must be JSON-serializable"
            ) from exc
    else:
        normalized_build_metadata = None
    if artifact_identity is None:
        artifact_identity = artifact_identity_payload(
            build_id=release.commit,
            assets=asset_manifest(
                Path(__file__).resolve().parents[1] / "opai" / "assets"
            ),
            platform_name=platform,
            architecture="unknown",
        )
    try:
        normalized_artifact_identity = validate_artifact_identity(
            artifact_identity,
            build_id=release.commit,
            platform_name=platform,
            release_tag=release.tag,
            rehearsal=release.rehearsal,
        )
        normalized_artifact_identity = json.loads(
            json.dumps(
                normalized_artifact_identity, sort_keys=True, separators=(",", ":")
            )
        )
    except (ReleaseIdentityError, TypeError, ValueError) as exc:
        raise ArtifactReleaseError(redact(str(exc))) from exc
    if normalized_build_metadata is not None:
        build_compatibility = normalized_build_metadata.get("compatibility")
        if (
            isinstance(build_compatibility, dict)
            and normalized_artifact_identity.get("compatibility") != build_compatibility
        ):
            raise ArtifactReleaseError(
                "artifact compatibility does not match candidate build metadata"
            )
    provenance_value: dict[str, Any] = {
        "artifact_identity": normalized_artifact_identity,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "tag": release.tag,
        "commit": release.commit,
        "rehearsal": release.rehearsal,
        "platform": platform,
    }
    if normalized_build_metadata is not None:
        provenance_value["build"] = normalized_build_metadata
    _write_json(
        provenance,
        provenance_value,
    )
    _write_json(
        signing,
        {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "status": signing_status,
            "production_ready": False,
            "reason": (
                "Signing and notarisation evidence was not supplied."
                if signing_status == "unsigned-prealpha"
                else (
                    "Native signing evidence is recorded, but a detached authenticated "
                    "archive attestation is required before public release."
                )
            ),
            "verification": signing_evidence,
        },
    )
    entries = _distributable_entries(root)
    checksums.write_text(
        "".join(f"{digest}  {relative}\n" for relative, digest in entries.items()),
        encoding="utf-8",
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


def verify_bundle(
    bundle: Path,
    *,
    signature_verifier: Callable[[Path, str], list[str]] | None = None,
) -> dict[str, Any]:
    """Verify bundle integrity and require native proof for signed claims.

    Checksums catch accidental corruption, but a signer-status file and its
    in-bundle manifest are not a cryptographic root of trust. A bundle claiming
    to be signed therefore needs a current platform verifier and a detached,
    authenticated archive attestation before it can be publicly released.
    """
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
    elif not isinstance(provenance.get("artifact_identity"), dict):
        problems.append("invalid artifact identity")
    else:
        try:
            validate_artifact_identity(
                provenance["artifact_identity"],
                build_id=str(provenance.get("commit") or ""),
                platform_name=str(provenance.get("platform") or ""),
                release_tag=str(provenance.get("tag") or ""),
                rehearsal=bool(provenance.get("rehearsal")),
            )
        except ReleaseIdentityError:
            problems.append("invalid artifact identity")
    provenance_platform = (
        provenance.get("platform") if isinstance(provenance, dict) else None
    )
    valid_signing_status = _valid_signing_status(signing, platform=provenance_platform)
    if not valid_signing_status:
        problems.append("invalid signing status")
    signing_status = signing.get("status") if isinstance(signing, dict) else None
    platform_signature_verified = False
    if valid_signing_status and signing_status != "unsigned-prealpha":
        if signature_verifier is None:
            problems.append("platform signature verification is required")
        else:
            try:
                signature_problems = signature_verifier(root, str(provenance_platform))
            except (ArtifactReleaseError, OSError, subprocess.SubprocessError) as exc:
                problems.append(
                    f"platform signature verification failed: {safe_detail(exc)}"
                )
            else:
                if not all(
                    isinstance(problem, str) and problem
                    for problem in signature_problems
                ):
                    problems.append(
                        "platform signature verifier returned invalid diagnostics"
                    )
                elif signature_problems:
                    problems.extend(
                        f"platform signature verification failed: {problem}"
                        for problem in signature_problems
                    )
                else:
                    platform_signature_verified = True
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
        "artifact_identity": (
            provenance.get("artifact_identity") if provenance else None
        ),
        "rehearsal": bool(provenance.get("rehearsal")) if provenance else None,
        "signing_status": signing_status,
        "platform_signature_verified": platform_signature_verified,
        "production_ready": False,
        "outer_release_authentication_required": signing_status
        in {"signed", "signed-and-notarized"},
        "file_count": len(actual),
    }
