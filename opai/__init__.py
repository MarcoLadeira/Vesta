"""Vesta public package metadata."""

import tempfile as _tempfile

from ._generated_release import APPLICATION_VERSION, RELEASE_STAGE

#: How many names ``tempfile`` may try before giving up. Its own limit is
#: ``os.TMP_MAX``, which on Windows is 2,147,483,647 -- and it retries on
#: "file exists" (and, on Windows, on "permission denied") every time. A
#: directory that refuses every name therefore turned one write into a hang
#: with no end. That is not hypothetical: creating a file through a directory
#: symlink answers "file exists" on some Windows machines, and the GUI's boot
#: waits on exactly such a write (`save_active_repo`), so the app froze
#: instead of reporting an error. Names are random, so a hundred genuine
#: collisions do not happen; a hundred refusals is an answer.
#:
#: Set here because every Vesta process imports this package first --
#: ``opaihub`` included -- which covers all thirteen places that create a
#: temporary file next to their target, not just the one that was caught.
TEMPFILE_ATTEMPTS = 100
if _tempfile.TMP_MAX > TEMPFILE_ATTEMPTS:
    _tempfile.TMP_MAX = TEMPFILE_ATTEMPTS


__version__ = APPLICATION_VERSION
__release_stage__ = RELEASE_STAGE
__brand__ = "Vesta"
