"""The HTML dashboard cache is always a coherent snapshot (#461)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from _helpers import make_repo

from opaihub.dashboard_html import build_dashboard_html
from opaihub.state import state_dir


class DashboardHtmlAtomicTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_writes_a_complete_file_with_no_temp_leftovers(self):
        path = build_dashboard_html(self.root)
        self.assertTrue(path.read_text(encoding="utf-8").rstrip().endswith("</html>"))
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_concurrent_refreshes_never_expose_a_torn_snapshot(self):
        path = state_dir(self.root) / "dashboard.html"
        build_dashboard_html(self.root)  # prime the file
        errors: list[str] = []
        stop = threading.Event()

        def writer() -> None:
            while not stop.is_set():
                try:
                    build_dashboard_html(self.root)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"writer: {exc!r}")
                    return

        def reader() -> None:
            for _ in range(400):
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue  # replace-in-progress on some platforms; retry
                if text and not text.rstrip().endswith("</html>"):
                    errors.append("reader saw a torn dashboard snapshot")
                    return

        writers = [threading.Thread(target=writer) for _ in range(3)]
        readers = [threading.Thread(target=reader) for _ in range(3)]
        for thread in writers + readers:
            thread.start()
        for thread in readers:
            thread.join()
        stop.set()
        for thread in writers:
            thread.join()

        self.assertEqual(errors, [])
        # The final snapshot is complete and no temp files leaked.
        self.assertTrue(path.read_text(encoding="utf-8").rstrip().endswith("</html>"))
        self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
