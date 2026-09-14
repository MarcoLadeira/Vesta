"""Persist pasted, dropped, and picked images so a prompt can point at them.

Vesta already has one way to put a file in front of a model: a workspace
relative path, prepended to the prompt as an ``@`` reference. The provider
CLIs read that path themselves. The composer's own note says it --
"Vesta links to your files -- it sends their location, not their contents."

An image pasted from the clipboard has no location. It is bytes in a
``DataTransfer``, and a screenshot has never been a file at all. So the only
new thing needed is somewhere to put those bytes; once written, an image is an
ordinary context reference and every provider path downstream is unchanged.

What this module refuses to do matters more than what it does. It is the
boundary where untrusted bytes and an untrusted filename arrive from a web
context, so:

* the declared media type is not trusted -- the bytes are sniffed, and a
  ``.png`` that is really a zip is rejected;
* the supplied filename is never used to build a path, only to pick a label,
  so ``../../.ssh/authorized_keys`` has no destination to influence;
* every write is proved to land inside the attachments directory after
  resolution, so a symlinked directory cannot redirect it;
* size and count are capped, because an unbounded paste is both a memory
  problem here and a bill downstream.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

# Sniffed from the leading bytes. The mapping is deliberately small: these are
# the formats a person actually pastes, and every one of them is something the
# provider CLIs can read. An unknown signature is rejected rather than guessed.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)
_WEBP_PREFIX = b"RIFF"
_WEBP_TAG = b"WEBP"

# 10 MB. Large enough for a full-resolution screenshot on a 5K display, small
# enough that a mis-paste cannot exhaust memory or a token budget.
MAX_BYTES = 10 * 1024 * 1024
# Per message. Someone attaching more than this wants a folder, not a paste.
MAX_PER_MESSAGE = 8
# Attachments older than this are swept on the next write. A chat's images are
# only interesting while the conversation is; keeping them forever silently
# grows a directory nobody ever looks at.
RETENTION_SECONDS = 7 * 24 * 60 * 60

ATTACHMENT_DIRNAME = "attachments"


class AttachmentError(Exception):
    """A rejection carrying a message that is safe to show the user."""


@dataclass(frozen=True)
class StoredImage:
    """An image on disk, named the only way the browser is allowed to see it."""

    relative_path: str
    name: str
    bytes_written: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "name": self.name,
            "bytes": self.bytes_written,
        }


def sniff_extension(payload: bytes) -> str | None:
    """The file extension the *bytes* justify, or ``None``.

    Deliberately ignores whatever the caller claimed. A browser will report
    ``image/png`` for anything a page hands it, and the whole point of this
    function is that the claim is not evidence.
    """

    for signature, suffix in _SIGNATURES:
        if payload.startswith(signature):
            return suffix
    if payload[:4] == _WEBP_PREFIX and payload[8:12] == _WEBP_TAG:
        return ".webp"
    return None


def attachments_dir(root: Path) -> Path:
    """Where images for this workspace live: ``.opaihub/attachments``.

    Inside the workspace because a context reference is workspace-relative,
    and under ``.opaihub`` because that directory is already Vesta's and
    already ignored by git -- an attachment must never turn up in a diff.
    """

    return Path(root).expanduser().resolve() / ".opaihub" / ATTACHMENT_DIRNAME


def _decode(data: str) -> bytes:
    """Decode a base64 payload, with or without a ``data:`` URL wrapper."""

    text = str(data or "").strip()
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    try:
        # validate=True so stray characters are an error rather than silently
        # skipped -- silent skipping turns corrupt input into corrupt output.
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError("That image could not be read.") from exc


def prune(root: Path, *, now: float | None = None) -> int:
    """Delete attachments past their retention. Returns how many went.

    Best effort by design: a file that cannot be removed (open in a viewer,
    say) is left alone rather than turned into an error on a paste that has
    nothing to do with it.
    """

    directory = attachments_dir(root)
    if not directory.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    removed = 0
    for entry in directory.iterdir():
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def store_image(
    root: Path,
    data: str,
    *,
    name: str = "",
    now: float | None = None,
) -> StoredImage:
    """Write one pasted or dropped image and return its context reference.

    ``name`` becomes a label and nothing else; the path is generated, so a
    hostile filename has no destination to influence.
    """

    payload = _decode(data)
    if not payload:
        raise AttachmentError("That image was empty.")
    if len(payload) > MAX_BYTES:
        megabytes = MAX_BYTES // (1024 * 1024)
        raise AttachmentError(f"Images must be {megabytes} MB or smaller.")
    suffix = sniff_extension(payload)
    if suffix is None:
        raise AttachmentError("That file is not an image Vesta can send.")

    directory = attachments_dir(root)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AttachmentError("Vesta could not save the image.") from exc
    prune(root, now=now)

    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    target = directory / f"{stamp}-{secrets.token_hex(4)}{suffix}"
    # The name is generated, so this cannot fail -- which is exactly why it is
    # checked. A containment proof that only runs when it might fail is a
    # containment proof that stops running the day the naming changes.
    try:
        resolved = target.resolve()
        resolved.relative_to(directory)
    except (OSError, ValueError) as exc:
        raise AttachmentError("Vesta could not save the image.") from exc
    try:
        resolved.write_bytes(payload)
    except OSError as exc:
        raise AttachmentError("Vesta could not save the image.") from exc

    workspace = Path(root).expanduser().resolve()
    return StoredImage(
        relative_path=resolved.relative_to(workspace).as_posix(),
        name=_display_name(name, suffix),
        bytes_written=len(payload),
    )


def _display_name(name: str, suffix: str) -> str:
    """A label for the chip. Never a path, never trusted, never long."""

    cleaned = "".join(
        character
        for character in str(name or "").strip()
        if character.isalnum() or character in {" ", "-", "_", "."}
    ).strip()
    # A leading dot would render as a hidden file; a bare extension is noise.
    cleaned = cleaned.lstrip(".")[:48]
    return cleaned or f"Pasted image{suffix}"
