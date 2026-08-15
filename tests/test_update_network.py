from __future__ import annotations

import io
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from opai.update.network import (
    HttpsOnlyRedirectHandler,
    ManifestFetchError,
    fetch_manifest,
)


class Response(io.BytesIO):
    def __init__(self, body: bytes, *, url: str, length: int | None = None):
        super().__init__(body)
        self._url = url
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


@pytest.mark.parametrize(
    "target",
    [
        "http://updates.example.test/manifest.json",
        "https://attacker.example/manifest.json",
    ],
)
def test_manifest_redirect_cannot_downgrade_or_change_origin(target: str):
    handler = HttpsOnlyRedirectHandler()
    request = Request("https://updates.example.test/stable/manifest.json")

    with pytest.raises(HTTPError):
        handler.redirect_request(request, None, 302, "moved", {}, target)


def test_manifest_fetch_is_bounded_before_and_during_read():
    with pytest.raises(ManifestFetchError, match="feed_too_large"):
        fetch_manifest(
            "https://updates.example.test/stable/manifest.json",
            open_url=lambda *_: Response(
                b"{}",
                url="https://updates.example.test/stable/manifest.json",
                length=5_000_000,
            ),
        )


def test_manifest_fetch_accepts_only_same_origin_https_response():
    body = b'{"signed":{}}'
    result = fetch_manifest(
        "https://updates.example.test/stable/manifest.json",
        open_url=lambda *_: Response(
            body, url="https://updates.example.test/stable/manifest.json"
        ),
    )

    assert result == body


def test_manifest_fetch_accepts_only_a_package_pinned_redirect_origin():
    body = b'{"signed":{}}'
    allowed = (("https", "release-assets.githubusercontent.com", None),)

    result = fetch_manifest(
        "https://github.com/owner/repo/releases/download/feed/manifest.json",
        open_url=lambda *_: Response(
            body, url="https://release-assets.githubusercontent.com/object?opaque=1"
        ),
        allowed_origins=allowed,
    )

    assert result == body


def test_manifest_fetch_rejects_an_unpinned_cross_origin_response():
    with pytest.raises(ManifestFetchError, match="unsafe_feed_redirect"):
        fetch_manifest(
            "https://github.com/owner/repo/releases/download/feed/manifest.json",
            open_url=lambda *_: Response(b"{}", url="https://attacker.example/object"),
            allowed_origins=(("https", "release-assets.githubusercontent.com", None),),
        )


def test_artifact_handler_allows_only_an_explicit_https_redirect_origin():
    handler = HttpsOnlyRedirectHandler(
        (("https", "release-assets.githubusercontent.com", None),)
    )
    request = Request("https://github.com/owner/repo/releases/download/v1/OPai.msix")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "moved",
        {},
        "https://release-assets.githubusercontent.com/object?token=opaque",
    )

    assert redirected is not None
    assert redirected.full_url.startswith(
        "https://release-assets.githubusercontent.com/"
    )
