from __future__ import annotations

import hashlib
import io
from pathlib import Path
from urllib.error import HTTPError

import pytest

from vesta.update.download import DownloadError, SecureDownloader
from vesta.update.models import InstallType, UpdateCandidate


CONTENT = b"0123456789abcdef"


def _candidate(**overrides: object) -> UpdateCandidate:
    values: dict[str, object] = {
        "version": "0.3.0",
        "build_id": "b" * 40,
        "channel": "stable",
        "release_id": "v0.3.0",
        "published_at": "2026-08-14T10:00:00Z",
        "platform": "windows",
        "architecture": "x86_64",
        "install_type": InstallType.WINDOWS_MSIX,
        "artifact_url": "https://updates.example.test/v0.3.0/Vesta.msix",
        "artifact_sha256": hashlib.sha256(CONTENT).hexdigest(),
        "artifact_size": len(CONTENT),
        "publisher_identity": "CN=Vesta",
        "metadata_key_ids": ("root-1",),
    }
    values.update(overrides)
    return UpdateCandidate(**values)


class Response(io.BytesIO):
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str = "",
    ):
        super().__init__(body)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(body))}
        self._url = url

    def geturl(self) -> str:
        return self._url or "https://updates.example.test/v0.3.0/Vesta.msix"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_secure_download_is_operation_scoped_and_hash_verified(tmp_path: Path):
    requests = []
    downloader = SecureDownloader(
        open_url=lambda request, timeout: requests.append(request) or Response(CONTENT)
    )

    target = downloader.download(_candidate(), tmp_path, "op-1", lambda *_: None)

    assert target.read_bytes() == CONTENT
    assert target.is_relative_to(tmp_path)
    assert target.parent.name == "op-1"
    assert requests[0].full_url.startswith("https://")


def test_secure_download_resumes_with_range_when_server_supports_it(tmp_path: Path):
    operation = tmp_path / "op-1"
    operation.mkdir()
    (operation / "Vesta.msix.part").write_bytes(CONTENT[:5])
    ranges: list[str] = []

    def open_url(request, timeout):
        ranges.append(request.headers.get("Range", ""))
        return Response(
            CONTENT[5:],
            status=206,
            headers={
                "Content-Length": str(len(CONTENT) - 5),
                "Content-Range": f"bytes 5-{len(CONTENT) - 1}/{len(CONTENT)}",
            },
        )

    target = SecureDownloader(open_url=open_url).download(
        _candidate(), tmp_path, "op-1", lambda *_: None
    )

    assert ranges == ["bytes=5-"]
    assert target.read_bytes() == CONTENT


def test_server_ignoring_range_restarts_cleanly_instead_of_appending(tmp_path: Path):
    operation = tmp_path / "op-1"
    operation.mkdir()
    (operation / "Vesta.msix.part").write_bytes(CONTENT[:5])

    target = SecureDownloader(
        open_url=lambda *_: Response(CONTENT, status=200)
    ).download(_candidate(), tmp_path, "op-1", lambda *_: None)

    assert target.read_bytes() == CONTENT


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"tampered-content", "artifact_hash_mismatch"),
        (b"x" * len(CONTENT), "artifact_hash_mismatch"),
        (CONTENT[:-1], "artifact_size_mismatch"),
    ],
)
def test_corrupt_or_truncated_download_never_becomes_verified(
    tmp_path: Path, body: bytes, expected: str
):
    downloader = SecureDownloader(open_url=lambda *_: Response(body))

    with pytest.raises(DownloadError, match=expected):
        downloader.download(_candidate(), tmp_path, "op-1", lambda *_: None)

    assert not list(tmp_path.rglob("*.verified"))


def test_http_failure_is_typed_without_leaking_signed_url(tmp_path: Path):
    def fail(request, timeout):
        raise HTTPError(request.full_url, 503, "sig=secret", {}, None)

    with pytest.raises(DownloadError, match="download_interrupted") as raised:
        SecureDownloader(open_url=fail).download(
            _candidate(), tmp_path, "op-1", lambda *_: None
        )

    assert "secret" not in str(raised.value)


def test_operation_id_cannot_escape_download_root(tmp_path: Path):
    downloader = SecureDownloader(open_url=lambda *_: Response(CONTENT))

    with pytest.raises(DownloadError, match="unsafe_staging_path"):
        downloader.download(_candidate(), tmp_path, "../escape", lambda *_: None)


def test_symlinked_download_root_is_rejected(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("this Windows account cannot create symlinks")

    with pytest.raises(DownloadError, match="unsafe_staging_path"):
        SecureDownloader(open_url=lambda *_: Response(CONTENT)).download(
            _candidate(), link, "op-1", lambda *_: None
        )


def test_staging_rejects_source_substitution_after_download(tmp_path: Path):
    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    downloader = SecureDownloader(open_url=lambda *_: Response(CONTENT))
    source = downloader.download(_candidate(), downloads, "op-1", lambda *_: None)
    source.write_bytes(b"x" * len(CONTENT))

    with pytest.raises(DownloadError, match="artifact_hash_mismatch"):
        downloader.stage(source, _candidate(), staging, "op-1")


def test_signed_redirect_origin_allows_verified_release_asset_download(tmp_path: Path):
    candidate = _candidate(
        artifact_url="https://github.com/owner/repo/releases/download/v0.3.0/Vesta.msix",
        native={
            "allowed_redirect_origins": ["https://release-assets.githubusercontent.com"]
        },
    )
    response = Response(
        CONTENT,
        url="https://release-assets.githubusercontent.com/object?opaque=1",
    )

    target = SecureDownloader(open_url=lambda *_: response).download(
        candidate, tmp_path, "op-1", lambda *_: None
    )

    assert target.read_bytes() == CONTENT


@pytest.mark.parametrize(
    "origin",
    [
        "http://release-assets.githubusercontent.com",
        "https://user:pass@example.test",
        "https://example.test/path",
    ],
)
def test_malformed_signed_redirect_origin_is_rejected(tmp_path: Path, origin: str):
    candidate = _candidate(native={"allowed_redirect_origins": [origin]})
    with pytest.raises(DownloadError, match="unsafe_artifact_redirect"):
        SecureDownloader(open_url=lambda *_: Response(CONTENT)).download(
            candidate, tmp_path, "op-1", lambda *_: None
        )
