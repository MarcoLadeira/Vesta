"""Structural validation for canonical Vesta release identity projections."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .release_identity import (
    ProjectRelease,
    ReleaseIdentityError,
    read_project_release,
    render_documentation_projection,
)


_GENERATED_FIELDS = {
    "APPLICATION_VERSION": "application_version",
    "RELEASE_CHANNEL": "release_channel",
    "RELEASE_STAGE": "release_stage",
    "DISPLAY_NAME": "display_name",
    "PUBLISHED_TAG": "published_tag",
}
_APPLICATION_IDENTITY_NAMES = frozenset(
    {
        "__version__",
        "__release_stage__",
        "APPLICATION_VERSION",
        "APP_VERSION",
        "VESTA_VERSION",
        "RELEASE_VERSION",
        "RELEASE_CHANNEL",
        "RELEASE_STAGE",
    }
)
_PROJECTION_REMEDIATION = "Run: python scripts/generate_release_identity.py"
_CURRENT_DOCUMENTATION = (
    Path("README.md"),
    Path("docs/INSTALL_PROOF.md"),
    Path("site/index.html"),
)


@dataclass(frozen=True)
class IdentityDrift:
    """One conflicting or undeclared application-identity projection."""

    surface: str
    path: Path
    expected: str
    actual: str
    remediation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "surface": self.surface,
            "path": str(self.path),
            "expected": self.expected,
            "actual": self.actual,
            "remediation": self.remediation,
        }

    def message(self) -> str:
        return (
            f"release identity drift: surface={self.surface} path={self.path} "
            f"expected={self.expected!r} actual={self.actual!r}; "
            f"remediation={self.remediation}"
        )


def _syntax_tree(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ReleaseIdentityError(f"cannot read release projection: {path}") from exc


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets: Iterable[ast.expr]
    if isinstance(node, ast.Assign):
        targets = node.targets
    else:
        targets = (node.target,)
    names = [target.id for target in targets if isinstance(target, ast.Name)]
    return names[0] if len(names) == 1 else None


def read_generated_release(path: Path) -> dict[str, str]:
    """Read the generated module as data without importing target-repo code."""

    values: dict[str, str] = {}
    for node in _syntax_tree(Path(path)).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        name = _assigned_name(node)
        value = node.value
        if (
            name in _GENERATED_FIELDS
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            values[name] = value.value
    missing = sorted(set(_GENERATED_FIELDS).difference(values))
    if missing:
        raise ReleaseIdentityError(
            f"generated release projection is missing: {', '.join(missing)}"
        )
    return values


def _projection_value(path: Path, name: str) -> str:
    try:
        tree = _syntax_tree(path)
    except ReleaseIdentityError:
        return "<unreadable>"
    for node in tree.body:
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and _assigned_name(node) == name
        ):
            value = node.value
            if isinstance(value, ast.Name):
                return value.id
            if isinstance(value, ast.Constant):
                return repr(value.value)
            return ast.dump(value, include_attributes=False)
    return "<missing>"


def _pyproject_duplicate_versions(path: Path) -> list[tuple[str, str]]:
    """Find app-version fields under tool.vesta tables, including on Python 3.10."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    table = ""
    duplicates: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            table = line[1:-1].strip()
            continue
        if not table.startswith("tool.vesta") or "=" not in line or line.startswith("#"):
            continue
        key, raw_value = (part.strip() for part in line.split("=", 1))
        if key in {"version", "release", "application_version", "app_version"}:
            duplicates.append((f"{table}.{key}", raw_value.strip("\"'")))
    return duplicates


