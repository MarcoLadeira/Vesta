"""App scaffolding — the free-boilerplate foundation of Vesta Build (#276).
Deterministic, no AI, no network: a prompt becomes a runnable app skeleton."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from vestahub.app_scaffold import infer_kind, kinds, scaffold_app, slugify
from vestahub.app_verify import verify_app


class SlugifyTests(unittest.TestCase):
    def test_free_text_becomes_a_safe_slug(self):
        self.assertEqual(
            slugify("A Todo App with Dark Mode!"), "a-todo-app-with-dark-mode"
        )
        self.assertEqual(slugify("  spaces  and  --dashes-- "), "spaces-and-dashes")

    def test_empty_falls_back(self):
        self.assertEqual(slugify(""), "app")
        self.assertEqual(slugify("!!!", fallback="thing"), "thing")


class ScaffoldWebTests(unittest.TestCase):
    def _scaffold(self, tmp, desc="A todo app with dark mode", **kw):
        return scaffold_app(Path(tmp), desc, **kw)

    def test_writes_a_runnable_web_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._scaffold(tmp)
            root = Path(result.root)
            self.assertEqual(result.kind, "web")
            self.assertEqual(result.name, "a-todo-app-with-dark-mode")
            for f in ("index.html", "styles.css", "app.js", "README.md"):
                self.assertTrue((root / f).exists(), f"missing {f}")
            self.assertEqual(result.entrypoint, "index.html")

    def test_index_html_is_well_formed_and_wired(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._scaffold(tmp)
            html = (Path(result.root) / "index.html").read_text(encoding="utf-8")
            self.assertTrue(html.lstrip().lower().startswith("<!doctype html>"))
            self.assertIn('<link rel="stylesheet" href="styles.css">', html)
            self.assertIn('<script src="app.js"></script>', html)
            # Title is derived from the description.
            self.assertIn("A Todo App With Dark Mode", html)

    def test_placeholders_are_substituted_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._scaffold(tmp)
            for f in result.files:
                text = (Path(result.root) / f).read_text(encoding="utf-8")
                self.assertNotIn("__APP_TITLE__", text)
                self.assertNotIn("__APP_DESC__", text)
                self.assertNotIn("__APP_NAME__", text)

    def test_manifest_reports_free_boilerplate_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._scaffold(tmp)
            data = result.to_dict()
            self.assertGreater(data["boilerplate_tokens_avoided"], 100)
            self.assertIn(result.preview_cmd, "\n".join(result.next_steps))

    def test_custom_name_overrides_derived(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._scaffold(tmp, name="my-thing")
            self.assertEqual(result.name, "my-thing")
            self.assertTrue((Path(result.root)).name == "my-thing")


class ScaffoldStaticTests(unittest.TestCase):
    def test_static_kind_writes_a_landing_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scaffold_app(Path(tmp), "A launch page", kind="static")
            root = Path(result.root)
            self.assertEqual(sorted(result.files), ["index.html", "styles.css"])
            self.assertFalse((root / "app.js").exists())


class DataTemplateTests(unittest.TestCase):
    def test_data_kind_writes_a_working_list_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scaffold_app(Path(tmp), "A todo app", kind="data")
            root = Path(result.root)
            self.assertEqual(result.kind, "data")
            self.assertEqual(
                sorted(result.files),
                ["README.md", "app.js", "index.html", "styles.css"],
            )
            app_js = (root / "app.js").read_text(encoding="utf-8")
            # It really is a working CRUD skeleton, not a stub.
            for fn in ("function addItem", "function deleteItem", "localStorage"):
                self.assertIn(fn, app_js)
            # The storage key is namespaced to the app.
            self.assertIn('"a-todo-app.items"', app_js)

    def test_the_scaffolded_data_app_passes_verification(self):
        # The strongest guarantee: a fresh data app is structurally sound —
        # doctype, wired assets, balanced JS/CSS — so `vesta build` can verify
        # against a known-good baseline.
        with tempfile.TemporaryDirectory() as tmp:
            result = scaffold_app(Path(tmp), "a grocery list", kind="data")
            report = verify_app(
                Path(result.root),
                entrypoint="index.html",
                files=["app.js", "styles.css"],
            )
            self.assertTrue(report["ok"], report)


class InferKindTests(unittest.TestCase):
    def test_list_apps_infer_the_data_template(self):
        for desc in (
            "a todo app",
            "notes with tags",
            "an expense tracker",
            "grocery shopping list",
            "habit tracker",
        ):
            self.assertEqual(infer_kind(desc), "data", desc)

    def test_landing_phrasing_infers_static(self):
        self.assertEqual(infer_kind("a landing page for my startup"), "static")
        self.assertEqual(infer_kind("my portfolio"), "static")

    def test_generic_falls_back_to_web(self):
        self.assertEqual(infer_kind("a drawing canvas"), "web")
        self.assertEqual(infer_kind(""), "web")

    def test_auto_kind_resolves_via_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scaffold_app(Path(tmp), "a todo app", kind="auto")
            self.assertEqual(result.kind, "data")
        with tempfile.TemporaryDirectory() as tmp:
            result = scaffold_app(Path(tmp), "a photo gallery", kind="auto")
            self.assertEqual(result.kind, "web")


class ScaffoldSafetyTests(unittest.TestCase):
    def test_unknown_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                scaffold_app(Path(tmp), "x", kind="rust-game")

    def test_refuses_to_clobber_a_nonempty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            (root / "keepme.txt").write_text("precious", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                scaffold_app(Path(tmp), "app")
            # The user's file is untouched.
            self.assertEqual(
                (root / "keepme.txt").read_text(encoding="utf-8"), "precious"
            )

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            (root / "old.txt").write_text("x", encoding="utf-8")
            result = scaffold_app(Path(tmp), "app", force=True)
            self.assertTrue((Path(result.root) / "index.html").exists())

    def test_known_kinds(self):
        self.assertIn("web", kinds())
        self.assertIn("static", kinds())
        self.assertIn("data", kinds())


class CliNewCommandTests(unittest.TestCase):
    def test_vesta_new_scaffolds_and_prints_manifest(self):
        from vesta.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["new", "a note taking app", "--into", tmp, "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["name"], "a-note-taking-app")
            self.assertTrue((Path(payload["root"]) / "index.html").exists())
            self.assertGreater(payload["boilerplate_tokens_avoided"], 0)

    def test_vesta_new_reports_a_clean_error_on_clobber(self):
        from vesta.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "taken"
            existing.mkdir()
            (existing / "f.txt").write_text("x", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["new", "taken", "--into", tmp, "--json"])
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(buf.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
