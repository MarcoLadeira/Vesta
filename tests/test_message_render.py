"""The chat answer renderer must turn Markdown into safe, themed rich text.

These tests are Qt-free: they assert on the HTML string the desktop GUI feeds
into a rich-text QLabel, so message formatting is covered without a display.
"""

from __future__ import annotations

import unittest

from opai.message_render import render_message_html as R


class RenderBasicsTests(unittest.TestCase):
    def test_plain_sentence_renders_as_itself(self):
        html = R("Just a normal sentence.")
        self.assertIn("Just a normal sentence.", html)
        # No stray Markdown punctuation invented.
        self.assertNotIn("**", html)

    def test_bold_and_italic(self):
        html = R("This is **bold** and this is *italic*.")
        self.assertIn("<b>bold</b>", html)
        self.assertIn("<i>italic</i>", html)
        self.assertNotIn("**", html)

    def test_inline_code_is_monospace_and_not_reparsed(self):
        html = R("Run `git **status**` now.")
        self.assertIn("<code", html)
        # The ** inside code must stay literal, not become bold.
        self.assertIn("git **status**", html)

    def test_headings(self):
        html = R("# Title\nbody text")
        self.assertIn("Title", html)
        self.assertIn("font-weight:700", html)
        self.assertNotIn("# Title", html)

    def test_unordered_list(self):
        html = R("- one\n- two\n- three")
        self.assertEqual(html.count("<li"), 3)
        self.assertIn("<ul", html)

    def test_ordered_list(self):
        html = R("1. first\n2. second")
        self.assertEqual(html.count("<li"), 2)
        self.assertIn("<ol", html)

    def test_fenced_code_block_preserved(self):
        html = R("Here:\n```\ndef f():\n    return 1\n```\ndone")
        self.assertIn("<pre", html)
        self.assertIn("def f():", html)
        self.assertIn("return 1", html)

    def test_links_become_anchors(self):
        html = R("See [the docs](https://example.com/x) please.")
        self.assertIn('href="https://example.com/x"', html)
        self.assertIn(">the docs</a>", html)


class RenderSafetyTests(unittest.TestCase):
    def test_html_is_escaped(self):
        html = R("a < b and c > d & e")
        self.assertIn("&lt;", html)
        self.assertIn("&gt;", html)
        self.assertIn("&amp;", html)
        # No raw injectable tag survives.
        self.assertNotIn("<script", html.lower())

    def test_script_injection_is_neutralised(self):
        html = R("<script>alert('x')</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_none_and_empty_are_safe(self):
        self.assertEqual(R(None), "")
        self.assertEqual(R(""), "")
        self.assertEqual(R("   "), "")

    def test_custom_palette_colours_used(self):
        html = R("# hi", colors={"ink": "#abcdef"})
        self.assertIn("#abcdef", html)

    def test_lists_close_before_paragraph(self):
        html = R("- one\n\nafter the list")
        # The <ul> must be closed before the trailing paragraph.
        self.assertLess(html.index("</ul>"), html.index("after the list"))


if __name__ == "__main__":
    unittest.main()
