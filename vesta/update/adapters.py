"""Platform adapter contract; platform mechanics do not own product state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .models import InstalledBuild, UpdateCandidate


@dataclass(frozen=True)
class AdapterVerification:
    verified: bool
    publisher_identity: str = ""
    evidence: str = ""
    error_category: str = ""


@dataclass(frozen=True)
class AdapterInstallResult:
    started: bool
    transaction_id: str = ""
    error_category: str = ""
    result_path: str = ""


class UpdateAdapter(Protocol):
    name: str

    def doctor(self, installed: InstalledBuild) -> dict[str, object]: ...

    def verify(
        self, artifact: Path, candidate: UpdateCandidate
    ) -> AdapterVerification: ...

    def install(
        self, artifact: Path, candidate: UpdateCandidate, *, mode: str
    ) -> AdapterInstallResult: ...

    def rollback(self, last_known_good: Mapping[str, Any]) -> AdapterInstallResult: ...


class UnsupportedUpdateAdapter:
    """Truthful adapter for portable, unknown, and source installations."""

    def __init__(self, name: str, remediation: str) -> None:
        self.name = name
        self.remediation = remediation

    def doctor(self, installed: InstalledBuild) -> dict[str, object]:
        return {
            "available": False,
            "name": self.name,
            "install_type": installed.install_type.value,
            "remediation": self.remediation,
        }

    def verify(self, artifact: Path, candidate: UpdateCandidate) -> AdapterVerification:
        return AdapterVerification(False, error_category="unsupported_install_type")

    def install(
        self, artifact: Path, candidate: UpdateCandidate, *, mode: str
    ) -> AdapterInstallResult:
        return AdapterInstallResult(False, error_category="unsupported_install_type")

    def rollback(self, last_known_good: Mapping[str, Any]) -> AdapterInstallResult:
        return AdapterInstallResult(False, error_category="rollback_unavailable")


class DeveloperGitUpdateAdapter(UnsupportedUpdateAdapter):
    """Explicit source-only compatibility path; never selected for packages."""

    def __init__(self, source_root: Path) -> None:
        super().__init__(
            "developer-git-release",
            "Developer source installation: update deliberately from a signed release tag.",
        )
        self.source_root = source_root.expanduser().resolve(strict=False)

    def check_source(self, *, force: bool = True) -> dict[str, object]:
        from vesta.updater import check_for_update

        return check_for_update(self.source_root, branch="main", force=force)

    def apply_source(self, *, force: bool = False, progress=None) -> dict[str, object]:
        from vesta.updater import apply_update

        return apply_update(
            self.source_root, branch="main", force=force, progress=progress
        )
