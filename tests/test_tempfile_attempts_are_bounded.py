"""A directory that refuses every temporary name fails; it does not hang.

``tempfile`` tries ``TMP_MAX`` names before giving up, and on Windows that is
``os.TMP_MAX``: 2,147,483,647. It retries on "file exists", and on Windows on
"permission denied" too. Creating a file through a directory symlink answers
"file exists" on some Windows machines, so an atomic write there never ended --
and the GUI's boot waits on one (``save_active_repo``). The unit gate's
``test_session_resume`` symlink test hung for exactly this reason, on ``main``,
until the gate's thirty-minute budget killed the whole run.

``opai/__init__.py`` bounds the loop for every OPai process. These tests make a
directory refuse every name, deterministically, rather than depending on a
machine that happens to have the symlink behaviour.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import opai
from opaihub.atomic_io import atomic_write_text


class TempfileAttemptsAreBoundedTests(unittest.TestCase):
    def test_every_opai_process_bounds_the_retries(self):
        self.assertLessEqual(tempfile.TMP_MAX, opai.TEMPFILE_ATTEMPTS)

    def _write_into_a_directory_that_refuses(self, refusal: OSError):
        attempts: list[str] = []
        real_open = os.open

        def refuses(path, flags, *args, **kwargs):
            if str(path).endswith(".tmp"):
                attempts.append(str(path))
                raise refusal
            return real_open(path, flags, *args, **kwargs)

        outcome: dict[str, BaseException] = {}

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "active_repo.json"

            def write():
                try:
                    with mock.patch.object(os, "open", refuses):
                        atomic_write_text(target, "{}")
                except BaseException as exc:  # noqa: BLE001 - recorded, asserted
                    outcome["error"] = exc

            worker = threading.Thread(target=write, daemon=True)
            worker.start()
            worker.join(timeout=60)

            self.assertFalse(worker.is_alive(), "the write never gave up")
            self.assertFalse(target.exists())
        return outcome.get("error"), attempts

    def test_file_exists_for_every_name_is_an_error_not_a_hang(self):
        error, attempts = self._write_into_a_directory_that_refuses(
            FileExistsError(17, "File exists")
        )

        self.assertIsInstance(error, FileExistsError)
        self.assertLessEqual(len(attempts), opai.TEMPFILE_ATTEMPTS)

    @unittest.skipUnless(os.name == "nt", "tempfile retries this only on Windows")
    def test_permission_denied_for_every_name_is_an_error_not_a_hang(self):
        error, attempts = self._write_into_a_directory_that_refuses(
            PermissionError(13, "Permission denied")
        )

        self.assertIsInstance(error, OSError)
        self.assertLessEqual(len(attempts), opai.TEMPFILE_ATTEMPTS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
