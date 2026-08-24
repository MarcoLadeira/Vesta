"""#613 crash matrix: termination at every lifecycle boundary (work in progress).

First acceptance criterion: "A run can be reconstructed after process
termination at every lifecycle boundary." This file grows one boundary at a
time; the PR is opened early so the work is visible while it is built.
"""

from __future__ import annotations

import unittest


class CrashMatrixPlaceholder(unittest.TestCase):
    def test_placeholder(self):
        self.skipTest("crash matrix under construction")


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
