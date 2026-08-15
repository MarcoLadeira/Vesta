"""HTTPS download, resume, digest verification, and contained staging."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request

from .models import UpdateCandidate
from .network import open_https, parse_https_origins


class DownloadError(RuntimeError):
    def __init__(self, category: str, *, retriable: bool = False) -> None:
        self.category = category
        self.retriable = retriable
        super().__init__(category)


OpenUrl = Callable[[Request, float], Any]
Progress = Callable[[int, int], None]
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _default_open(request: Request, timeout: float):
    return open_https(request, timeout)


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _safe_directory(root: Path) -> Path:
    raw = root.expanduser()
    if raw.exists() and _linked(raw):
        raise DownloadError("unsafe_staging_path")
    resolved = raw.resolve(strict=False)
    resolved.mkdir(parents=True, exist_ok=True)
    if _linked(resolved):
        raise DownloadError("unsafe_staging_path")
    return resolved


def _operation_directory(root: Path, operation_id: str) -> Path:
    if _SAFE_COMPONENT.fullmatch(str(operation_id)) is None:
        raise DownloadError("unsafe_staging_path")
    directory = root / operation_id
    if directory.exists() and _linked(directory):
        raise DownloadError("unsafe_staging_path")
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise DownloadError("unsafe_staging_path") from exc
    return directory


def _artifact_name(candidate: UpdateCandidate) -> str:
    name = unquote(Path(urlsplit(candidate.artifact_url).path).name)
    if _SAFE_COMPONENT.fullmatch(name) is None or name in {".", ".."}:
        raise DownloadError("unsafe_staging_path")
    return name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _allowed_redirect_origins(
    candidate: UpdateCandidate,
) -> tuple[tuple[str, str, int | None], ...]:
    values = candidate.native.get("allowed_redirect_origins") or []
    if not isinstance(values, list):
        raise DownloadError("unsafe_artifact_redirect")
    try:
        return parse_https_origins(values)
    except ValueError as exc:
        raise DownloadError("unsafe_artifact_redirect") from exc


class SecureDownloader:
    def __init__(
        self, *, open_url: OpenUrl | None = None, timeout_seconds: float = 60.0
    ):
        self._open_url = open_url
        self._timeout_seconds = timeout_seconds

    def download(
        self,
        candidate: UpdateCandidate,
        root: Path,
        operation_id: str,
        progress: Progress,
    ) -> Path:
        download_root = _safe_directory(root)
        directory = _operation_directory(download_root, operation_id)
        name = _artifact_name(candidate)
        partial = directory / f"{name}.part"
        verified = directory / f"{name}.verified"
        for path in (partial, verified):
            if path.exists() and _linked(path):
                raise DownloadError("unsafe_staging_path")
        if verified.is_file():
            if (
                verified.stat().st_size == candidate.artifact_size
                and _sha256(verified) == candidate.artifact_sha256
            ):
                return verified
            verified.unlink(missing_ok=True)
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > candidate.artifact_size:
            partial.unlink(missing_ok=True)
            offset = 0
        remaining = candidate.artifact_size - offset
        if shutil.disk_usage(download_root).free < remaining + 8 * 1024 * 1024:
            raise DownloadError("insufficient_disk_space", retriable=True)
        request = Request(candidate.artifact_url, method="GET")
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        try:
            allowed_origins = _allowed_redirect_origins(candidate)
            response = (
                self._open_url(request, self._timeout_seconds)
                if self._open_url is not None
                else open_https(
                    request,
                    self._timeout_seconds,
                    allowed_origins=allowed_origins,
                )
            )
            with response:
                final_url = str(
                    getattr(response, "geturl", lambda: candidate.artifact_url)()
                )
                if final_url != candidate.artifact_url:
                    from urllib.parse import urlsplit

                    source = urlsplit(candidate.artifact_url)
                    final = urlsplit(final_url)
                    final_origin = (
                        final.scheme.casefold(),
                        (final.hostname or "").casefold(),
                        final.port,
                    )
                    source_origin = (
                        source.scheme.casefold(),
                        (source.hostname or "").casefold(),
                        source.port,
                    )
                    if final_origin[0] != "https" or (
                        final_origin != source_origin
                        and final_origin not in allowed_origins
                    ):
                        raise DownloadError("unsafe_artifact_redirect")
                status = int(getattr(response, "status", 200))
                append = offset > 0 and status == 206
                if append:
                    content_range = str(response.headers.get("Content-Range") or "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise DownloadError("download_range_mismatch", retriable=True)
                else:
                    offset = 0
                mode = "ab" if append else "wb"
                written = offset
                with partial.open(mode) as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        written += len(chunk)
                        if written > candidate.artifact_size:
                            raise DownloadError("artifact_size_mismatch")
                        progress(written, candidate.artifact_size)
                    output.flush()
                    os.fsync(output.fileno())
        except DownloadError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError):
            raise DownloadError("download_interrupted", retriable=True) from None
        if partial.stat().st_size != candidate.artifact_size:
            raise DownloadError("artifact_size_mismatch")
        if _sha256(partial) != candidate.artifact_sha256:
            raise DownloadError("artifact_hash_mismatch")
        os.replace(partial, verified)
        return verified

    def stage(
        self,
        source: Path,
        candidate: UpdateCandidate,
        root: Path,
        operation_id: str,
    ) -> Path:
        staging_root = _safe_directory(root)
        directory = _operation_directory(staging_root, operation_id)
        if not source.is_file() or _linked(source):
            raise DownloadError("unsafe_staging_path")
        if source.stat().st_size != candidate.artifact_size:
            raise DownloadError("artifact_size_mismatch")
        if _sha256(source) != candidate.artifact_sha256:
            raise DownloadError("artifact_hash_mismatch")
        target = directory / _artifact_name(candidate)
        if target.exists() and _linked(target):
            raise DownloadError("unsafe_staging_path")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=directory,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                with source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if _sha256(temporary) != candidate.artifact_sha256:
                raise DownloadError("artifact_hash_mismatch")
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target
