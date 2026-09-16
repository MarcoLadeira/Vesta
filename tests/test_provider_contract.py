import unittest

from vesta.provider_contract import (
    classify_error_code,
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

    def test_copilot_monthly_quota_is_an_actionable_headline(self):
        for diagnostic in (
            "You have exceeded your monthly quota (Request ID: CE73:2B35E5)",
            "Copilot quota exceeded",
            "HTTP 403: You have exceeded your monthly quota",
        ):
            with self.subTest(diagnostic=diagnostic):
                error = normalize_provider_error("copilot", diagnostic)

                self.assertEqual(error["code"], "PROVIDER_QUOTA_EXHAUSTED")
                self.assertIn("quota", error["title"].lower())
                self.assertIn("switch model", error["userMessage"].lower())
                self.assertFalse(error["retryable"])

    def test_claude_monthly_spend_limit_is_visible_and_actionable(self):
        error = normalize_provider_error(
            "claude",
            "You've hit your monthly spend limit · raise it at "
            "claude.ai/settings/usage?from=cc_cli_limit_message",
        )

        self.assertEqual(error["code"], "PROVIDER_QUOTA_EXHAUSTED")
        self.assertIn("monthly spend limit", error["userMessage"].lower())
        self.assertIn("reset", error["userMessage"].lower())
        self.assertIn("claude.ai/settings/usage", error["userMessage"])
        self.assertFalse(error["retryable"])

    def test_secret_values_are_redacted(self):
        # #622: this delegates to vestahub.command_runner.redact, the one
        # canonical redactor — assignment values need to be realistically
        # long (a real cookie/token isn't 6 characters) since that pattern's
        # 16-char minimum is a deliberate anti-over-redaction guard, not an
        # oversight (a short bare value is indistinguishable from a hash).
        text = (
            "Authorization: Bearer sk-live-secret123456 "
            "api_key=token_verysecretvalue1234567890 "
            "cookie=session-secretvalue1234567890"
        )

        safe = redact_secrets(text)

        self.assertNotIn("sk-live-secret123456", safe)
        self.assertNotIn("token_verysecretvalue1234567890", safe)
        self.assertNotIn("session-secretvalue1234567890", safe)
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
        # Simple label now uses provider-prefixed model name (not Vesta generic)
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
            provider_display_name("copilot", "gpt-5.4"), "Copilot · GPT-5.4"
        )
        self.assertEqual(
            provider_display_name("copilot", "claude-haiku-4.5"),
            "Copilot · Claude Haiku",
        )

    def test_auto_and_local_labels_unchanged(self):
        self.assertEqual(provider_display_name("auto"), "Vesta · Auto mode")
        self.assertEqual(provider_display_name(""), "Vesta · Auto mode")
        self.assertEqual(provider_display_name("local"), "Vesta · Local mode")


class OutdatedProviderCliTests(unittest.TestCase):
    """A stale provider CLI is a one-command fix, not an UNKNOWN failure.

    Round 2 (2026-07-24) found Codex unusable as a fallback because its real
    diagnostic — "The 'gpt-5.6-terra' model requires a newer version of Codex"
    — classified as UNKNOWN and surfaced as "Vesta could not complete this
    request", hiding the actual remedy.
    """

    CODEX_400 = (
        'Codex reported: {"type":"error","status":400,"error":'
        '{"type":"invalid_request_error","message":"The \'gpt-5.6-terra\' model '
        "requires a newer version of Codex. Please upgrade to the latest one or "
        'CLI and try again."}}'
    )

    def test_codex_version_mismatch_is_named_and_actionable(self):
        error = normalize_provider_error("codex", self.CODEX_400, model="gpt-5.6-terra")
        self.assertEqual(error["code"], "PROVIDER_CLI_OUTDATED")
        self.assertIn("out of date", error["title"].lower())
        self.assertIn("@openai/codex", error["userMessage"])
        # Retrying the same stale CLI cannot succeed, so Auto must move on
        # rather than burn attempts on it.
        self.assertFalse(error["retryable"])

    def test_unrelated_model_errors_keep_their_own_classification(self):
        self.assertEqual(
            normalize_provider_error("codex", "unknown model: gpt-9")["code"],
            "MODEL_UNAVAILABLE",
        )
        self.assertEqual(
            normalize_provider_error("codex", "401 invalid authentication")["code"],
            "AUTH_INVALID",
        )


