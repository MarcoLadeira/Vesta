"""Images arriving from a web context, and everything that must not happen.

This is the boundary where untrusted bytes and an untrusted filename cross
into the filesystem, so most of these tests are about refusals. The happy path
is one function call; the value is in what it declines to do with a payload
that lies about what it is.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import pytest

from vestahub.attachments import (
    MAX_BYTES,
    RETENTION_SECONDS,
    AttachmentError,
    attachments_dir,
    prune,
    sniff_extension,
    store_image,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"
JPEG = b"\xff\xd8\xff\xe0" + b"pixels"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"pixels"


def encode(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def test_a_pasted_png_becomes_a_workspace_relative_reference(tmp_path: Path):
    """The whole design in one assertion.

    An image has to end up as the same kind of thing a picked file is -- a
    workspace-relative path -- because that is what the prompt carries and
    what the provider CLIs already know how to read. Nothing downstream
    changes for images; they just gain a location they never had.
    """
    stored = store_image(tmp_path, encode(PNG), name="Screenshot.png")

    assert stored.relative_path.startswith(".vestahub/attachments/")
    assert stored.relative_path.endswith(".png")
    assert (tmp_path / stored.relative_path).read_bytes() == PNG
    assert stored.bytes_written == len(PNG)


@pytest.mark.parametrize(
    ("payload", "suffix"),
    [(PNG, ".png"), (JPEG, ".jpg"), (b"GIF89a" + b"x", ".gif"), (WEBP, ".webp")],
    ids=["png", "jpeg", "gif", "webp"],
)
def test_the_extension_comes_from_the_bytes(tmp_path: Path, payload, suffix):
    assert sniff_extension(payload) == suffix
    assert store_image(tmp_path, encode(payload)).relative_path.endswith(suffix)


def test_a_zip_calling_itself_a_png_is_refused(tmp_path: Path):
    """A browser reports whatever media type a page hands it.

    The declared type is a claim, not evidence, so it is never consulted --
    only the leading bytes are, and these do not describe an image.
    """
    with pytest.raises(AttachmentError, match="not an image"):
        store_image(tmp_path, "data:image/png;base64," + encode(b"PK\x03\x04zip"))

    assert (
        not any(attachments_dir(tmp_path).glob("*"))
        if attachments_dir(tmp_path).is_dir()
        else True
    )


def test_a_hostile_filename_cannot_choose_a_destination(tmp_path: Path):
    """The name is a label. The path is generated, so there is nothing to escape."""
    stored = store_image(tmp_path, encode(PNG), name="../../../../.ssh/authorized_keys")

    assert stored.relative_path.startswith(".vestahub/attachments/")
    assert "/" not in stored.name and ".." not in stored.name
    assert (tmp_path / stored.relative_path).is_file()
    assert not (tmp_path.parent / ".ssh").exists()


def test_a_data_url_wrapper_is_accepted(tmp_path: Path):
    """FileReader.readAsDataURL is what the composer has in hand, so take it."""
    stored = store_image(tmp_path, "data:image/png;base64," + encode(PNG))

    assert (tmp_path / stored.relative_path).read_bytes() == PNG


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ("", "empty"),
        ("not base64 !!!", "could not be read"),
        ("data:image/png;base64,####", "could not be read"),
    ],
    ids=["empty", "garbage", "garbage-in-data-url"],
)
def test_unreadable_payloads_are_refused_with_something_sayable(
    tmp_path: Path, payload, match
):
    with pytest.raises(AttachmentError, match=match):
        store_image(tmp_path, payload)


def test_an_oversized_image_is_refused_before_it_is_written(tmp_path: Path):
    """An unbounded paste is a memory problem here and a bill downstream."""
    huge = encode(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_BYTES)

    with pytest.raises(AttachmentError, match="10 MB or smaller"):
        store_image(tmp_path, huge)

    assert (
        list(attachments_dir(tmp_path).glob("*")) == []
        if attachments_dir(tmp_path).is_dir()
        else True
    )


def test_two_pastes_of_the_same_image_do_not_collide(tmp_path: Path):
    first = store_image(tmp_path, encode(PNG), name="shot.png")
    second = store_image(tmp_path, encode(PNG), name="shot.png")

    assert first.relative_path != second.relative_path
    assert (tmp_path / first.relative_path).is_file()
    assert (tmp_path / second.relative_path).is_file()


def test_attachments_live_where_git_will_not_see_them(tmp_path: Path):
    """`.vestahub/` is already ignored. An attachment must never reach a diff."""
    stored = store_image(tmp_path, encode(PNG))

    assert stored.relative_path.split("/")[0] == ".vestahub"


def test_old_attachments_are_swept_and_recent_ones_are_not(tmp_path: Path):
    keep = store_image(tmp_path, encode(PNG))
    stale = attachments_dir(tmp_path) / "20200101-000000-deadbeef.png"
    stale.write_bytes(PNG)
    old = time.time() - RETENTION_SECONDS - 60
    import os

    os.utime(stale, (old, old))

    removed = prune(tmp_path)

    assert removed == 1
    assert not stale.exists()
    assert (tmp_path / keep.relative_path).is_file()


def test_pruning_a_workspace_with_no_attachments_is_not_an_error(tmp_path: Path):
    assert prune(tmp_path) == 0


def test_a_nameless_paste_still_gets_a_label(tmp_path: Path):
    """A clipboard screenshot has no filename at all -- the commonest case."""
    assert store_image(tmp_path, encode(PNG)).name == "Pasted image.png"