def _literal_identity_assignments(root: Path) -> list[tuple[Path, str, str]]:
    findings: list[tuple[Path, str, str]] = []
    generated = (root / "vesta" / "_generated_release.py").resolve()
    independent = {(root / "opcoding" / "__init__.py").resolve()}
    for package in ("vesta", "vestahub", "opcoding"):
        package_root = root / package
        if not package_root.is_dir():
            continue
        for path in package_root.rglob("*.py"):
            resolved = path.resolve()
            if resolved == generated or resolved in independent:
                continue
            try:
                tree = _syntax_tree(resolved)
            except ReleaseIdentityError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                name = _assigned_name(node)
                value = node.value
                if (
                    name in _APPLICATION_IDENTITY_NAMES
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    findings.append((resolved, name, value.value))
    return findings


def _expected_generated(release: ProjectRelease) -> dict[str, str]:
    return {
        "APPLICATION_VERSION": release.application_version,
        "RELEASE_CHANNEL": release.release_channel,
        "RELEASE_STAGE": release.release_stage,
        "DISPLAY_NAME": release.display_name,
        "PUBLISHED_TAG": release.published_tag,
    }


def validate_release_identity(root: Path) -> tuple[IdentityDrift, ...]:
    """Validate canonical input, declared projections, and duplicate-source policy."""

    repository = Path(root).expanduser().resolve()
    pyproject = repository / "pyproject.toml"
    try:
        release = read_project_release(pyproject)
    except ReleaseIdentityError:
        return (
            IdentityDrift(
                surface="canonical.application_version",
                path=pyproject,
                expected="a readable [project].version for project vesta",
                actual="<unreadable canonical release source>",
                remediation="Restore pyproject.toml [project].version.",
            ),
        )
    drifts: list[IdentityDrift] = []
    generated_path = (repository / "vesta" / "_generated_release.py").resolve()
    try:
        actual_generated = read_generated_release(generated_path)
    except ReleaseIdentityError:
        drifts.append(
            IdentityDrift(
                surface="generated.runtime_projection",
                path=generated_path,
                expected="a complete generated release projection",
                actual="<unreadable or incomplete generated projection>",
                remediation=_PROJECTION_REMEDIATION,
            )
        )
        actual_generated = {}
    for constant, expected in _expected_generated(release).items():
        actual = actual_generated.get(constant)
        if actual != expected:
            drifts.append(
                IdentityDrift(
                    surface=f"generated.{_GENERATED_FIELDS[constant]}",
                    path=generated_path,
                    expected=expected,
                    actual=actual or "<missing>",
                    remediation=_PROJECTION_REMEDIATION,
                )
            )

    projections = (
        (
            repository / "vesta" / "__init__.py",
            "__version__",
            "APPLICATION_VERSION",
        ),
        (repository / "vesta" / "__init__.py", "__release_stage__", "RELEASE_STAGE"),
        (
            repository / "vestahub" / "__init__.py",
            "__version__",
            "APPLICATION_VERSION",
        ),
    )
    for path, name, expected in projections:
        actual = _projection_value(path.resolve(), name)
        if actual != expected:
            drifts.append(
                IdentityDrift(
                    surface=f"runtime_projection.{name}",
                    path=path.resolve(),
                    expected=expected,
                    actual=actual,
                    remediation="Import the generated canonical release projection.",
                )
            )

    for field, actual in _pyproject_duplicate_versions(pyproject):
        drifts.append(
            IdentityDrift(
                surface="duplicate_pyproject_application_version",
                path=pyproject,
                expected="[project].version is the only editable application version",
                actual=f"{field}={actual}",
                remediation="Remove the duplicate tool.vesta application-version field.",
            )
        )
    for path, _name, actual in _literal_identity_assignments(repository):
        drifts.append(
            IdentityDrift(
                surface="duplicate_application_version",
                path=path,
                expected="import the generated canonical projection",
                actual=actual,
                remediation=(
                    "Import vesta._generated_release instead of editing another version."
                ),
            )
        )
    for relative in _CURRENT_DOCUMENTATION:
        path = (repository / relative).resolve()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            start = next(
                index
                for index, line in enumerate(lines)
                if line.strip().startswith("<!-- vesta-release-identity:")
            )
            end = lines.index("<!-- /vesta-release-identity -->", start + 1)
            actual_projection = "\n".join(lines[start : end + 1])
        except (OSError, StopIteration, ValueError):
            actual_projection = "<missing>"
        expected_projection = render_documentation_projection(
            release, surface=relative.as_posix()
        )
        if actual_projection != expected_projection:
            drifts.append(
                IdentityDrift(
                    surface=f"documentation.{relative.as_posix()}",
                    path=path,
                    expected=expected_projection,
                    actual=actual_projection,
                    remediation=_PROJECTION_REMEDIATION,
                )
            )
    return tuple(drifts)
