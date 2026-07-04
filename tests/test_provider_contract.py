import unittest

from opai.provider_contract import (
    dedupe_error_text,
    normalize_provider_error,
    provider_display_name,
    redact_secrets,
)


class ProviderErrorContractTests(unittest.TestCase):
    def test_401_is_auth_invalid(self):
        error = normalize_provider_error(
            "claude", "API Error: 401 Invalid authentication credentials"
        )

        self.assertEqual(error["code"], "AUTH_INVALID")
        self.assertEqual(error["authStatus"], "invalid")
        self.assertEqual(
            error["title"], "This account's sign-in was rejected by the provider."
        )
        self.assertIn("open_settings", error["recoveryActions"])
        self.assertIn("disconnect", error["recoveryActions"])
        self.assertFalse(error["retryable"])

    def test_expired_token_is_distinct_from_invalid(self):
        error = normalize_provider_error("codex", "OAuth token expired")

        self.assertEqual(error["code"], "AUTH_EXPIRED")
        self.assertEqual(error["authStatus"], "expired")
        self.assertIn("reconnect", error["recoveryActions"])

    def test_missing_credentials_map_to_auth_missing(self):
        error = normalize_provider_error("copilot", "No credentials configured")

        self.assertEqual(error["code"], "AUTH_MISSING")
        self.assertEqual(error["authStatus"], "not_configured")

    def test_rate_limit_and_timeout_are_retryable(self):
        limited = normalize_provider_error("claude", "HTTP 429 rate limit exceeded")
        timed_out = normalize_provider_error("claude", "", timed_out=True)

        self.assertEqual(limited["code"], "PROVIDER_RATE_LIMITED")
        self.assertTrue(limited["retryable"])
        self.assertEqual(timed_out["code"], "PROVIDER_TIMEOUT")
        self.assertTrue(timed_out["retryable"])

    def test_secret_values_are_redacted(self):
        text = (
            "Authorization: Bearer sk-live-secret123456 "
            "api_key=token_verysecretvalue cookie=session-secret"
        )

        safe = redact_secrets(text)

        self.assertNotIn("sk-live-secret123456", safe)
        self.assertNotIn("token_verysecretvalue", safe)
        self.assertNotIn("session-secret", safe)
        self.assertIn("[REDACTED]", safe)

    def test_repeated_provider_error_is_collapsed(self):
        repeated = (
            "Failed to authenticate. API Error: 401 Invalid authentication credentials"
            "Failed to authenticate. API Error: 401 Invalid authentication credentials"
        )

        safe = dedupe_error_text(repeated)

        self.assertEqual(
            safe,
            "Failed to authenticate. API Error: 401 Invalid authentication credentials",
        )

    def test_provider_details_are_advanced_only(self):
        # Simple label now uses provider-prefixed model name (not OPai generic)
        self.assertEqual(provider_display_name("claude", "haiku"), "Claude · Haiku 4.5")
        # Advanced label is unchanged — full diagnostic string for inspector/tooltip
        self.assertEqual(
            provider_display_name("claude", "haiku", advanced=True),
            "Claude Haiku 4.5 via Anthropic account connector",
        )

    def test_claude_model_labels(self):
        self.assertEqual(
            provider_display_name("claude", "sonnet"), "Claude · Sonnet 4.6"
        )
        self.assertEqual(provider_display_name("claude", "opus"), "Claude · Opus 4.8")
        self.assertEqual(provider_display_name("claude", "haiku"), "Claude · Haiku 4.5")

    def test_codex_model_labels(self):
        self.assertEqual(provider_display_name("codex", "gpt-5.5"), "Codex · GPT-5.5")
        self.assertEqual(provider_display_name("codex", "gpt-5.4"), "Codex · GPT-5.4")
        self.assertEqual(
            provider_display_name("codex", "gpt-5.4-mini"), "Codex · GPT-5.4 Mini"
        )
        self.assertEqual(
            provider_display_name("codex", "gpt-5.3-codex-spark"), "Codex · Spark"
        )

    def test_copilot_model_labels(self):
        self.assertEqual(
            provider_display_name("copilot", "claude-sonnet-4.6"),
            "Copilot · Claude Sonnet",
        )
        self.assertEqual(
            provider_display_name("copilot", "gpt-5.2"), "Copilot · GPT-5.2"
        )
        self.assertEqual(
            provider_display_name("copilot", "claude-haiku-4.5"),
            "Copilot · Claude Haiku",
        )

    def test_auto_and_local_labels_unchanged(self):
        self.assertEqual(provider_display_name("auto"), "OPai · Auto mode")
        self.assertEqual(provider_display_name(""), "OPai · Auto mode")
        self.assertEqual(provider_display_name("local"), "OPai · Local mode")


if __name__ == "__main__":
    unittest.main()
