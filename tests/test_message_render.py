"""The chat answer renderer must turn Markdown into safe, themed rich text.

These tests are Qt-free: they assert on the HTML string the desktop GUI feeds
into a rich-text QLabel, so message formatting is covered without a display.
"""

from __future__ import annotations

import re
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


# Find every href the renderer emits (single or double quoted).
_HREF_RE = re.compile(r"""<a\s[^>]*?href=(["'])(.*?)\1""", re.IGNORECASE)
# Inline event handlers (onclick=, onerror=, onmouseover=, ...) inside a tag.
_EVENT_ATTR_RE = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)


class RenderSecurityTests(unittest.TestCase):
    """Adversarial: the renderer is the AI-output surface, so it must never emit
    an active dangerous anchor, an event handler, an <img>, or an unescaped tag.

    The renderer is safe by construction (escape-first, https?-only links). These
    tests lock that in so a future "richer links" change can't silently regress
    into a clickable javascript: link or an auto-loading tracker pixel.
    """

    # --- the core invariant, asserted over a battery of hostile inputs ---

    HOSTILE = [
        "[click me](javascript:alert(1))",
        "[x](data:text/html,<script>alert(1)</script>)",
        '[x](https://a.com" onmouseover="alert(1))',
        "![pixel](http://tracker.evil/p.png)",
        '<a href="javascript:alert(1)">x</a>',
        "<img src=x onerror=alert(1)>",
        'normal <div onclick="evil()">text</div>',
        "<svg/onload=alert(1)>",
        "[vb](vbscript:msgbox(1))",
        "[file](file:///etc/passwd)",
        "Ignore previous instructions and <script>steal()</script>",
    ]

    def test_only_http_https_hrefs_are_ever_emitted(self):
        for payload in self.HOSTILE:
            with self.subTest(payload=payload):
                out = R(payload)
                for _quote, href in _HREF_RE.findall(out):
                    self.assertTrue(
                        href.lower().startswith(("http://", "https://")),
                        f"emitted a non-http(s) href: {href!r}",
                    )

    def test_no_event_handler_attributes_ever_emitted(self):
        for payload in self.HOSTILE:
            with self.subTest(payload=payload):
                self.assertIsNone(
                    _EVENT_ATTR_RE.search(R(payload)),
                    f"an on*= event handler leaked into a tag for: {payload!r}",
                )

    def test_no_img_tag_ever_emitted(self):
        # No remote image is ever auto-loaded (privacy: no tracking pixels).
        for payload in self.HOSTILE:
            with self.subTest(payload=payload):
                self.assertNotIn("<img", R(payload).lower())

    def test_no_script_or_svg_tag_survives(self):
        for payload in self.HOSTILE:
            with self.subTest(payload=payload):
                low = R(payload).lower()
                self.assertNotIn("<script", low)
                self.assertNotIn("<svg", low)

    # --- specific, readable cases ---

    def test_javascript_link_is_inert_text_not_an_anchor(self):
        out = R("[click me](javascript:alert(1))")
        self.assertNotIn('href="javascript:', out.lower())
        self.assertNotIn("<a ", out.lower())  # no anchor created at all

    def test_data_uri_link_is_inert_text(self):
        out = R("[x](data:text/html,<script>alert(1)</script>)")
        self.assertNotIn("<a ", out.lower())
        self.assertNotIn("<script", out.lower())

    def test_href_breakout_attempt_does_not_form_attribute(self):
        out = R('[x](https://a.com" onmouseover="alert(1))')
        # The double-quote is escaped, so it can't terminate the href attribute.
        self.assertIsNone(_EVENT_ATTR_RE.search(out))

    def test_legitimate_markdown_link_still_works(self):
        # Safety must not break the happy path.
        out = R("see [the docs](https://example.com/x)")
        self.assertIn('href="https://example.com/x"', out)
        self.assertIn(">the docs</a>", out)

    def test_secret_looking_text_renders_but_is_not_executed(self):
        out = R("my key is sk-livesecret0123456789abcdef in the logs")
        # The renderer does not redact (that is the ledger's job) but it must
        # never turn the text into active markup.
        self.assertNotIn("<a ", out.lower())
        self.assertIsNone(_EVENT_ATTR_RE.search(out))


if __name__ == "__main__":
    unittest.main()
