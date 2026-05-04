import tempfile
import unittest
from pathlib import Path

from opcoding.cache import prompt_cache_dir
from opcoding.memory import add_decision, add_memory, list_memory
from opcoding.mcp_manager import mcp_doctor, render_codex_mcp_config
from opcoding.models import build_model_bundle
from opcoding.project_index import build_index, search_index
from opcoding.shipper import ship_plan


class AdvancedSmokeTests(unittest.TestCase):
    def test_project_index_finds_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text(
                "class CheckoutService:\n    pass\n", encoding="utf-8"
            )
            index = build_index(root)
            self.assertEqual(index["file_count"], 1)
            matches = search_index(root, "CheckoutService")
            self.assertEqual(matches["match_count"], 1)

    def test_memory_stores_events_and_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_memory(root, "note", "hello", "world")
            add_decision(root, "Use local-first", "Keeps cost low")
            memory = list_memory(root)
            self.assertEqual(memory["events"][0]["title"], "hello")
            self.assertEqual(memory["decisions"][0]["title"], "Use local-first")

    def test_model_bundle_prepares_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# demo\n", encoding="utf-8")
            bundle = build_model_bundle(root, "explain project")
            self.assertFalse(bundle["model_executed"])
            self.assertIn("prompt", bundle)
            self.assertLessEqual(bundle["prompt_chars"], 9000)

            cache_files = list(prompt_cache_dir(root).glob("*.json"))
            self.assertEqual(len(cache_files), 1)
            cached_text = cache_files[0].read_text(encoding="utf-8")
            self.assertNotIn('"prompt"', cached_text)
            self.assertIn('"prompt_hash"', cached_text)

    def test_mcp_render_uses_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doctor = mcp_doctor(root)
            self.assertTrue(doctor["roots"][0]["exists"])
            rendered = render_codex_mcp_config(root)
            self.assertIn("servers", rendered)

    def test_ship_plan_has_confirmation_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = ship_plan(root, "build and ship notes app")
            self.assertIn(
                "explicit confirmation before push/deploy/expensive model",
                plan["automatic_gates"],
            )


if __name__ == "__main__":
    unittest.main()
