"""Minimal, bounded HTTPS transport for updater metadata and artifacts."""

from __future__ import annotations

from typing import Any, Callable, Iterable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class ManifestFetchError(RuntimeError):
    pass


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port


def parse_https_origins(
    values: Iterable[object], *, maximum: int = 4
) -> tuple[tuple[str, str, int | None], ...]:
    """Parse a small, exact allowlist of credential-free HTTPS origins."""

    raw = tuple(values)
    if len(raw) > maximum:
        raise ValueError("too many redirect origins")
    origins: list[tuple[str, str, int | None]] = []
    for value in raw:
        parsed = urlsplit(str(value))
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("redirect origin must be an exact HTTPS origin")
        origins.append(("https", parsed.hostname.casefold(), parsed.port))
    return tuple(dict.fromkeys(origins))


class HttpsOnlyRedirectHandler(HTTPRedirectHandler):
    """Reject redirects before credentials/query data can reach another origin."""

    def __init__(
        self, allowed_origins: tuple[tuple[str, str, int | None], ...] = ()
    ) -> None:
        super().__init__()
        self._allowed_origins = frozenset(allowed_origins)

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        target = _origin(newurl)
        if target[0] != "https" or (
            target != _origin(req.full_url) and target not in self._allowed_origins
        ):
            raise HTTPError(req.full_url, code, "unsafe updater redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(HttpsOnlyRedirectHandler())


def open_https(
    request: Request,
    timeout: float,
    *,
    allowed_origins: tuple[tuple[str, str, int | None], ...] = (),
):
    if _origin(request.full_url)[0] != "https":
        raise HTTPError(request.full_url, 400, "HTTPS required", {}, None)
    opener = (
        _OPENER
        if not allowed_origins
        else build_opener(HttpsOnlyRedirectHandler(allowed_origins))
    )
    return opener.open(request, timeout=timeout)


def fetch_manifest(
    url: str,
    *,
    open_url: Callable[[Request, float], Any] | None = None,
    timeout_seconds: float = 20.0,
    allowed_origins: tuple[tuple[str, str, int | None], ...] = (),
) -> bytes:
    if _origin(url)[0] != "https" or not _origin(url)[1]:
        raise ManifestFetchError("feed_url_invalid")
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.vesta.update-manifest+json, application/json"
        },
        method="GET",
    )
    try:
        response = (
            open_url(request, timeout_seconds)
            if open_url is not None
            else open_https(request, timeout_seconds, allowed_origins=allowed_origins)
        )
        with response:
            final_url = str(getattr(response, "geturl", lambda: url)())
            final_origin = _origin(final_url)
            if final_origin[0] != "https" or (
                final_origin != _origin(url) and final_origin not in allowed_origins
            ):
                raise ManifestFetchError("unsafe_feed_redirect")
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None and int(raw_length) > MAX_MANIFEST_BYTES:
                raise ManifestFetchError("feed_too_large")
            payload = response.read(MAX_MANIFEST_BYTES + 1)
    except ManifestFetchError:
        raise
    except (OSError, HTTPError, TimeoutError, TypeError, ValueError):
        raise ManifestFetchError("feed_unavailable") from None
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ManifestFetchError("feed_too_large")
    return payload
