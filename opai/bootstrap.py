"""Standard-library startup boundary for every Vesta application entry point.

Nothing in this module imports the CLI, runtime, YAML parser, or Qt at module
load time.  That is deliberate: startup failures must name the missing layer
before a deep import turns them into an unrelated configuration exception.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess  # nosec B404 - macOS-only native startup dialog
import sys
from typing import Callable, Iterable, TextIO

from ._generated_release import APPLICATION_VERSION
from .asset_identity import (
    AssetIntegrityError,
    load_asset_binding,
    verify_asset_binding,
)
from .compatibility import (
    RuntimeCompatibilityError,
    load_compatibility_binding,
    runtime_compatibility_payload,
    validate_runtime_compatibility,
)
from .project_discovery import discover_project_root
from .release_identity import (
    packaged_metadata_paths,
    release_version_text,
    surface_identity_payload,
)


BOOTSTRAP_EXIT_CODE = 78
BOOTSTRAP_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DependencyRequirement:
    import_name: str
    component: str
    purpose: str


@dataclass(frozen=True)
class StartupContext:
    startup_mode: str
    application_version: str
    source_root: Path


@dataclass
class BootstrapFailure(RuntimeError):
    """A bounded, actionable startup failure safe for UI or persistence."""

    category: str
    component: str
    message: str
    remediation: str
    startup_mode: str = "unknown"

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "component": self.component,
            "message": self.message,
            "ok": False,
            "remediation": self.remediation,
            "schema_version": BOOTSTRAP_SCHEMA_VERSION,
            "startup_mode": self.startup_mode,
        }


CORE_DEPENDENCIES = (
    DependencyRequirement("yaml", "PyYAML", "configuration can be loaded"),
    DependencyRequirement(
        "cryptography", "cryptography", "signed local evidence can be verified"
    ),
    DependencyRequirement(
        "packaging", "packaging", "version compatibility can be checked"
    ),
)
DESKTOP_DEPENDENCY = DependencyRequirement(
    "PySide6", "PySide6", "the desktop application can open"
)
WEBENGINE_DEPENDENCIES = (
    DependencyRequirement(
        "PySide6.QtWebEngineWidgets", "QtWebEngine", "the web desktop can render"
    ),
    DependencyRequirement(
        "PySide6.QtWebChannel", "QtWebEngine", "the web desktop bridge can start"
    ),
)


def _source_checkout(root: Path) -> bool:
    return (root / ".git").exists() and (root / "pyproject.toml").is_file()


def _packaged_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _embedded_build_exists(root: Path) -> bool:
    return any(
        candidate.is_file() and not candidate.is_symlink()
        for candidate in (
            root / "opai" / "_embedded_build.json",
            root / "_embedded_build.json",
        )
    )


def _repair_command(mode: str, *, desktop: bool = False) -> str:
    if mode == "source_checkout":
        suffix = '".[desktop-gui]"' if desktop else "."
        return f"Run `python -m pip install -e {suffix}` and retry."
    if mode == "packaged_application":
        return "Reinstall Vesta from the signed desktop package, then retry."
    return (
        "Run `python -m pip install --force-reinstall "
        f'"opai=={APPLICATION_VERSION}"` and retry.'
    )


def detect_startup_context(
    *,
    source_root: Path | None = None,
    distribution_lookup: Callable[[str], str] = importlib.metadata.version,
    packaged: bool | None = None,
) -> StartupContext:
    """Classify this installation without consulting cwd, PATH, or Git."""

    root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
    is_source = _source_checkout(root)
    if is_source:
        distribution_version = None
    else:
        try:
            distribution_version = distribution_lookup("opai")
        except importlib.metadata.PackageNotFoundError as exc:
            if _embedded_build_exists(root):
                mode = (
                    "packaged_application"
                    if (_packaged_runtime() if packaged is None else packaged)
                    else "installed_distribution"
                )
                raise BootstrapFailure(
                    category="package_metadata_unavailable",
                    component="opai-distribution-metadata",
                    message=(
                        "This packaged Vesta payload is missing its installed "
                        "distribution metadata."
                    ),
                    remediation=_repair_command(mode),
                    startup_mode=mode,
                ) from exc
            distribution_version = None
        except Exception as exc:
            raise BootstrapFailure(
                category="package_metadata_unavailable",
                component="opai-distribution-metadata",
                message="Vesta package metadata is unreadable.",
                remediation=_repair_command("installed_distribution"),
                startup_mode="unknown",
            ) from exc

    if is_source:
        mode = "source_checkout"
    elif distribution_version is not None:
        mode = "installed_distribution"
    elif _packaged_runtime() if packaged is None else packaged:
        mode = "packaged_application"
    else:
        raise BootstrapFailure(
            category="unsupported_startup_mode",
            component="raw-source-invocation",
            message=(
                "This Vesta source copy has neither an editable checkout marker nor "
                "installed package metadata."
            ),
            remediation=(
                f'Run `python -m pip install "opai=={APPLICATION_VERSION}"` and '
                "launch the installed `opai` command."
            ),
            startup_mode="unsupported_raw_source",
        )

    if (
        distribution_version is not None
        and str(distribution_version) != APPLICATION_VERSION
    ):
        raise BootstrapFailure(
            category="package_integrity_failure",
            component="opai-distribution-metadata",
            message=(
                f"Installed metadata reports Vesta {distribution_version}, but the "
                f"runtime reports {APPLICATION_VERSION}."
            ),
            remediation=_repair_command(mode),
            startup_mode=mode,
        )
    return StartupContext(
        startup_mode=mode,
        application_version=APPLICATION_VERSION,
        source_root=root,
    )


def _project_root(arguments: Iterable[str]) -> Path:
    values = tuple(str(value) for value in arguments)
    for index, value in enumerate(values):
        if value == "--project" and index + 1 < len(values):
            return Path(values[index + 1]).expanduser().resolve(strict=False)
        if value.startswith("--project="):
            return Path(value.split("=", 1)[1]).expanduser().resolve(strict=False)
    return discover_project_root(Path.cwd())


def _validate_persisted_project_schema(
    arguments: Iterable[str],
    compatibility: dict[str, object],
    *,
    startup_mode: str,
) -> None:
    """Read only the existing project schema sentinel before runtime imports."""

    supported = compatibility.get("project_state_schema_version")
    if not isinstance(supported, int) or isinstance(supported, bool):
        return
    state_path = _project_root(arguments) / ".opaihub" / "project.json"
    try:
        if state_path.is_symlink() or not state_path.is_file():
            return
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Existing state recovery/configuration diagnostics remain authoritative
        # for malformed content. This probe owns only forward compatibility.
        return
    observed = value.get("schema_version") if isinstance(value, dict) else None
    if (
        isinstance(observed, int)
        and not isinstance(observed, bool)
        and observed > supported
    ):
        raise BootstrapFailure(
            category="incompatible_schema",
            component="project-state",
            message=(
                f"Runtime project schema {observed} is newer than this Vesta build "
                f"supports ({supported})."
            ),
            remediation=(
                "Use the newer Vesta version that wrote this project state, or restore "
                "a compatible project-state backup before retrying."
            ),
            startup_mode=startup_mode,
        )


def _missing_specs(
    requirements: Iterable[DependencyRequirement],
    spec_finder: Callable[[str], object | None],
) -> DependencyRequirement | None:
    for requirement in requirements:
        try:
            present = spec_finder(requirement.import_name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            present = False
        if not present:
            return requirement
    return None


def preflight_startup(
    argv: Iterable[str],
    *,
    desktop: bool = False,
    source_root: Path | None = None,
    spec_finder: Callable[[str], object | None] = importlib.util.find_spec,
    distribution_lookup: Callable[[str], str] = importlib.metadata.version,
    packaged: bool | None = None,
    check_dependencies: bool = True,
    validate_integrity: bool = True,
    asset_root: Path | None = None,
    metadata_paths: Iterable[Path] | None = None,
) -> StartupContext:
    """Validate installation and dependency boundaries before deep imports."""

    arguments = tuple(str(value) for value in argv)
    context = detect_startup_context(
        source_root=source_root,
        distribution_lookup=distribution_lookup,
        packaged=packaged,
    )
    if check_dependencies:
        requirements = list(CORE_DEPENDENCIES)
        if desktop:
            requirements.append(DESKTOP_DEPENDENCY)
            if "--classic" not in arguments:
                requirements.extend(WEBENGINE_DEPENDENCIES)
        missing = _missing_specs(requirements, spec_finder)
        if missing is not None:
            raise BootstrapFailure(
                category="missing_dependency",
                component=missing.component,
                message=f"Vesta requires {missing.component} before {missing.purpose}.",
                remediation=_repair_command(context.startup_mode, desktop=desktop),
                startup_mode=context.startup_mode,
            )
    if not validate_integrity:
        return context

    paths = (
        tuple(metadata_paths)
        if metadata_paths is not None
        else (
            Path(__file__).resolve().with_name("_embedded_build.json"),
            *packaged_metadata_paths("release-identity.json"),
        )
    )
    require_binding = context.startup_mode != "source_checkout"
    try:
        compatibility = runtime_compatibility_payload()
        if require_binding:
            compatibility = load_compatibility_binding(paths)
            if compatibility is None:
                raise AssetIntegrityError(
                    "package_integrity_failure",
                    "compatibility-identity",
                    "packaged compatibility identity is missing; reinstall Vesta",
                )
            validate_runtime_compatibility(compatibility)
        _validate_persisted_project_schema(
            arguments,
            compatibility,
            startup_mode=context.startup_mode,
        )
        if desktop:
            assets = load_asset_binding(paths) if require_binding else None
            verify_asset_binding(
                asset_root or Path(__file__).resolve().parent / "assets",
                expected=assets,
                require_binding=require_binding,
            )
    except AssetIntegrityError as exc:
        message = {
            "missing_packaged_asset": "Required packaged Vesta assets are missing.",
            "package_integrity_failure": (
                "Packaged Vesta assets or identity metadata failed integrity validation."
            ),
        }.get(exc.code, "Packaged Vesta integrity validation failed.")
        raise BootstrapFailure(
            category=exc.code,
            component=exc.component,
            message=message,
            remediation=_repair_command(context.startup_mode, desktop=desktop),
            startup_mode=context.startup_mode,
        ) from exc
    except RuntimeCompatibilityError as exc:
        fields = sorted(set(exc.expected) | set(exc.actual))
        mismatched = [
            field
            for field in fields
            if exc.expected.get(field) != exc.actual.get(field)
        ]
        raise BootstrapFailure(
            category="incompatible_schema",
            component="runtime-compatibility",
            message=(
                "Packaged runtime compatibility is unsupported for: "
                + ", ".join(mismatched)
                + "."
            ),
            remediation=_repair_command(context.startup_mode, desktop=desktop),
            startup_mode=context.startup_mode,
        ) from exc
    return context


def _json_requested(arguments: Iterable[str]) -> bool:
    return "--json" in tuple(arguments)


def _version_requested(arguments: Iterable[str]) -> bool:
    values = tuple(arguments)
    return values == ("--version",) or bool(values and values[0] == "version")


def _help_requested(arguments: Iterable[str]) -> bool:
    return any(value in {"-h", "--help"} for value in arguments)


def _gui_requested(arguments: Iterable[str]) -> bool:
    values = tuple(arguments)
    for index, value in enumerate(values):
        if value == "--project":
            continue
        if index and values[index - 1] == "--project":
            continue
        if not value.startswith("-"):
            return value == "gui"
    return False


def _emit_failure(
    failure: BootstrapFailure,
    *,
    arguments: Iterable[str],
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    if _json_requested(arguments):
        print(json.dumps(failure.to_dict(), sort_keys=True), file=stdout)
        return
    print(
        f"Vesta startup failed [{failure.category}] ({failure.component}).\n"
        f"{failure.message}\n{failure.remediation}",
        file=stderr,
    )


def _desktop_failure_dialog(failure: BootstrapFailure) -> None:
    """Best-effort native display for a console-free desktop launcher."""

    text = f"{failure.message}\n\n{failure.remediation}\n\nCode: {failure.category}"
    try:
        if sys.platform.startswith("win"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "Vesta could not start", 0x10)
            return
        if sys.platform == "darwin":
            script = (
                "display dialog "
                + json.dumps(text)
                + ' with title "Vesta could not start" buttons {"OK"} '
                'default button "OK" with icon stop'
            )
            subprocess.run(  # nosec B603 - fixed system executable and safe text
                ["/usr/bin/osascript", "-e", script],
                check=False,
                capture_output=True,
                timeout=10,
            )
            return
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("Vesta could not start", text, parent=root)
        root.destroy()
    except Exception:
        # stderr emission remains the portable fallback; startup diagnostics
        # must never fail because the host cannot create a native dialog.
        return


def _module_failure(exc: BaseException, mode: str) -> BootstrapFailure | None:
    if isinstance(exc, BootstrapFailure):
        return exc
    if isinstance(exc, json.JSONDecodeError):
        return BootstrapFailure(
            category="malformed_user_configuration",
            component="user-configuration",
            message="Vesta could not parse a user configuration file.",
            remediation=(
                "Run `vesta doctor`, repair the reported configuration file, and retry."
            ),
            startup_mode=mode,
        )
    if (
        type(exc).__name__ == "RegistryLoadError"
        and type(exc).__module__ == "opaihub.loader"
    ):
        if isinstance(exc.__cause__, ModuleNotFoundError):
            dependency_failure = _module_failure(exc.__cause__, mode)
            if dependency_failure is not None:
                return dependency_failure
        return BootstrapFailure(
            category="malformed_user_configuration",
            component="user-configuration",
            message="Vesta could not parse a user registry configuration file.",
            remediation=(
                "Run `vesta doctor`, repair the reported configuration file, and retry."
            ),
            startup_mode=mode,
        )
    if isinstance(exc, importlib.metadata.PackageNotFoundError):
        return BootstrapFailure(
            category="package_metadata_unavailable",
            component="opai-distribution-metadata",
            message="Vesta package metadata is unavailable.",
            remediation=_repair_command(mode),
            startup_mode=mode,
        )
    if isinstance(exc, ModuleNotFoundError):
        dependency = {
            "yaml": "PyYAML",
            "PySide6": "PySide6",
            "cryptography": "cryptography",
            "packaging": "packaging",
        }.get(str(exc.name or "").split(".", 1)[0])
        if dependency:
            return BootstrapFailure(
                category="missing_dependency",
                component=dependency,
                message=f"Vesta requires {dependency} before startup can continue.",
                remediation=_repair_command(mode, desktop=dependency == "PySide6"),
                startup_mode=mode,
            )
    return None


def _run(
    arguments: list[str],
    *,
    desktop: bool,
    source_root: Path | None,
    spec_finder: Callable[[str], object | None],
    distribution_lookup: Callable[[str], str],
    importer: Callable[[str], object],
    stdout: TextIO,
    stderr: TextIO,
    failure_handler: Callable[[BootstrapFailure], None] | None = None,
    asset_root: Path | None = None,
    metadata_paths: Iterable[Path] | None = None,
    validate_integrity: bool = True,
) -> int:
    try:
        needs_desktop = (desktop or _gui_requested(arguments)) and (
            "--once" not in arguments
        )
        context = preflight_startup(
            arguments,
            desktop=needs_desktop,
            source_root=source_root,
            spec_finder=spec_finder,
            distribution_lookup=distribution_lookup,
            check_dependencies=not (
                _version_requested(arguments) or _help_requested(arguments)
            ),
            validate_integrity=validate_integrity,
            asset_root=asset_root,
            metadata_paths=metadata_paths,
        )
        if _version_requested(arguments):
            if _json_requested(arguments):
                print(
                    json.dumps(surface_identity_payload(), indent=2, sort_keys=True),
                    file=stdout,
                )
            else:
                print(release_version_text(), file=stdout)
            return 0
        module = importer("opai.cli")
        if desktop:
            return int(module.gui_main())  # type: ignore[attr-defined]
        return int(module.main(arguments))  # type: ignore[attr-defined]
    except Exception as exc:
        failure = _module_failure(
            exc,
            locals().get("context", None).startup_mode
            if "context" in locals()
            else "unknown",
        )
        if failure is None:  # pragma: no cover - the caught types above are mapped
            raise
        _emit_failure(failure, arguments=arguments, stdout=stdout, stderr=stderr)
        if failure_handler is not None:
            failure_handler(failure)
        return BOOTSTRAP_EXIT_CODE


def run_cli(
    argv: Iterable[str] | None = None,
    *,
    source_root: Path | None = None,
    spec_finder: Callable[[str], object | None] = importlib.util.find_spec,
    distribution_lookup: Callable[[str], str] = importlib.metadata.version,
    importer: Callable[[str], object] = importlib.import_module,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    asset_root: Path | None = None,
    metadata_paths: Iterable[Path] | None = None,
    validate_integrity: bool = True,
) -> int:
    return _run(
        list(sys.argv[1:] if argv is None else argv),
        desktop=False,
        source_root=source_root,
        spec_finder=spec_finder,
        distribution_lookup=distribution_lookup,
        importer=importer,
        stdout=stdout or sys.stdout,
        stderr=stderr or sys.stderr,
        failure_handler=None,
        asset_root=asset_root,
        metadata_paths=metadata_paths,
        validate_integrity=validate_integrity,
    )


def run_desktop(
    argv: Iterable[str] | None = None,
    *,
    source_root: Path | None = None,
    spec_finder: Callable[[str], object | None] = importlib.util.find_spec,
    distribution_lookup: Callable[[str], str] = importlib.metadata.version,
    importer: Callable[[str], object] = importlib.import_module,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    failure_handler: Callable[[BootstrapFailure], None] | None = None,
    asset_root: Path | None = None,
    metadata_paths: Iterable[Path] | None = None,
    validate_integrity: bool = True,
) -> int:
    return _run(
        list(sys.argv[1:] if argv is None else argv),
        desktop=True,
        source_root=source_root,
        spec_finder=spec_finder,
        distribution_lookup=distribution_lookup,
        importer=importer,
        stdout=stdout or sys.stdout,
        stderr=stderr or sys.stderr,
        failure_handler=failure_handler,
        asset_root=asset_root,
        metadata_paths=metadata_paths,
        validate_integrity=validate_integrity,
    )


def cli_main() -> int:
    return run_cli()


def desktop_main() -> int:
    return run_desktop(failure_handler=_desktop_failure_dialog)