class TransientTransportClassificationTests(unittest.TestCase):
    """Momentary transport failures must be recognizable, not "UNKNOWN".

    These are what a user experiences as "sometimes my messages just don't
    work": a socket timeout or an overloaded endpoint used to fall through to
    the generic "Vesta could not complete this request" dead end instead of
    being absorbed by one retry.
    """

    TIMEOUTS = (
        "timed out",
        "The read operation timed out",
        "_ssl.c:1112: The handshake operation timed out",
        "HTTP 504: gateway timeout",
        "context deadline exceeded",
    )
    NETWORK = (
        "Remote end closed connection without response",
        "[Errno 104] Connection reset by peer",
        "[WinError 10053] An established connection was aborted",
        "ConnectionAbortedError",
        "[Errno 111] Connection refused",
        "EOF occurred in violation of protocol",
        "Temporary failure in name resolution",
    )
    UNAVAILABLE = (
        "provider unavailable (HTTP 503): overloaded",
        "The model is overloaded. Please try again later.",
        "HTTP 529: overloaded_error",
        "HTTP 500: internal error",
        "Stopped: the provider was temporarily unavailable.",
        "502 Bad Gateway",
    )

    def test_each_transient_shape_gets_its_transient_code(self):
        for detail in self.TIMEOUTS:
            with self.subTest(detail=detail):
                self.assertEqual(classify_error_code(detail), "PROVIDER_TIMEOUT")
        for detail in self.NETWORK:
            with self.subTest(detail=detail):
                self.assertEqual(classify_error_code(detail), "NETWORK_ERROR")
        for detail in self.UNAVAILABLE:
            with self.subTest(detail=detail):
                self.assertEqual(classify_error_code(detail), "PROVIDER_UNAVAILABLE")

    def test_transient_codes_are_the_ones_auto_retries(self):
        from vestahub import auto_router

        for detail in self.TIMEOUTS + self.NETWORK + self.UNAVAILABLE:
            with self.subTest(detail=detail):
                error = normalize_provider_error("gemini", detail)
                self.assertTrue(auto_router.is_transient_error(error), detail)

    def test_numbers_that_merely_look_like_status_codes_are_not_5xx(self):
        # The previous bare "502"/"503" substrings would read a token count as
        # an outage. A status code is now matched as a whole token.
        self.assertEqual(classify_error_code("used 1503 tokens"), "UNKNOWN")
        self.assertEqual(classify_error_code("the run took 5030 ms"), "UNKNOWN")

    def test_deterministic_failures_are_not_reclassified_as_transient(self):
        from vestahub import auto_router

        cases = {
            "429 rate limit exceeded": "PROVIDER_RATE_LIMITED",
            "401 unauthorized": "AUTH_INVALID",
            "insufficient balance, please recharge": "PROVIDER_QUOTA_EXHAUSTED",
            "The 'gpt-5.6-terra' model requires a newer version of Codex": (
                "PROVIDER_CLI_OUTDATED"
            ),
            "Error loading configuration: unknown variant, expected fast": (
                "CONFIG_INVALID"
            ),
            "unknown model: gpt-9": "MODEL_UNAVAILABLE",
            "Not logged in - Please run /login": "AUTH_MISSING",
        }
        for detail, expected in cases.items():
            with self.subTest(detail=detail):
                self.assertEqual(classify_error_code(detail), expected)
                self.assertFalse(
                    auto_router.is_transient_error(
                        normalize_provider_error("codex", detail)
                    )
                )


if __name__ == "__main__":
    unittest.main()
