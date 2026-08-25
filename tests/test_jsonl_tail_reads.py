"""Regression coverage for bounded ledger and audit tail reads."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import audit, ledger


class JsonlTailReadTests(unittest.TestCase):
    def _write_rows(self, path: Path, count: int = 2_000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.dumps({"row": index}) for index in range(count)]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def test_zero_limit_returns_no_ledger_or_audit_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_rows(ledger.ledger_path(root), count=3)
            self._write_rows(audit.audit_path(root), count=3)

            self.assertEqual(ledger.read_events(root, limit=0), [])
            self.assertEqual(audit.read_audit(root, limit=0), [])

    def test_limited_reads_do_not_materialize_the_complete_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_rows(ledger.ledger_path(root))
            self._write_rows(audit.audit_path(root))

            with mock.patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("bounded read loaded the complete log"),
            ):
                ledger_rows = ledger.read_events(root, limit=2)
                audit_rows = audit.read_audit(root, limit=2)

            self.assertEqual([row["row"] for row in ledger_rows], [1998, 1999])
            self.assertEqual([row["row"] for row in audit_rows], [1998, 1999])

    def test_tail_limit_keeps_physical_line_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = (
                json.dumps({"row": 1})
                + "\n"
                + "malformed\n"
                + json.dumps({"row": 2})
                + "\n\n"
            )
            ledger_path = ledger.ledger_path(root)
            audit_path = audit.audit_path(root)
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(content, encoding="utf-8")
            audit_path.write_text(content, encoding="utf-8")

            self.assertEqual(ledger.read_events(root, limit=3), [{"row": 2}])
            self.assertEqual(audit.read_audit(root, limit=3), [{"row": 2}])


if __name__ == "__main__":
    unittest.main()
