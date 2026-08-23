"""Build a channel-labelled portable OPai desktop artifact from an exact tag."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess  # nosec B404 - commands are fixed local tool invocations
import sys
import tempfile
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opai._generated_release import APPLICATION_VERSION, RELEASE_CHANNEL  # noqa: E402
from opai.asset_identity import asset_manifest  # noqa: E402
from opai.update.models import InstallType  # noqa: E402
from opai.update.packaging import runtime_identity  # noqa: E402
from opaihub.desktop_artifacts import (  # noqa: E402
    ArtifactReleaseError,
    DeploymentSpecs,
    build_commands,
    deployment_specs,
    load_build_pins,
    release_ref,
    render_pyside_deploy_spec,
    write_bundle_evidence,
)
from opaihub.proc import no_window_kwargs  # noqa: E402


def _run(command: list[str], *, cwd: Path, timeout: int = 1800) -> None:
    print("+", " ".join(command))
    subprocess.run(  # nosec B603 - generated local build-tool commands only
        command,
        cwd=str(cwd),
        check=True,
        timeout=timeout,
        **no_window_kwargs(),
    )


def _deploy_script(build_python: Path) -> Path:
    executable_dir = build_python.parent
    candidates = [
        executable_dir / "pyside6-deploy.exe",
        executable_dir / "pyside6-deploy",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    discover = subprocess.run(  # nosec B603 - fixed interpreter probe only
        [
            str(build_python),
            "-c",
            (
                "from pathlib import Path; import PySide6; "
                "print(Path(PySide6.__file__).parent / 'scripts' / 'deploy.py')"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
        **no_window_kwargs(),
    )
    fallback = Path(discover.stdout.strip())
    if discover.returncode == 0 and fallback.is_file():
        return fallback
    raise ArtifactReleaseError(
        "PySide6 Deploy was not found in the selected isolated build environment"
    )


def _verify_build_environment(build_python: Path) -> dict[str, str]:
    expected = load_build_pins(ROOT)
    probe = subprocess.run(  # nosec B603 - fixed local interpreter probe only
        [
            str(build_python),
            "-c",
            (
                "import importlib.metadata as m, json; "
                "print(json.dumps({'PySide6': m.version('PySide6'), "
                "'Nuitka': m.version('Nuitka')}))"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
        **no_window_kwargs(),
    )
    if probe.returncode != 0:
        raise ArtifactReleaseError(
            "selected build Python cannot report PySide6 and Nuitka"
        )
    try:
        versions = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactReleaseError(
            "selected build Python returned invalid version data"
        ) from exc
    if versions != expected:
        raise ArtifactReleaseError(
            f"selected build Python versions do not match {expected}: {versions}"
        )
    return versions


def _build_metadata(build_python: Path, *, lock_file: Path | None) -> dict[str, object]:
    """Capture only reproducibility-relevant, non-secret build evidence."""
    probe = subprocess.run(  # nosec B603 - fixed local interpreter probe only
        [
            str(build_python),
            "-c",
            (
                "import importlib.metadata as m, json, platform, sys; "
                "names = ('Nuitka', 'PySide6', 'PyYAML', 'keyring', 'pip', "
                "'setuptools', 'wheel'); "
                "print(json.dumps({'python': {'implementation': "
                "platform.python_implementation(), 'version': "
                "platform.python_version(), 'cache_tag': "
                "sys.implementation.cache_tag}, 'runner': {'system': "
                "platform.system(), 'release': platform.release(), 'machine': "
                "platform.machine()}, 'dependencies': {name: m.version(name) "
                "for name in names}}, sort_keys=True))"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
        **no_window_kwargs(),
    )
    if probe.returncode != 0:
        raise ArtifactReleaseError("selected build Python cannot report build metadata")
    try:
        metadata = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactReleaseError(
            "selected build Python returned invalid build metadata"
        ) from exc
    if not isinstance(metadata, dict):
        raise ArtifactReleaseError(
            "selected build Python returned invalid build metadata"
        )
    if lock_file is not None:
        try:
            contents = lock_file.read_bytes()
        except OSError as exc:
            raise ArtifactReleaseError(
                f"native build lock is unreadable: {lock_file}"
            ) from exc
        metadata["lock"] = {
            "name": lock_file.name,
            "sha256": hashlib.sha256(contents).hexdigest(),
        }
    return metadata


def _prepare_staging(work: Path, specs: DeploymentSpecs) -> DeploymentSpecs:
    staging = work / "staging"
    staging.mkdir(parents=True, exist_ok=False)
    gui_entry = staging / specs.gui.entrypoint.name
    cli_entry = staging / specs.cli.entrypoint.name
    shutil.copy2(specs.gui.entrypoint, gui_entry)
    shutil.copy2(specs.cli.entrypoint, cli_entry)
    return DeploymentSpecs(
        gui=replace(specs.gui, entrypoint=gui_entry),
        cli=replace(specs.cli, entrypoint=cli_entry),
    )


def _component_output(directory: Path, name: str) -> Path:
    candidates = (
        directory / f"{name}.dist",
        directory / f"{name}.app",
        directory / f"{name}.exe",
        directory / name,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise ArtifactReleaseError(f"native build did not produce {name} in {directory}")


def _copy_component(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    if source.is_file():
        shutil.copy2(source, destination / source.name)
        return
    if source.suffix == ".app":
        shutil.copytree(source, destination / source.name)
        return
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def _default_bundle_path(tag: str, channel: str) -> Path:
    platform_name = platform.system().lower()
    return ROOT / "dist" / "desktop" / f"OPai-{tag}-{platform_name}-{channel}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a source-free, portable OPai desktop artifact from an exact tag."
    )
    parser.add_argument(
        "--output-dir", help="Empty destination directory for the bundle"
    )
    parser.add_argument(
        "--build-python",
        default=sys.executable,
        help="Python from the isolated PySide6/Nuitka build environment",
    )
    parser.add_argument(
        "--lock-file",
        help="Reviewed hash lock used to install the isolated build environment",
    )
    parser.add_argument("--allow-untagged", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--channel",
        choices=("unsigned-prealpha", "production"),
        default="unsigned-prealpha",
    )
    args = parser.parse_args()

    try:
        reference = release_ref(ROOT, allow_untagged=bool(args.allow_untagged))
        if args.channel == "production":
            raise ArtifactReleaseError(
                "production artifacts require the signing/notarisation workflow; "
                "this builder only emits unsigned-prealpha evidence"
            )
        destination = (
            Path(args.output_dir).expanduser().resolve()
            if args.output_dir
            else _default_bundle_path(reference.tag, args.channel)
        )
        if destination.exists() and any(destination.iterdir()):
            raise ArtifactReleaseError(
                f"refusing to overwrite non-empty output directory: {destination}"
            )
        build_python = Path(args.build_python).expanduser().resolve()
        lock_file = (
            Path(args.lock_file).expanduser().resolve() if args.lock_file else None
        )
        if lock_file is not None and not lock_file.is_file():
            raise ArtifactReleaseError(f"native build lock is missing: {lock_file}")
        deploy_script = _deploy_script(build_python)
        raw_output = Path(tempfile.gettempdir()) / "opai-artifact-dry-run" / "raw"
        specs = deployment_specs(ROOT, raw_output)

        if args.dry_run:
            commands = build_commands(
                specs,
                build_python=build_python,
                deploy_script=deploy_script,
                spec_dir=raw_output.parent / "specs",
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "release": reference.__dict__,
                        "channel": args.channel,
                        "commands": commands,
                        "gui_spec": render_pyside_deploy_spec(
                            specs.gui, build_python=build_python
                        ),
                    },
                    indent=2,
                )
            )
            return 0

        _verify_build_environment(build_python)
        build_metadata = _build_metadata(build_python, lock_file=lock_file)
        destination.mkdir(parents=True, exist_ok=False)
        with tempfile.TemporaryDirectory(prefix="opai-artifact-build-") as temporary:
            work = Path(temporary)
            raw_specs = deployment_specs(ROOT, work / "raw")
            staged_specs = _prepare_staging(work, raw_specs)
            spec_dir = work / "specs"
            spec_dir.mkdir()
            gui_spec = spec_dir / "gui-pyside6-deploy.spec"
            gui_spec.write_text(
                render_pyside_deploy_spec(staged_specs.gui, build_python=build_python),
                encoding="utf-8",
            )
            gui_command, cli_command = build_commands(
                staged_specs,
                build_python=build_python,
                deploy_script=deploy_script,
                spec_dir=spec_dir,
            )
            _run(gui_command, cwd=ROOT)
            _run(cli_command, cwd=ROOT)
            _copy_component(
                _component_output(staged_specs.gui.output_dir, staged_specs.gui.name),
                destination / "gui",
            )
            _copy_component(
                _component_output(staged_specs.cli.output_dir, staged_specs.cli.name),
                destination / "cli",
            )
        artifact_identity = runtime_identity(
            version=APPLICATION_VERSION,
            build_id=reference.commit,
            channel=RELEASE_CHANNEL,
            platform=platform.system(),
            architecture=platform.machine(),
            install_type=InstallType.PORTABLE,
            package_identity="",
            publisher_identity="",
            assets=asset_manifest(ROOT / "opai" / "assets"),
        )
        (destination / "release-identity.json").write_text(
            json.dumps(artifact_identity, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_bundle_evidence(
            destination,
            reference,
            platform=platform.system().lower(),
            signing_status="unsigned-prealpha",
            build_metadata=build_metadata,
            artifact_identity=artifact_identity,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "bundle": str(destination),
                    "release": reference.__dict__,
                    "channel": args.channel,
                },
                indent=2,
            )
        )
        return 0
    except (ArtifactReleaseError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
