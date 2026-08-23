"""Standard-library startup boundary for every OPai application entry point.

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
from .release_identity import release_version_text, surface_identity_payload


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


def _repair_command(mode: str, *, desktop: bool = False) -> str:
    if mode == "source_checkout":
        suffix = '".[desktop-gui]"' if desktop else "."
        return f"Run `python -m pip install -e {suffix}` and retry."
    if mode == "packaged_application":
        return "Reinstall OPai from the signed desktop package, then retry."
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
        except importlib.metadata.PackageNotFoundError:
            distribution_version = None
        except Exception as exc:
            raise BootstrapFailure(
                category="package_metadata_unavailable",
                component="opai-distribution-metadata",
                message="OPai package metadata is unreadable.",
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
                "This OPai source copy has neither an editable checkout marker nor "
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
                f"Installed metadata reports OPai {distribution_version}, but the "
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
) -> StartupContext:
    """Validate installation and dependency boundaries before deep imports."""

    arguments = tuple(str(value) for value in argv)
    context = detect_startup_context(
        source_root=source_root,
        distribution_lookup=distribution_lookup,
        packaged=packaged,
    )
    if not check_dependencies:
        return context
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
            message=f"OPai requires {missing.component} before {missing.purpose}.",
            remediation=_repair_command(context.startup_mode, desktop=desktop),
            startup_mode=context.startup_mode,
        )
    return context


def _json_requested(arguments: Iterable[str]) -> bool:
    return "--json" in tuple(arguments)


def _version_requested(arguments: Iterable[str]) -> bool:
    values = tuple(arguments)
    return values == ("--version",) or bool(values and values[0] == "version")


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
        f"OPai startup failed [{failure.category}] ({failure.component}).\n"
        f"{failure.message}\n{failure.remediation}",
        file=stderr,
    )


def _desktop_failure_dialog(failure: BootstrapFailure) -> None:
    """Best-effort native display for a console-free desktop launcher."""

    text = f"{failure.message}\n\n{failure.remediation}\n\nCode: {failure.category}"
    try:
        if sys.platform.startswith("win"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "OPai could not start", 0x10)
            return
        if sys.platform == "darwin":
            script = (
                "display dialog "
                + json.dumps(text)
                + ' with title "OPai could not start" buttons {"OK"} '
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
        messagebox.showerror("OPai could not start", text, parent=root)
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
            message="OPai could not parse a user configuration file.",
            remediation=(
                "Run `opai doctor`, repair the reported configuration file, and retry."
            ),
            startup_mode=mode,
        )
    if isinstance(exc, importlib.metadata.PackageNotFoundError):
        return BootstrapFailure(
            category="package_metadata_unavailable",
            component="opai-distribution-metadata",
            message="OPai package metadata is unavailable.",
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
                message=f"OPai requires {dependency} before startup can continue.",
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
            check_dependencies=not _version_requested(arguments),
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
    except (BootstrapFailure, json.JSONDecodeError, ModuleNotFoundError) as exc:
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
    )


def cli_main() -> int:
    return run_cli()


def desktop_main() -> int:
    return run_desktop(failure_handler=_desktop_failure_dialog)
