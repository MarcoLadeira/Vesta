"""Fail-closed transport and identity helpers for desktop release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess  # nosec B404 - fixed platform archive tool only
import sys
import tarfile
import tempfile
import tomllib
from typing import Any, Iterable, Mapping
import zipfile


TRANSPORT_ROOT = "bundle"
SIGNED_ARCHIVE_ROOT = "opai-desktop-bundle"
MAX_ARCHIVE_MEMBERS = 100_000
MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RELEASE_TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:(?:a|b|rc)(?:0|[1-9][0-9]*))?$"
)
PEP440_PATTERN = re.compile(
    r"^(?P<base>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*))(?:(?P<kind>a|b|rc)(?P<number>0|[1-9][0-9]*))?$"
)
DUNDER_VERSION_PATTERN = re.compile(
    r'(?m)^\s*__version__\s*=\s*"(?P<version>[^"]+)"\s*$'
)
PLATFORM_PROVENANCE = {
    "windows-latest": "windows",
    "macos-latest": "darwin",
}
IDENTITY_FIELDS = (
    "release_tag",
    "candidate_sha",
    "tag_object_sha",
    "platform",
    "run_id",
    "signed_artifact_id",
    "signed_artifact_name",
    "signed_artifact_attempt",
    "smoke_attempt",
)


class TransportError(RuntimeError):
    """Release transport or identity evidence is unsafe or inconsistent."""


def canonical_release_tag(version: str) -> str:
    """Prefix the repository's canonical PEP 440 version for its release tag."""

    match = PEP440_PATTERN.fullmatch(str(version).strip())
    if match is None:
        raise TransportError(f"unsupported canonical package version: {version!r}")
    suffix = ""
    if match.group("kind") is not None:
        suffix = f"{match.group('kind')}{match.group('number')}"
    return f"v{match.group('base')}{suffix}"


def read_release_versions(root: Path) -> dict[str, str]:
    """Read every authoritative package version without importing the package."""

    repository = root.expanduser().resolve()
    try:
        pyproject = tomllib.loads(
            (repository / "pyproject.toml").read_text(encoding="utf-8")
        )
        project_version = str(pyproject["project"]["version"])
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise TransportError("pyproject.toml has no readable project version") from exc
    versions = {"pyproject.toml": project_version}
    for relative in ("opai/__init__.py", "opaihub/__init__.py"):
        try:
            text = (repository / relative).read_text(encoding="utf-8")
        except OSError as exc:
            raise TransportError(
                f"cannot read canonical version source: {relative}"
            ) from exc
        match = DUNDER_VERSION_PATTERN.search(text)
        if match is None:
            raise TransportError(
                f"canonical version source has no __version__: {relative}"
            )
        versions[relative] = match.group("version")
    return versions


def validate_release_versions(*, release_tag: str, versions: Mapping[str, str]) -> str:
    """Return the canonical package version when every source and tag agree."""

    if RELEASE_TAG_PATTERN.fullmatch(release_tag) is None:
        raise TransportError(
            f"release tag is not a canonical v-prefixed PEP 440 version: {release_tag!r}"
        )
    required = {"pyproject.toml", "opai/__init__.py", "opaihub/__init__.py"}
    if set(versions) != required:
        raise TransportError("canonical package version sources are incomplete")
    unique = {str(value).strip() for value in versions.values()}
    if len(unique) != 1:
        raise TransportError(f"canonical package versions disagree: {dict(versions)!r}")
    version = unique.pop()
    expected_tag = canonical_release_tag(version)
    if release_tag != expected_tag:
        raise TransportError(
            f"release tag {release_tag!r} does not match package version "
            f"{version!r} ({expected_tag})"
        )
    return version


