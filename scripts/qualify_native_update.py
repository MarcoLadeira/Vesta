"""Qualify a real signed packaged update on a native GitHub-hosted runner.

This harness deliberately has no mock or skip mode. It installs a genuine prior
package, drives the packaged updater against a staged signed feed, and writes a
report only for checks that actually ran on the host OS.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess  # nosec B404
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from vesta.compatibility import runtime_compatibility_payload
from vesta.update.storage import UpdaterPaths
from vestahub.atomic_io import atomic_write_text, interprocess_transaction
from vestahub.owner_lease import new_lease


class QualificationError(RuntimeError):
    pass


def _embedded_identity(package: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(package) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith("release-identity.json")
            ]
            if len(names) != 1:
                raise QualificationError("package release identity is ambiguous")
            value = json.loads(archive.read(names[0]))
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise QualificationError("package release identity is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise QualificationError("package release identity is invalid")
    return value


def _run(
    command: list[str],
    *,
    timeout: int = 180,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # Qualification commands are fixed and argv-separated.
    return subprocess.run(  # nosec B603
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
        env=env,
    )


def _json_output(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if completed.returncode not in {0, 2, 3}:
        raise QualificationError("packaged CLI did not return a documented exit code")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise QualificationError("packaged CLI did not return JSON") from exc
    if not isinstance(value, dict):
        raise QualificationError("packaged CLI returned a non-object payload")
    return value


@dataclass(frozen=True)
class NativePaths:
    cli: Path
    gui: Path
    identity: Path


class NativeHost:
    def __init__(
        self,
        *,
        package_identity: str,
        publisher_identity: str,
        application: Path,
        baseline_feed_url: str = "",
    ) -> None:
        self.package_identity = package_identity
        self.publisher_identity = publisher_identity
        self.application = application
        self.baseline_feed_url = baseline_feed_url

    @property
    def platform(self) -> str:
        raise NotImplementedError

    @property
    def policy_path(self) -> Path:
        raise NotImplementedError

    def install_baseline(self, package: Path) -> None:
        raise NotImplementedError

    def paths(self) -> NativePaths:
        raise NotImplementedError

    def verify_native(self, package: Path) -> None:
        raise NotImplementedError

    def assert_candidate_installed(self, expected_version: str) -> NativePaths:
        raise NotImplementedError

    def assert_downgrade_rejected(self, baseline: Path) -> None:
        raise NotImplementedError

    def launch_gui(self, gui: Path, workspace: Path) -> subprocess.Popen[bytes]:
        # The executable path comes from the verified installed package.
        return subprocess.Popen(  # nosec B603
            [str(gui), "--project", str(workspace)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop_gui(self) -> None:
        raise NotImplementedError

    def assert_installed_identity(self, *, version: str, build_id: str) -> NativePaths:
        paths = self.paths()
        identity = json.loads(paths.identity.read_text(encoding="utf-8"))
        if identity.get("version") != version or identity.get("build_id") != build_id:
            raise QualificationError(
                "installed package identity does not match expected build"
            )
        return paths


class WindowsHost(NativeHost):
    platform = "windows"

    @property
    def policy_path(self) -> Path:
        return (
            Path(os.environ.get("ProgramData") or "C:/ProgramData")
            / "Vesta"
            / "update-policy.json"
        )

    def _powershell(self, script: str, *arguments: str, timeout: int = 180):
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if not shell:
            raise QualificationError("PowerShell is unavailable")
        return _run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", script, *arguments],
            timeout=timeout,
        )

    def install_baseline(self, package: Path) -> None:
        result = self._powershell(
            "Add-AppxPackage -LiteralPath $args[0] -ForceApplicationShutdown -ErrorAction Stop",
            str(package),
        )
        if result.returncode != 0:
            raise QualificationError("baseline MSIX installation failed")

    def paths(self) -> NativePaths:
        result = self._powershell(
            "$p=Get-AppxPackage -Name $args[0] -ErrorAction Stop; $p.InstallLocation",
            self.package_identity,
        )
        root = Path(result.stdout.strip()) if result.returncode == 0 else Path()
        paths = NativePaths(
            root / "cli" / "vesta.exe",
            root / "gui" / "Vesta.exe",
            root / "release-identity.json",
        )
        if not all(path.is_file() for path in (paths.cli, paths.gui, paths.identity)):
            raise QualificationError("installed MSIX layout is incomplete")
        return paths

    def verify_native(self, package: Path) -> None:
        result = self._powershell(
            "$s=Get-AuthenticodeSignature -LiteralPath $args[0]; "
            "[pscustomobject]@{Status=[string]$s.Status;Subject=[string]$s.SignerCertificate.Subject}|ConvertTo-Json -Compress",
            str(package),
        )
        value = _json_output(result)
        if (
            value.get("Status") != "Valid"
            or value.get("Subject") != self.publisher_identity
        ):
            raise QualificationError("MSIX publisher verification failed")

    def assert_candidate_installed(self, expected_version: str) -> NativePaths:
        paths = self.paths()
        identity = json.loads(paths.identity.read_text(encoding="utf-8"))
        if identity.get("version") != expected_version:
            raise QualificationError("candidate MSIX identity is not installed")
        return paths

    def assert_downgrade_rejected(self, baseline: Path) -> None:
        result = self._powershell(
            "Add-AppxPackage -LiteralPath $args[0] -ForceApplicationShutdown -ErrorAction Stop",
            str(baseline),
        )
        if result.returncode == 0:
            raise QualificationError("Windows accepted an unforced package downgrade")

    def stop_gui(self) -> None:
        self._powershell(
            "Get-Process -Name Vesta -ErrorAction SilentlyContinue | Stop-Process -Force",
            timeout=30,
        )


class MacOSHost(NativeHost):
    platform = "macos"

    @property
    def policy_path(self) -> Path:
        return Path("/Library/Managed Preferences/com.vesta.desktop.update.json")

    def install_baseline(self, package: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="vesta-baseline-") as temporary:
            root = Path(temporary)
            result = _run(["/usr/bin/ditto", "-x", "-k", str(package), str(root)])
            apps = [item for item in root.rglob("*.app") if item.is_dir()]
            if result.returncode != 0 or len(apps) != 1:
                raise QualificationError("baseline Sparkle archive extraction failed")
            self.application.parent.mkdir(parents=True, exist_ok=True)
            if self.application.exists():
                shutil.rmtree(self.application)
            result = _run(["/usr/bin/ditto", str(apps[0]), str(self.application)])
            if result.returncode != 0:
                raise QualificationError(
                    "baseline macOS application installation failed"
                )

    def paths(self) -> NativePaths:
        resources = self.application / "Contents" / "Resources"
        executable = self.application / "Contents" / "MacOS" / "Vesta"
        paths = NativePaths(
            resources / "vesta", executable, resources / "release-identity.json"
        )
        if not all(path.is_file() for path in (paths.cli, paths.gui, paths.identity)):
            raise QualificationError("installed macOS application layout is incomplete")
        return paths

    def verify_native(self, package: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="vesta-native-verify-") as temporary:
            root = Path(temporary)
            if (
                _run(["/usr/bin/ditto", "-x", "-k", str(package), str(root)]).returncode
                != 0
            ):
                raise QualificationError("Sparkle archive extraction failed")
            apps = [item for item in root.rglob("*.app") if item.is_dir()]
            if len(apps) != 1:
                raise QualificationError(
                    "Sparkle archive does not contain exactly one application"
                )
            app = apps[0]
            if (
                _run(
                    ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)]
                ).returncode
                != 0
            ):
                raise QualificationError("macOS code signature verification failed")
            if (
                _run(
                    ["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)]
                ).returncode
                != 0
            ):
                raise QualificationError("macOS Gatekeeper assessment failed")
            details = _run(["/usr/bin/codesign", "-d", "--verbose=4", str(app)])
            if (
                f"TeamIdentifier={self.publisher_identity}"
                not in details.stderr + details.stdout
            ):
                raise QualificationError("macOS Team ID does not match release policy")

    def assert_candidate_installed(self, expected_version: str) -> NativePaths:
        paths = self.paths()
        identity = json.loads(paths.identity.read_text(encoding="utf-8"))
        if identity.get("version") != expected_version:
            raise QualificationError("candidate macOS identity is not installed")
        return paths

    def assert_downgrade_rejected(self, baseline: Path) -> None:
        del baseline
        if not self.baseline_feed_url.startswith("https://"):
            raise QualificationError("baseline Sparkle feed is unavailable")
        paths = self.paths()
        identity = json.loads(paths.identity.read_text(encoding="utf-8"))
        version = str(identity.get("version") or "")
        helper = (
            self.application
            / "Contents"
            / "Resources"
            / "VestaUpdater"
            / "sparkle.app"
            / "Contents"
            / "MacOS"
            / "sparkle"
        )
        if not helper.is_file():
            raise QualificationError("Sparkle external updater is missing")
        result = _run(
            [
                str(helper),
                str(self.application),
                "--application",
                str(self.application),
                "--check-immediately",
                "--feed-url",
                self.baseline_feed_url,
                "--channels",
                "stable",
                "--interactive",
            ],
            timeout=180,
        )
        del result
        time.sleep(10)
        current = json.loads(self.paths().identity.read_text(encoding="utf-8"))
        if current.get("version") != version:
            raise QualificationError("Sparkle downgrade rejection failed")

    def stop_gui(self) -> None:
        _run(["/usr/bin/pkill", "-TERM", "-x", "Vesta"], timeout=30)


@contextmanager
def _managed_feed(path: Path, feed_url: str) -> Iterator[Callable[[str], None]]:
    previous = path.read_bytes() if path.is_file() else None

    def write(url: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "schema_version": 1,
            "feedOverride": url,
            "allowedReleaseChannel": "stable",
            "management_source": "native-release-qualification",
        }
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")

    write(feed_url)
    try:
        yield write
    finally:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)


class Qualification:
    def __init__(
        self,
        *,
        host: NativeHost,
        report: Path,
        artifact_identity: dict[str, Any],
    ) -> None:
        self.host = host
        self.report = report
        self.artifact_identity = artifact_identity
        self.results: list[dict[str, object]] = []

    def prove(self, name: str, action: Callable[[], object]) -> object:
        started = time.monotonic()
        try:
            result = action()
        except Exception as exc:
            self.results.append(
                {
                    "name": name,
                    "status": "failed",
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "error_category": type(exc).__name__,
                }
            )
            self.write(complete=False)
            raise
        self.results.append(
            {
                "name": name,
                "status": "passed",
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        )
        return result

    def write(self, *, complete: bool) -> None:
        self.report.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "artifact_identity": self.artifact_identity,
            "schema_version": 1,
            "platform": self.host.platform,
            "native_execution": True,
            "complete": complete,
            "passed": sum(item["status"] == "passed" for item in self.results),
            "required": 16,
            "scenarios": self.results,
        }
        self.report.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def _state(payload: dict[str, Any]) -> str:
    operation = payload.get("operation")
    if not isinstance(operation, dict):
        operation = (
            payload.get("status", {}).get("operation", {})
            if isinstance(payload.get("status"), dict)
            else {}
        )
    return str(operation.get("state") or "") if isinstance(operation, dict) else ""


def _qualified_candidate_identity(
    package: Path,
    *,
    host: NativeHost,
    expected_version: str,
    expected_build_id: str,
) -> dict[str, Any]:
    """Validate and return the exact immutable identity under qualification."""

    identity = _embedded_identity(package)
    expected_install_type = (
        "windows_msix" if host.platform == "windows" else "macos_sparkle"
    )
    expected = {
        "application_version": expected_version,
        "build_id": expected_build_id,
        "compatibility": runtime_compatibility_payload(),
        "install_type": expected_install_type,
        "package_identity": host.package_identity,
        "platform": host.platform,
        "publisher_identity": host.publisher_identity,
        "version": expected_version,
    }
    mismatched = [
        field
        for field, expected_value in expected.items()
        if identity.get(field) != expected_value
    ]
    assets = identity.get("assets")
    if (
        not isinstance(assets, dict)
        or assets.get("schema_version") != 1
        or assets.get("application_version") != expected_version
        or re.fullmatch(r"[0-9a-f]{64}", str(assets.get("fingerprint_sha256") or ""))
        is None
    ):
        mismatched.append("assets")
    if mismatched:
        raise QualificationError(
            "candidate package identity mismatch: " + ", ".join(sorted(set(mismatched)))
        )
    return identity


def _wait_until(action: Callable[[], Any], *, timeout: int = 300) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = action()
            if value:
                return value
        except (OSError, ValueError, QualificationError, json.JSONDecodeError):
            pass
        time.sleep(3)
    raise QualificationError("native update timed out")


def qualify(
    *,
    host: NativeHost,
    baseline: Path,
    candidate: Path,
    feed_url: str,
    baseline_manifest_url: str,
    expected_version: str,
    expected_build_id: str,
    report: Path,
) -> None:
    candidate_identity = _qualified_candidate_identity(
        candidate,
        host=host,
        expected_version=expected_version,
        expected_build_id=expected_build_id,
    )
    qualification = Qualification(
        host=host,
        report=report,
        artifact_identity=candidate_identity,
    )
    workspace = Path(tempfile.mkdtemp(prefix="vesta-native-qualification-workspace-"))
    qualification.prove(
        "baseline-package-is-present",
        lambda: (
            baseline.stat().st_size > 0 or (_ for _ in ()).throw(QualificationError())
        ),
    )
    qualification.prove(
        "candidate-package-is-present",
        lambda: (
            candidate.stat().st_size > 0 or (_ for _ in ()).throw(QualificationError())
        ),
    )
    qualification.prove(
        "baseline-native-publisher-is-valid", lambda: host.verify_native(baseline)
    )
    qualification.prove(
        "candidate-native-publisher-is-valid", lambda: host.verify_native(candidate)
    )
    qualification.prove(
        "baseline-package-installs", lambda: host.install_baseline(baseline)
    )
    paths = qualification.prove("packaged-cli-and-gui-are-installed", host.paths)
    if not isinstance(paths, NativePaths):
        raise QualificationError("packaged paths did not pass native qualification")

    def cli(*arguments: str, timeout: int = 180) -> tuple[int, dict[str, Any]]:
        completed = _run(
            [str(paths.cli), "--project", str(workspace), *arguments],
            timeout=timeout,
        )
        return completed.returncode, _json_output(completed)

    with _managed_feed(host.policy_path, feed_url) as write_feed:
        _code, initial = cli("update", "status", "--json")
        qualification.prove(
            "packaged-status-reports-native-identity",
            lambda: (
                initial.get("installed", {}).get("install_type")
                == ("windows_msix" if host.platform == "windows" else "macos_sparkle")
                or (_ for _ in ()).throw(QualificationError())
            ),
        )
        code, discovered = cli("update", "check", "--json")
        qualification.prove(
            "signed-feed-discovers-candidate",
            lambda: (
                (code == 3 and _state(discovered) == "available")
                or (_ for _ in ()).throw(QualificationError())
            ),
        )
        updater_paths = UpdaterPaths.for_home(Path.home())

        def prove_operation_lock() -> None:
            with interprocess_transaction(updater_paths.mutex):
                _busy_code, busy = cli("update", "download", "--json")
            if (
                _busy_code != 2
                or busy.get("error_category") != "operation_busy"
                or _state(busy) != "available"
            ):
                raise QualificationError(
                    "packaged updater did not honor the cross-process lock"
                )

        qualification.prove(
            "concurrent-process-cannot-acquire-update-operation",
            prove_operation_lock,
        )
        _code, downloaded = cli("update", "download", "--json", timeout=300)
        qualification.prove(
            "download-hash-and-native-signature-verify",
            lambda: (
                _state(downloaded) == "ready_to_install"
                or (_ for _ in ()).throw(QualificationError())
            ),
        )
        first = cli("update", "status", "--json")[1]
        second = cli("update", "status", "--json")[1]
        qualification.prove(
            "second-process-observes-shared-operation",
            lambda: (
                first.get("operation", {}).get("operation_id")
                == second.get("operation", {}).get("operation_id")
                or (_ for _ in ()).throw(QualificationError())
            ),
        )
        baseline_identity = _embedded_identity(baseline)
        thread_path = workspace / ".vestahub" / "gui" / "thread.json"
        thread_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            thread_path,
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "running",
                    "lease": new_lease(),
                },
                sort_keys=True,
            )
            + "\n",
        )
        _code, blocked = cli("update", "install", "--json")
        qualification.prove(
            "active-work-blocks-native-replacement",
            lambda: (
                (
                    _state(blocked) == "waiting_for_idle"
                    and blocked.get("operation", {}).get("error_category")
                    == "active_work_blocked"
                    and host.assert_installed_identity(
                        version=str(baseline_identity["version"]),
                        build_id=str(baseline_identity["build_id"]),
                    )
                )
                or (_ for _ in ()).throw(QualificationError())
            ),
        )
        thread_path.unlink(missing_ok=True)
        _code, deferred = cli("update", "install", "--on-quit", "--json")
        qualification.prove(
            "install-on-quit-persists-deferred-intent",
            lambda: (
                _state(deferred) == "install_on_quit"
                or (_ for _ in ()).throw(QualificationError())
            ),
        )

        process = qualification.prove(
            "baseline-gui-remains-launchable-before-replacement",
            lambda: host.launch_gui(paths.gui, workspace),
        )
        if not isinstance(process, subprocess.Popen):
            raise QualificationError("baseline GUI process did not launch")
        time.sleep(5)
        process.terminate()
        process.wait(timeout=30)

        _code, installing = cli("update", "install", "--json")
        qualification.prove(
            "native-installer-starts-from-canonical-operation",
            lambda: (
                _state(installing) == "restarting"
                or (_ for _ in ()).throw(QualificationError())
            ),
        )
        candidate_paths = qualification.prove(
            "candidate-version-replaces-baseline",
            lambda: _wait_until(
                lambda: host.assert_candidate_installed(expected_version)
            ),
        )
        if not isinstance(candidate_paths, NativePaths):
            raise QualificationError("candidate package paths are invalid")
        paths = candidate_paths
        candidate_status = qualification.prove(
            "candidate-packaged-cli-starts",
            lambda: cli("update", "status", "--json")[1],
        )
        if not isinstance(candidate_status, dict):
            raise QualificationError("candidate CLI status is invalid")

        completed = qualification.prove(
            "new-gui-confirms-post-update-health",
            lambda: _wait_until(
                lambda: (
                    payload
                    if _state(payload := cli("update", "status", "--json")[1])
                    == "completed"
                    else None
                )
            ),
        )
        if not isinstance(completed, dict):
            raise QualificationError("candidate health result is invalid")

        qualification.prove(
            "native-platform-rejects-downgrade",
            lambda: host.assert_downgrade_rejected(baseline),
        )

        def restore_baseline() -> None:
            nonlocal paths
            host.stop_gui()
            operation = json.loads(updater_paths.operation.read_text(encoding="utf-8"))
            if operation.get("state") != "completed" or not operation.get(
                "last_known_good", {}
            ).get("artifact_path"):
                raise QualificationError("prepared native recovery state is missing")
            operation.update(
                {
                    "state": "rollback_pending",
                    "error_category": "qualification_health_failure",
                    "safe_diagnostic": "Injected native qualification health failure.",
                }
            )
            atomic_write_text(
                updater_paths.operation,
                json.dumps(operation, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
            _rollback_code, rolling = cli("update", "rollback", "--json")
            if _rollback_code != 0 or _state(rolling) != "rolling_back":
                raise QualificationError("native rollback helper did not start")
            restored_paths = _wait_until(
                lambda: host.assert_installed_identity(
                    version=str(baseline_identity["version"]),
                    build_id=str(baseline_identity["build_id"]),
                )
            )
            if not isinstance(restored_paths, NativePaths):
                raise QualificationError("restored native package paths are invalid")
            paths = restored_paths
            _wait_until(
                lambda: (
                    payload
                    if _state(payload := cli("update", "status", "--json")[1])
                    == "rolled_back"
                    else None
                )
            )

        qualification.prove("native-rollback-restores-baseline", restore_baseline)

        write_feed(baseline_manifest_url)
        _code, replayed = cli("update", "check", "--json")
        qualification.prove(
            "replayed-or-mixed-feed-is-rejected",
            lambda: (
                (
                    _state(replayed) == "unavailable"
                    and replayed.get("operation", {}).get("error_category")
                    in {
                        "metadata_rollback",
                        "target_rollback",
                        "mix_and_match_detected",
                    }
                )
                or (_ for _ in ()).throw(QualificationError())
            ),
        )
        write_feed("https://127.0.0.1:9/unreachable/manifest.json")
        _code, offline = cli("update", "check", "--json")
        qualification.prove(
            "offline-check-is-not-reported-up-to-date",
            lambda: (
                _state(offline) == "unavailable"
                or (_ for _ in ()).throw(QualificationError())
            ),
        )
    qualification.write(complete=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows", "macos"), required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--feed-url", required=True)
    parser.add_argument("--baseline-manifest-url", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-build-id", required=True)
    parser.add_argument("--package-identity", required=True)
    parser.add_argument("--publisher-identity", required=True)
    parser.add_argument("--baseline-feed-url", default="")
    parser.add_argument("--application", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if re.fullmatch(r"[0-9a-f]{40}", args.expected_build_id) is None:
        parser.error("--expected-build-id must be an exact lowercase commit SHA")
    if not args.feed_url.startswith("https://"):
        parser.error("--feed-url must use HTTPS")
    if not args.baseline_manifest_url.startswith("https://"):
        parser.error("--baseline-manifest-url must use HTTPS")
    host: NativeHost
    if args.platform == "windows":
        host = WindowsHost(
            package_identity=args.package_identity,
            publisher_identity=args.publisher_identity,
            application=args.application or Path.cwd(),
            baseline_feed_url=args.baseline_feed_url,
        )
    else:
        host = MacOSHost(
            package_identity=args.package_identity,
            publisher_identity=args.publisher_identity,
            application=args.application or Path("/Applications/Vesta.app"),
            baseline_feed_url=args.baseline_feed_url,
        )
    try:
        qualify(
            host=host,
            baseline=args.baseline.resolve(strict=True),
            candidate=args.candidate.resolve(strict=True),
            feed_url=args.feed_url,
            baseline_manifest_url=args.baseline_manifest_url,
            expected_version=args.expected_version,
            expected_build_id=args.expected_build_id,
            report=args.report.resolve(strict=False),
        )
        return 0
    except (
        OSError,
        QualificationError,
        subprocess.SubprocessError,
        ValueError,
        zipfile.BadZipFile,
    ):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
