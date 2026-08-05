"""#622: every provider/runner boundary redacts before a value can reach a
result, log, receipt or CLI payload — never downstream, never per-consumer.

Two things were true before this fix, both measured directly rather than
assumed from the issue text:

1. ``opai.provider_contract.redact_secrets`` was a second, independent
   pattern list, narrower than and drifted from the canonical
   ``opaihub.command_runner.redact`` (imported by 28+ modules). It missed
   GitHub fine-grained PATs, Google/Gemini keys, Groq keys, AWS access key
   IDs and Slack tokens — the exact five categories #549/#546 had already
   fixed in the canonical redactor, just never propagated here.

2. ``opaihub/ask.py``'s two ``except Exception`` handlers returned a raw,
   un-redacted ``str(exc)`` that reached ``render_ask``'s plain-text CLI
   output completely unmodified — reproduced end to end before fixing:
   a runner raising ``RuntimeError("... Bearer sk-live-...")`` printed the
   live key straight to the terminal via ``opai ask``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opai.provider_contract import redact_secrets
from opaihub.ask import render_ask, run_ask


class ProviderContractDelegationTests(unittest.TestCase):
    """redact_secrets must now catch everything the canonical redactor does."""

    def test_github_fine_grained_pat_is_redacted(self):
        text = "auth failed for github_pat_11ABCDEFG0123456789abcdefghijklmnop"
        self.assertNotIn("github_pat_11ABCDEFG0123456789", redact_secrets(text))

    def test_google_gemini_key_is_redacted(self):
        text = "API error: key AIzaSyD-1234567890abcdefghijklmnopqrstuv rejected"
        self.assertNotIn(
            "AIzaSyD-1234567890abcdefghijklmnopqrstuv", redact_secrets(text)
        )

    def test_groq_key_is_redacted(self):
        text = "invalid credentials: gsk_1234567890abcdefghijklmnopqrstuvwxyzABCD"
        self.assertNotIn(
            "gsk_1234567890abcdefghijklmnopqrstuvwxyzABCD", redact_secrets(text)
        )

    def test_aws_access_key_id_is_redacted(self):
        text = "access denied for AKIAIOSFODNN7EXAMPLE"
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redact_secrets(text))

    def test_slack_token_is_redacted(self):
        text = "webhook failed: xoxb-1234567890-1234567890123-abcdefghijklmnopqrstuvwx"
        self.assertNotIn("xoxb-1234567890-1234567890123", redact_secrets(text))

    def test_deepseek_style_key_is_redacted(self):
        # #673 shipped in this same session — the new provider's key shape
        # must be covered by the redactor its errors will flow through.
        text = "invalid key sk-deadbeef0123456789abcdef01234567"
        self.assertNotIn("sk-deadbeef0123456789abcdef01234567", redact_secrets(text))

    def test_bearer_and_assignment_forms_still_work(self):
        # Regression guard: the categories the old pattern list already
        # caught must still be caught after delegating to the new one.
        text = "Authorization: Bearer sk-live-abc123SECRETKEYxyz789"
        self.assertNotIn("sk-live-abc123SECRETKEYxyz789", redact_secrets(text))

    def test_empty_and_none_input_is_safe(self):
        self.assertEqual(redact_secrets(None), "")
        self.assertEqual(redact_secrets(""), "")

    def test_non_secret_text_passes_through_unchanged(self):
        text = "file not found: /repo/src/app.py"
        self.assertEqual(redact_secrets(text), text)


class AskBoundaryRedactionTests(unittest.TestCase):
    """opaihub/ask.py's two failure-return sites redact before returning."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_answer_only_path_redacts_a_leaked_bearer_token(self):
        class LeakyRunner:
            def available(self):
                return True

            def complete(self, *a, **k):
                raise RuntimeError(
                    "provider rejected request: "
                    "Authorization: Bearer sk-live-abc123SECRETKEYxyz789"
                )

        result = run_ask(
            self.root,
            "do a task",
            allow_edits=False,
            runner=LeakyRunner(),
            record=False,
        )
        self.assertEqual(result["status"], "runner_error")
        self.assertNotIn("sk-live-abc123SECRETKEYxyz789", result["error"])
        self.assertIn("[REDACTED]", result["error"])

    def test_edit_path_also_redacts(self):
        # The second leak site is in a *different* function from run_ask:
        # run_explicit_model (the tool-loop/edit dispatcher used by
        # opai.app_state's paid/free-tier callers). Same reproduction shape.
        from opaihub.ask import run_explicit_model

        class LeakyEditRunner:
            def available(self):
                return True

            def complete_with_tools(self, *a, **k):
                raise RuntimeError(
                    "internal error: api_key=sk-proj-abc123DEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                )

        result = run_explicit_model(
            self.root,
            "fix the bug",
            runner=LeakyEditRunner(),
            selected_model_id="free:groq:test-model",
            allow_edits=True,
            record=False,
        )
        self.assertEqual(result["status"], "runner_error")
        self.assertNotIn(
            "sk-proj-abc123DEFGHIJKLMNOPQRSTUVWXYZ0123456789", result["error"]
        )

    def test_the_cli_render_path_never_shows_the_raw_secret(self):
        """The actual user-visible surface this fix closes: `opai ask`'s
        plain-text output (render_ask), not just the internal result dict."""

        class LeakyRunner:
            def available(self):
                return True

            def complete(self, *a, **k):
                raise RuntimeError(
                    "provider rejected request: "
                    "Authorization: Bearer sk-live-abc123SECRETKEYxyz789"
                )

        result = run_ask(
            self.root,
            "do a task",
            allow_edits=False,
            runner=LeakyRunner(),
            record=False,
        )
        rendered = render_ask(result)
        self.assertNotIn("sk-live-abc123SECRETKEYxyz789", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_a_genuinely_non_secret_error_still_reads_clearly(self):
        # Redaction must not eat ordinary, useful error text.
        class BoringRunner:
            def available(self):
                return True

            def complete(self, *a, **k):
                raise RuntimeError("connection refused: localhost:11434")

        result = run_ask(
            self.root,
            "do a task",
            allow_edits=False,
            runner=BoringRunner(),
            record=False,
        )
        self.assertIn("connection refused", result["error"])
        self.assertIn("localhost:11434", result["error"])


if __name__ == "__main__":
    unittest.main()