def _archive_path(name: str, *, expected_root: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise TransportError(f"unsafe archive member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise TransportError(f"unsafe archive member path: {name!r}")
    if not path.parts or path.parts[0] != expected_root:
        raise TransportError(f"archive member is outside {expected_root!r}: {name!r}")
    if any(":" in part for part in path.parts):
        raise TransportError(f"unsafe archive member path: {name!r}")
    return path


def _safe_link_target(path: PurePosixPath, target: str, *, expected_root: str) -> None:
    if not target or "\\" in target or "\x00" in target:
        raise TransportError(f"unsafe archive link target: {target!r}")
    link = PurePosixPath(target)
    if link.is_absolute():
        raise TransportError(f"unsafe archive link target: {target!r}")
    parts = list(path.parent.parts)
    for part in link.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if len(parts) <= 1:
                raise TransportError(f"archive link escapes its bundle: {target!r}")
            parts.pop()
        else:
            if ":" in part:
                raise TransportError(f"unsafe archive link target: {target!r}")
            parts.append(part)
    if not parts or parts[0] != expected_root:
        raise TransportError(f"archive link escapes its bundle: {target!r}")


def _archive_member_key(path: PurePosixPath) -> str:
    value = str(path)
    if os.name == "nt" or sys.platform == "darwin":
        return value.casefold()
    return value


def _check_archive_limits(*, member_count: int, uncompressed_bytes: int) -> None:
    if member_count <= 0:
        raise TransportError("archive is empty")
    if member_count > MAX_ARCHIVE_MEMBERS:
        raise TransportError(
            f"archive has too many members ({member_count} > {MAX_ARCHIVE_MEMBERS})"
        )
    if uncompressed_bytes < 0 or uncompressed_bytes > MAX_UNCOMPRESSED_BYTES:
        raise TransportError(
            "archive expands beyond the allowed byte limit "
            f"({uncompressed_bytes} > {MAX_UNCOMPRESSED_BYTES})"
        )


def pack_transport(bundle: Path, archive: Path) -> None:
    """Create a mode- and symlink-preserving tar used only between CI jobs."""

    source = bundle.expanduser().resolve()
    destination = archive.expanduser().resolve()
    if not source.is_dir() or source.is_symlink():
        raise TransportError(f"transport bundle is not a real directory: {source}")
    if destination.exists():
        raise TransportError(f"transport archive already exists: {destination}")
    if source == destination or source in destination.parents:
        raise TransportError("transport archive must be outside the bundle")
    destination.parent.mkdir(parents=True, exist_ok=True)

    def normalize(member: tarfile.TarInfo) -> tarfile.TarInfo:
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise TransportError(f"unsupported transport entry: {member.name}")
        member.uid = 0
        member.gid = 0
        member.uname = ""
        member.gname = ""
        return member

    try:
        with tarfile.open(destination, mode="w", format=tarfile.PAX_FORMAT) as value:
            value.add(source, arcname=TRANSPORT_ROOT, recursive=True, filter=normalize)
    except (OSError, tarfile.TarError) as exc:
        destination.unlink(missing_ok=True)
        raise TransportError(f"could not create release transport: {exc}") from exc


def extract_transport(archive: Path, bundle_output: Path) -> None:
    """Safely extract one trusted transport tar into a new bundle directory."""

    source = archive.expanduser().resolve()
    output = bundle_output.expanduser().resolve()
    if not source.is_file():
        raise TransportError(f"transport archive is missing: {source}")
    if output.exists():
        raise TransportError(f"bundle output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(source, mode="r:") as value:
            members = value.getmembers()
            _check_archive_limits(
                member_count=len(members),
                uncompressed_bytes=sum(member.size for member in members),
            )
            paths: dict[str, PurePosixPath] = {}
            links: set[PurePosixPath] = set()
            for member in members:
                path = _archive_path(member.name, expected_root=TRANSPORT_ROOT)
                key = _archive_member_key(path)
                if key in paths:
                    raise TransportError(
                        f"duplicate transport archive member: {member.name}"
                    )
                paths[key] = path
                if not (
                    member.isfile()
                    or member.isdir()
                    or member.issym()
                    or member.islnk()
                ):
                    raise TransportError(f"unsupported transport entry: {member.name}")
                if member.issym() or member.islnk():
                    links.add(path)
                    _safe_link_target(
                        path, member.linkname, expected_root=TRANSPORT_ROOT
                    )
            for path in paths.values():
                if any(parent in links for parent in path.parents):
                    raise TransportError(
                        f"transport archive entry traverses a link: {path}"
                    )
            with tempfile.TemporaryDirectory(
                prefix="opai-transport-", dir=output.parent
            ) as temporary:
                temporary_path = Path(temporary)
                value.extractall(temporary_path, members=members, filter="data")
                extracted = temporary_path / TRANSPORT_ROOT
                if not extracted.is_dir() or extracted.is_symlink():
                    raise TransportError("transport has no regular bundle root")
                extracted.replace(output)
    except (OSError, tarfile.TarError) as exc:
        raise TransportError(f"could not extract release transport: {exc}") from exc


def extract_signed_zip(archive: Path, bundle_output: Path) -> None:
    """Clean-extract the exact signed ZIP while preserving POSIX execute modes."""

    source = archive.expanduser().resolve()
    output = bundle_output.expanduser().resolve()
    if not source.is_file():
        raise TransportError(f"signed archive is missing: {source}")
    if output.exists():
        raise TransportError(f"bundle output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(source) as value:
            infos = value.infolist()
            _check_archive_limits(
                member_count=len(infos),
                uncompressed_bytes=sum(info.file_size for info in infos),
            )

            def signed_path(name: str) -> PurePosixPath:
                normalized = name.rstrip("/")
                try:
                    return _archive_path(normalized, expected_root=SIGNED_ARCHIVE_ROOT)
                except TransportError:
                    if sys.platform != "darwin":
                        raise
                    metadata = _archive_path(normalized, expected_root="__MACOSX")
                    if len(metadata.parts) < 2 or metadata.parts[1] not in {
                        SIGNED_ARCHIVE_ROOT,
                        f"._{SIGNED_ARCHIVE_ROOT}",
                    }:
                        raise TransportError(
                            f"unsafe macOS archive metadata path: {name!r}"
                        )
                    return metadata

            paths: dict[str, PurePosixPath] = {}
            symlinks: set[PurePosixPath] = set()
            for info in infos:
                path = signed_path(info.filename)
                key = _archive_member_key(path)
                if key in paths:
                    raise TransportError(
                        f"duplicate signed archive member: {info.filename}"
                    )
                paths[key] = path
                mode = info.external_attr >> 16
                if stat.S_IFMT(mode) == stat.S_IFLNK:
                    if path.parts[0] == "__MACOSX":
                        raise TransportError("macOS archive metadata cannot be a link")
                    symlinks.add(path)
                    target = value.read(info).decode("utf-8")
                    _safe_link_target(path, target, expected_root=SIGNED_ARCHIVE_ROOT)
                elif stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise TransportError(
                        f"unsupported signed archive entry: {info.filename}"
                    )
            for path in paths.values():
                if any(parent in symlinks for parent in path.parents):
                    raise TransportError(
                        f"signed archive entry traverses a link: {path}"
                    )

            with tempfile.TemporaryDirectory(
                prefix="opai-signed-", dir=output.parent
            ) as temporary:
                temporary_path = Path(temporary)
                if sys.platform == "darwin":
                    completed = subprocess.run(  # nosec B603 - fixed ditto path/argv
                        [
                            "/usr/bin/ditto",
                            "-x",
                            "-k",
                            str(source),
                            str(temporary_path),
                        ],
                        capture_output=True,
                        check=False,
                        text=True,
                        timeout=300,
                    )
                    if completed.returncode != 0:
                        detail = (completed.stderr or completed.stdout).strip()
                        raise TransportError(f"ditto rejected signed archive: {detail}")
                    extracted = temporary_path / SIGNED_ARCHIVE_ROOT
                    if not extracted.is_dir() or extracted.is_symlink():
                        raise TransportError(
                            "signed archive has no regular bundle root"
                        )
                    extracted.replace(output)
                    return
                for info in infos:
                    path = signed_path(info.filename)
                    destination = temporary_path.joinpath(*path.parts)
                    mode = info.external_attr >> 16
                    file_type = stat.S_IFMT(mode)
                    if info.is_dir() or file_type == stat.S_IFDIR:
                        destination.mkdir(parents=True, exist_ok=True)
                    elif file_type == stat.S_IFLNK:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        os.symlink(value.read(info).decode("utf-8"), destination)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with (
                            value.open(info) as reader,
                            destination.open("xb") as writer,
                        ):
                            shutil.copyfileobj(reader, writer)
                    permissions = stat.S_IMODE(mode)
                    if permissions and os.name != "nt" and not destination.is_symlink():
                        destination.chmod(permissions)
                extracted = temporary_path / SIGNED_ARCHIVE_ROOT
                if not extracted.is_dir() or extracted.is_symlink():
                    raise TransportError("signed archive has no regular bundle root")
                extracted.replace(output)
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        zipfile.BadZipFile,
    ) as exc:
        raise TransportError(f"could not extract signed archive: {exc}") from exc


def verify_bundle_identity(
    bundle: Path, *, release_tag: str, candidate_sha: str, platform: str
) -> dict[str, Any]:
    """Validate archive-internal provenance against trusted workflow identity."""

    if platform not in PLATFORM_PROVENANCE:
        raise TransportError(f"unsupported release platform: {platform!r}")
    try:
        value = json.loads(
            (bundle.expanduser().resolve() / "provenance.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError("bundle provenance is missing or invalid") from exc
    expected = {
        "tag": release_tag,
        "commit": candidate_sha,
        "platform": PLATFORM_PROVENANCE[platform],
    }
    if not isinstance(value, dict) or any(
        value.get(key) != item for key, item in expected.items()
    ):
        raise TransportError(
            "bundle provenance does not match trusted release identity"
        )
    return value


def resolve_artifact(
    inventory: Iterable[Mapping[str, Any]], *, name_prefix: str, current_attempt: int
) -> dict[str, Any]:
    """Resolve the newest non-expired producer artifact available to this attempt."""

    if current_attempt <= 0 or not name_prefix:
        raise TransportError("artifact resolver inputs are invalid")
    pattern = re.compile(re.escape(name_prefix) + r"(?P<attempt>[1-9][0-9]*)$")
    candidates: dict[int, dict[str, Any]] = {}
    for raw in inventory:
        name = str(raw.get("name") or "")
        match = pattern.fullmatch(name)
        if match is None or raw.get("expired") is True:
            continue
        attempt = int(match.group("attempt"))
        if attempt > current_attempt:
            continue
        artifact_id = raw.get("id")
        if not isinstance(artifact_id, int) or artifact_id <= 0:
            raise TransportError(f"artifact has an invalid ID: {name!r}")
        if attempt in candidates:
            raise TransportError(
                f"artifact attempt is ambiguous: {name_prefix}{attempt}"
            )
        candidates[attempt] = {
            "artifact_id": artifact_id,
            "artifact_name": name,
            "producer_attempt": attempt,
        }
    if not candidates:
        raise TransportError(f"no usable same-run artifact matches {name_prefix!r}")
    return candidates[max(candidates)]


def load_artifact_inventory(path: Path) -> list[dict[str, Any]]:
    """Load one or more paginated ``gh api`` JSON response documents."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TransportError("artifact inventory is unreadable") from exc
    decoder = json.JSONDecoder()
    offset = 0
    documents: list[Any] = []
    try:
        while offset < len(text):
            while offset < len(text) and text[offset].isspace():
                offset += 1
            if offset >= len(text):
                break
            document, offset = decoder.raw_decode(text, offset)
            documents.append(document)
    except json.JSONDecodeError as exc:
        raise TransportError("artifact inventory is not valid JSON") from exc
    artifacts: list[dict[str, Any]] = []
    for document in documents:
        pages = document if isinstance(document, list) else [document]
        for page in pages:
            if not isinstance(page, dict) or not isinstance(
                page.get("artifacts"), list
            ):
                raise TransportError("artifact inventory response has an invalid shape")
            for artifact in page["artifacts"]:
                if not isinstance(artifact, dict):
                    raise TransportError("artifact inventory contains a non-object")
                artifacts.append(artifact)
    if not documents:
        raise TransportError("artifact inventory is empty")
    return artifacts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_identity(identity: Mapping[str, Any]) -> dict[str, str]:
    normalized = {field: str(identity.get(field) or "") for field in IDENTITY_FIELDS}
    if RELEASE_TAG_PATTERN.fullmatch(normalized["release_tag"]) is None:
        raise TransportError("smoke identity has an invalid release tag")
    for field in ("candidate_sha", "tag_object_sha"):
        if SHA_PATTERN.fullmatch(normalized[field]) is None:
            raise TransportError(f"smoke identity has an invalid {field}")
    if normalized["platform"] not in PLATFORM_PROVENANCE:
        raise TransportError("smoke identity has an invalid platform")
    for field in (
        "run_id",
        "signed_artifact_id",
        "signed_artifact_attempt",
        "smoke_attempt",
    ):
        if not normalized[field].isdigit() or int(normalized[field]) <= 0:
            raise TransportError(f"smoke identity has an invalid {field}")
    if int(normalized["signed_artifact_attempt"]) > int(normalized["smoke_attempt"]):
        raise TransportError("signed artifact attempt cannot follow its smoke attempt")
    if not normalized["signed_artifact_name"].strip():
        raise TransportError("smoke identity has no signed artifact name")
    return normalized


def bind_smoke_evidence(
    *, raw_report: Path, archive: Path, output: Path, identity: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind a successful native smoke report to exact archive/workflow identity."""

    try:
        report = json.loads(raw_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError("native smoke report is missing or invalid") from exc
    if not isinstance(report, dict) or report.get("ok") is not True:
        raise TransportError("native artifact smoke did not qualify")
    source = archive.expanduser().resolve()
    if not source.is_file():
        raise TransportError("signed archive is missing")
    value = {
        "schema_version": 1,
        "verdict": "qualified",
        "archive": {"name": source.name, "sha256": _sha256(source)},
        "identity": _normalize_identity(identity),
        "smoke": report,
    }
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return value


def verify_smoke_evidence(
    *, evidence: Path, archive: Path, expected_identity: Mapping[str, Any]
) -> dict[str, Any]:
    """Fail unless smoke evidence names these exact immutable archive bytes."""

    try:
        value = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError("bound smoke evidence is missing or invalid") from exc
    source = archive.expanduser().resolve()
    expected_archive = {"name": source.name, "sha256": _sha256(source)}
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("verdict") != "qualified"
        or not isinstance(value.get("smoke"), dict)
        or value["smoke"].get("ok") is not True
        or value.get("archive") != expected_archive
        or value.get("identity") != _normalize_identity(expected_identity)
    ):
        raise TransportError("smoke evidence does not match the exact release archive")
    return value


def _write_github_outputs(path: Path, values: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for key, raw in values.items():
            value = str(raw)
            if "\n" in value or "\r" in value:
                raise TransportError(f"workflow output is not single-line: {key}")
            output.write(f"{key}={value}\n")


def _identity_from_args(args: argparse.Namespace) -> dict[str, str]:
    return {
        "release_tag": args.release_tag,
        "candidate_sha": args.candidate_sha,
        "tag_object_sha": args.tag_object_sha,
        "platform": args.platform,
        "run_id": args.run_id,
        "signed_artifact_id": args.signed_artifact_id,
        "signed_artifact_name": args.signed_artifact_name,
        "signed_artifact_attempt": args.signed_artifact_attempt,
        "smoke_attempt": args.smoke_attempt,
    }


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--tag-object-sha", required=True)
    parser.add_argument("--platform", required=True, choices=tuple(PLATFORM_PROVENANCE))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--signed-artifact-id", required=True)
    parser.add_argument("--signed-artifact-name", required=True)
    parser.add_argument("--signed-artifact-attempt", required=True)
    parser.add_argument("--smoke-attempt", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-release-tag")
    validate.add_argument("--root", type=Path, default=Path.cwd())
    validate.add_argument("--release-tag", required=True)
    validate.add_argument("--github-output", type=Path, required=True)

    pack = commands.add_parser("pack-transport")
    pack.add_argument("--bundle", type=Path, required=True)
    pack.add_argument("--archive", type=Path, required=True)

    extract = commands.add_parser("extract-transport")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--bundle-output", type=Path, required=True)

    extract_zip = commands.add_parser("extract-signed-zip")
    extract_zip.add_argument("--archive", type=Path, required=True)
    extract_zip.add_argument("--bundle-output", type=Path, required=True)

    resolve = commands.add_parser("resolve-artifact")
    resolve.add_argument("--inventory", type=Path, required=True)
    resolve.add_argument("--name-prefix", required=True)
    resolve.add_argument("--current-attempt", type=int, required=True)
    resolve.add_argument("--github-output", type=Path, required=True)

    verify_bundle = commands.add_parser("verify-bundle-identity")
    verify_bundle.add_argument("--bundle", type=Path, required=True)
    verify_bundle.add_argument("--release-tag", required=True)
    verify_bundle.add_argument("--candidate-sha", required=True)
    verify_bundle.add_argument("--platform", required=True)

    bind = commands.add_parser("bind-smoke-evidence")
    bind.add_argument("--raw-report", type=Path, required=True)
    bind.add_argument("--archive", type=Path, required=True)
    bind.add_argument("--output", type=Path, required=True)
    _add_identity_arguments(bind)

    verify_smoke = commands.add_parser("verify-smoke-evidence")
    verify_smoke.add_argument("--evidence", type=Path, required=True)
    verify_smoke.add_argument("--archive", type=Path, required=True)
    _add_identity_arguments(verify_smoke)

    args = parser.parse_args(argv)
    try:
        if args.command == "validate-release-tag":
            version = validate_release_versions(
                release_tag=args.release_tag,
                versions=read_release_versions(args.root),
            )
            _write_github_outputs(
                args.github_output,
                {
                    "package_version": version,
                    "canonical_release_tag": canonical_release_tag(version),
                },
            )
        elif args.command == "pack-transport":
            pack_transport(args.bundle, args.archive)
        elif args.command == "extract-transport":
            extract_transport(args.archive, args.bundle_output)
        elif args.command == "extract-signed-zip":
            extract_signed_zip(args.archive, args.bundle_output)
        elif args.command == "resolve-artifact":
            resolved = resolve_artifact(
                load_artifact_inventory(args.inventory),
                name_prefix=args.name_prefix,
                current_attempt=args.current_attempt,
            )
            _write_github_outputs(args.github_output, resolved)
        elif args.command == "verify-bundle-identity":
            verify_bundle_identity(
                args.bundle,
                release_tag=args.release_tag,
                candidate_sha=args.candidate_sha,
                platform=args.platform,
            )
        elif args.command == "bind-smoke-evidence":
            bind_smoke_evidence(
                raw_report=args.raw_report,
                archive=args.archive,
                output=args.output,
                identity=_identity_from_args(args),
            )
        elif args.command == "verify-smoke-evidence":
            verify_smoke_evidence(
                evidence=args.evidence,
                archive=args.archive,
                expected_identity=_identity_from_args(args),
            )
        else:  # pragma: no cover - argparse owns the closed command set
            raise TransportError(f"unsupported command: {args.command}")
    except TransportError as exc:
        print(f"desktop release transport failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
