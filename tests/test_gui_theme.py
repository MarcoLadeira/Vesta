"""The desktop GUI's app-wide colour theme (Qt-free).

Uses isolated_home so the developer's real ~/.vesta/gui_theme.json is never read
or written: a theme saved on this machine must not change a test's answer.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from _helpers import isolated_home

from vesta.gui_theme import (
    DEFAULT_THEME,
    PALETTES,
    SYSTEM_DARK_THEME,
    THEME_GROUND,
    THEMES,
    load_theme,
    normalize_theme,
    resolve_theme,
    save_theme,
    stamp_theme,
    theme_path,
)

WEB_DIR = Path(__file__).resolve().parents[1] / "vesta" / "assets" / "web"


class NormalizeTests(unittest.TestCase):
    def test_known_preferences_pass_through_in_any_case(self):
        for theme in THEMES:
            self.assertEqual(normalize_theme(theme), theme)
            self.assertEqual(normalize_theme(f"  {theme.upper()} "), theme)

    def test_the_choices_are_light_viber_coder_dark_vesta_and_system(self):
        self.assertEqual(THEMES, ("light", "viber-coder", "dark", "vesta", "system"))
        self.assertEqual(PALETTES, ("light", "viber-coder", "dark", "vesta"))

    def test_anything_else_is_the_default_viber_coder_theme(self):
        self.assertEqual(DEFAULT_THEME, "viber-coder")
        for value in (None, "", "sepia", "auto", "viber", 1, True, ["light"]):
            self.assertEqual(normalize_theme(value), "viber-coder")


class PersistenceTests(unittest.TestCase):
    def test_a_fresh_profile_opens_in_the_default_theme(self):
        with isolated_home():
            self.assertFalse(theme_path().exists())
            self.assertEqual(load_theme(), "viber-coder")

    def test_each_theme_round_trips_through_the_app_wide_file(self):
        with isolated_home() as home:
            for theme in THEMES:
                self.assertEqual(save_theme(theme), theme)
                self.assertEqual(load_theme(), theme)
            self.assertEqual(theme_path(), home / ".vesta" / "gui_theme.json")
            stored = json.loads(theme_path().read_text(encoding="utf-8"))
            self.assertEqual(stored, {"theme": "system"})

    def test_an_unknown_value_is_stored_as_the_default(self):
        with isolated_home():
            save_theme("light")
            self.assertEqual(save_theme("neon"), "viber-coder")
            self.assertEqual(load_theme(), "viber-coder")

    def test_a_corrupt_or_foreign_file_reads_as_the_default(self):
        with isolated_home():
            path = theme_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            for content in ("{not json", "[]", '"light"', '{"theme": 7}'):
                path.write_text(content, encoding="utf-8")
                self.assertEqual(load_theme(), "viber-coder", content)


class ResolveTests(unittest.TestCase):
    def test_system_follows_the_operating_system_between_light_and_viber_coder(self):
        self.assertEqual(SYSTEM_DARK_THEME, "viber-coder")
        self.assertEqual(resolve_theme("system", system_prefers_light=True), "light")
        self.assertEqual(
            resolve_theme("system", system_prefers_light=False), "viber-coder"
        )

    def test_an_explicit_choice_ignores_the_operating_system(self):
        for prefers_light in (True, False):
            for palette in PALETTES:
                self.assertEqual(
                    resolve_theme(palette, system_prefers_light=prefers_light), palette
                )
        self.assertEqual(
            resolve_theme("bogus", system_prefers_light=True), "viber-coder"
        )


class StampTests(unittest.TestCase):
    def test_the_resolved_theme_lands_on_the_html_element(self):
        html = '<!DOCTYPE html>\n<html lang="en">\n<head></head><body></body></html>'
        stamped = stamp_theme(html, "light")
        self.assertIn('<html lang="en" data-theme="light">', stamped)
        self.assertEqual(stamped.count("data-theme"), 1)

    def test_an_existing_theme_attribute_is_replaced_not_duplicated(self):
        for tag in (
            '<html data-theme="dark" lang="en">',
            "<html data-theme='dark' lang=\"en\">",
            '<html lang="en" data-theme=dark>',
        ):
            stamped = stamp_theme(tag + "<body></body></html>", "light")
            self.assertEqual(stamped.count("data-theme"), 1, tag)
            self.assertIn('data-theme="light"', stamped)
            self.assertIn('lang="en"', stamped)

    def test_only_a_palette_is_ever_stamped(self):
        for palette in PALETTES:
            self.assertIn(f'data-theme="{palette}"', stamp_theme("<html>", palette))
        # "system" is a preference, not a palette: the host resolves it first.
        self.assertIn('data-theme="viber-coder"', stamp_theme("<html>", "system"))
        self.assertIn('data-theme="viber-coder"', stamp_theme("<HTML>", "<script>"))

    def test_a_document_without_an_html_element_is_left_alone(self):
        self.assertEqual(stamp_theme("<body></body>", "light"), "<body></body>")
        self.assertEqual(stamp_theme("<htmlx>", "light"), "<htmlx>")


class WebContractTests(unittest.TestCase):
    """The Python host and the page must agree on names and colours."""

    @staticmethod
    def _palettes() -> dict[str, str]:
        css = (WEB_DIR / "design-tokens.css").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        blocks = {}
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            match = re.fullmatch(
                r'\s*(?::root,\s*)?\[data-theme="([\w-]+)"\]\s*', selector
            )
            if match:
                blocks[match.group(1)] = body
        return blocks

    def test_every_theme_has_a_palette_and_every_palette_a_theme(self):
        self.assertEqual(set(self._palettes()), set(PALETTES))
        self.assertEqual(set(THEME_GROUND), set(PALETTES))

    def test_the_window_ground_is_each_palettes_background(self):
        for theme, body in self._palettes().items():
            bg = re.search(r"--bg:\s*([^;]+);", body)
            self.assertIsNotNone(bg, theme)
            self.assertEqual(bg.group(1).strip().lower(), THEME_GROUND[theme], theme)

    def test_the_page_knows_the_same_themes_and_default(self):
        script = (WEB_DIR / "theme.js").read_text(encoding="utf-8")
        names = re.search(r"var THEMES = \[([^\]]*)\]", script)
        default = re.search(r'var DEFAULT_THEME = "([^"]+)"', script)
        self.assertIsNotNone(names)
        self.assertIsNotNone(default)
        self.assertEqual(set(re.findall(r'"([^"]+)"', names.group(1))), set(THEMES))
        self.assertEqual(default.group(1), DEFAULT_THEME)

    def test_the_page_loads_the_theme_engine(self):
        index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="theme.js"></script>', index)
        # Before app.js, which applies the theme at boot.
        self.assertLess(index.index("theme.js"), index.index('src="app.js"'))


if __name__ == "__main__":
    unittest.main()
