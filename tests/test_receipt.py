"""Signed savings receipts (#87) + verification round-trip (#88 library half).

No real CLI, no network: receipts are pure reads of the local ledger.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from opai.cli import main
from opaihub.ledger import record_route_decision
from opaihub.receipt import build_receipt, render_receipt_svg, verify_receipt


def _repo(root: Path, *, name: str = "demo") -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "pyproject.toml").write_text(
        f"[project]\nname='{name}'\n", encoding="utf-8"
    )


def _seed_savings(root: Path) -> None:
    record_route_decision(root, "fix the auth bug", model_tier="L0", agent="claude")
    record_route_decision(root, "add a unit test", model_tier="L1", agent="claude")


class BuildReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _repo(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_signed_receipt_has_required_fields(self):
        _seed_savings(self.root)
        receipt = build_receipt(self.root)
        for field in (
            "report",
            "schema_version",
            "generated_at",
            "project_name",
            "has_data",
            "baseline_tier",
            "totals",
            "privacy",
            "receipt_hash",
            "signature",
        ):
            self.assertIn(field, receipt, f"receipt missing field: {field}")
        for total in (
            "routed_tasks",
            "cloud_calls_avoided",
            "estimated_baseline_usd",
            "estimated_actual_spend_usd",
            "estimated_savings_usd",
            "estimated_savings_percent",
        ):
            self.assertIn(total, receipt["totals"])

    def test_signature_block_present_when_signed(self):
        receipt = build_receipt(self.root, sign=True)
        self.assertIsInstance(receipt.get("signature"), dict)
        self.assertEqual(receipt["signature"]["algorithm"], "HMAC-SHA256")

    def test_no_sign_omits_signature_block(self):
        receipt = build_receipt(self.root, sign=False)
        self.assertNotIn("signature", receipt)

    def test_reflects_seeded_savings(self):
        _seed_savings(self.root)
        receipt = build_receipt(self.root)
        self.assertTrue(receipt["has_data"])
        self.assertEqual(receipt["totals"]["routed_tasks"], 2)
        self.assertEqual(receipt["totals"]["cloud_calls_avoided"], 2)
        self.assertGreater(receipt["totals"]["estimated_savings_usd"], 0)

    def test_empty_ledger_is_graceful(self):
        receipt = build_receipt(self.root)
        self.assertFalse(receipt["has_data"])
        self.assertEqual(receipt["totals"]["routed_tasks"], 0)
        self.assertEqual(receipt["totals"]["estimated_savings_usd"], 0)

    def test_no_absolute_path_leaks_into_receipt(self):
        _seed_savings(self.root)
        receipt = build_receipt(self.root)
        blob = json.dumps(receipt)
        self.assertNotIn(str(self.root), blob)
        # only the project name (basename) is carried, never the full path
        self.assertEqual(receipt["project_name"], self.root.name)


class RedactionTests(unittest.TestCase):
    def test_secret_in_project_name_is_redacted(self):
        tmp = tempfile.mkdtemp(prefix="sk-livesecret0123456789abcdef-")
        root = Path(tmp)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        _repo(root)
        receipt = build_receipt(root)
        blob = json.dumps(receipt)
        self.assertNotIn("sk-livesecret0123456789abcdef", blob)


class VerifyReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _repo(self.root)
        _seed_savings(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clean_receipt_verifies(self):
        receipt = build_receipt(self.root)
        result = verify_receipt(self.root, receipt)
        self.assertTrue(result["verified"], result["problems"])
        self.assertEqual(result["problems"], [])

    def test_tampered_number_fails_verification(self):
        receipt = build_receipt(self.root)
        receipt = json.loads(json.dumps(receipt))  # deep copy
        receipt["totals"]["estimated_savings_usd"] = 999.99
        result = verify_receipt(self.root, receipt)
        self.assertFalse(result["verified"])
        self.assertTrue(any("hash" in p for p in result["problems"]))

    def test_missing_hash_fails_verification(self):
        receipt = build_receipt(self.root)
        receipt.pop("receipt_hash", None)
        result = verify_receipt(self.root, receipt)
        self.assertFalse(result["verified"])

    def test_unsigned_receipt_does_not_verify(self):
        receipt = build_receipt(self.root, sign=False)
        result = verify_receipt(self.root, receipt)
        self.assertFalse(result["verified"])
        self.assertTrue(any("signature" in p for p in result["problems"]))


class RenderSvgTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _repo(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_svg_renders_with_data(self):
        _seed_savings(self.root)
        svg = render_receipt_svg(build_receipt(self.root))
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Savings Receipt", svg)
        self.assertIn("SIGNED", svg)

    def test_unsigned_svg_shows_unverified_watermark(self):
        svg = render_receipt_svg(build_receipt(self.root, sign=False))
        self.assertIn("UNVERIFIED", svg)

    def test_empty_ledger_svg_shows_no_data(self):
        svg = render_receipt_svg(build_receipt(self.root))
        self.assertIn("No data yet", svg)

    def test_svg_escapes_and_has_no_secret(self):
        # a secret-looking ledger task must not survive into the rendered card
        record_route_decision(
            self.root, "deploy token=sk-supersecret9876543210abcd", model_tier="L0"
        )
        svg = render_receipt_svg(build_receipt(self.root))
        self.assertNotIn("sk-supersecret9876543210abcd", svg)


class ReceiptCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _repo(self.root)
        _seed_savings(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cli_receipt_exits_clean(self):
        self.assertEqual(main(["receipt", "--project", str(self.root)]), 0)

    def test_cli_writes_svg_and_json(self):
        svg = self.root / "card.svg"
        out = self.root / "receipt.json"
        rc = main(
            [
                "receipt",
                "--project",
                str(self.root),
                "--svg",
                str(svg),
                "--out",
                str(out),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(
            svg.exists() and svg.read_text(encoding="utf-8").startswith("<svg")
        )
        self.assertTrue(out.exists())

    def test_cli_verify_round_trip(self):
        out = self.root / "receipt.json"
        main(["receipt", "--project", str(self.root), "--out", str(out)])
        rc = main(["receipt", "verify", str(out), "--project", str(self.root)])
        self.assertEqual(rc, 0)

    def test_cli_verify_tampered_returns_nonzero(self):
        out = self.root / "receipt.json"
        main(["receipt", "--project", str(self.root), "--out", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        data["totals"]["estimated_savings_usd"] = 4242.0
        out.write_text(json.dumps(data), encoding="utf-8")
        rc = main(["receipt", "verify", str(out), "--project", str(self.root)])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
