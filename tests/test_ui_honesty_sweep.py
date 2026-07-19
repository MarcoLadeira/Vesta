"""UI/docs honesty contract (#400).

These are cheap, deterministic guards that fail the moment the shipped web UI or
its docs start over-promising again: an undefined CSS token, an advertised
keyboard shortcut that was never wired, a re-hardcoded copy of the run-mode
labels, a link/label that claims a view that doesn't exist, or a design doc that
describes a different product's palette.
"""

import re
import unittest
from pathlib import Path

from opaihub.autonomy import MODE_LABELS

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "opai" / "assets" / "web"
STYLES = WEB / "styles.css"
# Colour/type/spacing tokens live in design-tokens.css (#388); styles.css
# consumes them, so both files ship together and both define custom properties.
DESIGN_TOKENS = WEB / "design-tokens.css"
APP_JS = WEB / "app.js"
SETTINGS_JS = WEB / "settings.js"
GUI_CONTROLS = ROOT / "opai" / "gui_controls.py"
DESIGN_DOC = ROOT / "docs" / "DESIGN_SYSTEM.md"

# Files that used to keep their own hardcoded run-mode label dict; they must now
# consume the single source in opaihub/autonomy.py instead.
MODE_LABEL_CONSUMERS = [
    ROOT / "opai" / "gui_web.py",
    ROOT / "opaihub" / "gui_pipeline.py",
    ROOT / "opai" / "gui_desktop.py",
]


class NoUndefinedCssVarsTests(unittest.TestCase):
    def test_every_css_var_reference_is_defined(self):
        styles = STYLES.read_text(encoding="utf-8")
        tokens = DESIGN_TOKENS.read_text(encoding="utf-8")
        # A custom property is "defined" wherever it appears as `--name:` — across
        # the token file and any :root/theme/component block in styles.css.
        defined = set(re.findall(r"(--[A-Za-z0-9-]+)\s*:", styles + "\n" + tokens))
        # Only flag references with NO fallback: `var(--name)`. A `var(--x, y)`
        # reference degrades to `y` when `--x` is absent, so it is a deliberate
        # optional token, not a silent breakage.
        referenced = set(re.findall(r"var\(\s*(--[A-Za-z0-9-]+)\s*\)", styles))
        undefined = sorted(referenced - defined)
        self.assertEqual(
            undefined, [], f"styles.css references undefined CSS variables: {undefined}"
        )


class ShortcutsAreWiredTests(unittest.TestCase):
    def _advertised_shortcuts(self):
        text = GUI_CONTROLS.read_text(encoding="utf-8")
        m = re.search(r"SHORTCUTS\b.*?=\s*\[(.*?)\]\n", text, re.S)
        assert m, "could not locate the SHORTCUTS list in gui_controls.py"
        return re.findall(r'\(\s*"([^"]+)"\s*,', m.group(1))

    @staticmethod
    def _js_key(chord):
        part = chord.split("+")[-1]
        if part == "Esc":
            return "Escape"
        if len(part) == 1 and part.isalpha():
            return part.lower()  # app.js compares e.key against lowercase letters
        return part

    def test_every_advertised_shortcut_is_handled_in_app_js(self):
        app = APP_JS.read_text(encoding="utf-8")
        chords = self._advertised_shortcuts()
        self.assertIn("Ctrl+B", chords)  # guard: the list still advertises these
        self.assertIn("?", chords)
        for chord in chords:
            key = self._js_key(chord)
            with self.subTest(chord=chord):
                self.assertIn(
                    f'"{key}"',
                    app,
                    f"shortcut {chord!r} is advertised but not wired in app.js",
                )


class SingleModeLabelSourceTests(unittest.TestCase):
    _DICT_LITERAL = '"safe-auto": "Safe Auto"'

    def test_python_consumers_import_from_autonomy_and_do_not_redefine(self):
        for path in MODE_LABEL_CONSUMERS:
            src = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertIn(
                    "MODE_LABELS",
                    src,
                    f"{path.name} should consume autonomy.MODE_LABELS",
                )
                self.assertNotIn(
                    self._DICT_LITERAL,
                    src,
                    f"{path.name} re-hardcodes the run-mode labels",
                )

    def test_settings_js_mirror_matches_autonomy(self):
        js = SETTINGS_JS.read_text(encoding="utf-8")
        block = js.split("var MODE_LABELS", 1)[1].split("}", 1)[0]
        pairs = re.findall(r'["\']?([a-z-]+)["\']?\s*:\s*"([^"]+)"', block)
        js_labels = dict(pairs)
        self.assertEqual(
            js_labels,
            dict(MODE_LABELS),
            "settings.js MODE_LABELS drifted from opaihub/autonomy.py MODE_LABELS",
        )


class ModeLabelSourceBehaviourTests(unittest.TestCase):
    """Exercise the single label source and its consumers (not just static text)."""

    def test_mode_label_maps_known_modes_and_falls_back(self):
        from opaihub.autonomy import mode_label

        for mode, label in MODE_LABELS.items():
            with self.subTest(mode=mode):
                self.assertEqual(mode_label(mode), label)
        # Unknown modes fall back to the raw id — never a wrong human label.
        self.assertEqual(mode_label("nonexistent-mode"), "nonexistent-mode")

    def test_effective_label_uses_the_shared_source(self):
        from opaihub.autonomy import effective_mode

        decision = effective_mode("plan", {})
        self.assertEqual(decision.effective_label, MODE_LABELS["plan"])

    def test_pipeline_mode_label_consumes_autonomy(self):
        from opaihub.gui_pipeline import _mode_label

        self.assertEqual(_mode_label("full-auto"), MODE_LABELS["full-auto"])
        self.assertEqual(_mode_label("ask"), MODE_LABELS["ask"])
        # The pipeline defaults an unknown mode to the safest label.
        self.assertEqual(_mode_label("mystery"), MODE_LABELS["safe-auto"])


class HonestLabelsTests(unittest.TestCase):
    def test_receipt_button_does_not_claim_a_ledger(self):
        app = APP_JS.read_text(encoding="utf-8")
        # The receipt button reads "Summary" (with the shared arrow icon) and its
        # aria-label matches; it must not advertise a ledger that doesn't exist.
        self.assertIn('aria-label="Open the savings summary">Summary ', app)
        self.assertNotIn("Open the savings ledger", app)
        self.assertNotIn(">Ledger ", app)

    def test_truncation_marker_does_not_promise_an_unreachable_record(self):
        app = APP_JS.read_text(encoding="utf-8")
        self.assertNotIn("full record in the ledger", app)


class DesignDocMatchesShippedUiTests(unittest.TestCase):
    def test_design_system_documents_the_web_surface(self):
        doc = DESIGN_DOC.read_text(encoding="utf-8")
        self.assertIn("Chromium", doc)
        self.assertIn("styles.css", doc)
        self.assertIn("--accent", doc)
        # The legacy claim that the shipped GUI *is* QSS-not-CSS must be gone.
        self.assertNotIn("The OPai GUI is **PySide6 + QSS**, not CSS.", doc)


if __name__ == "__main__":
    unittest.main()
