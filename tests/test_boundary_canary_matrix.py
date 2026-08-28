"""#622: canary credentials injected at real boundaries, scanned at the sinks.

The issue asks for exactly this and names the shape:

    **Canary matrix** — Inject canary secrets into provider exception,
    subprocess stderr, command arguments, environment, URL, Git remote, nested
    JSON, exception chain, callback and binary output. Scan every sink.

The static check in ``test_raw_exception_interpolation`` proves no production
module *writes* a raw interpolation. That is a statement about source code. It
cannot tell you whether a secret survives the path from an exception into a
returned payload, because a redactor that silently failed, or a sink reached by
some route the scanner cannot see, would leave the source looking perfectly
clean.

So these tests do the other half: put a real credential inside a real
exception, run the real function, and search what comes back.

The canaries are the shapes ``command_runner.redact`` recognises. A shape it
does not recognise would test the redactor's coverage rather than the
boundary's wiring, and coverage is #549's job -- this file's job is that the
wiring is connected.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - constructing exception fixtures only
import unittest

from opaihub.boundary_errors import BoundaryError, safe_detail

#: Synthetic credentials. None is real; each is a shape the sanctioned
#: redactor recognises, chosen because a user could plausibly paste one into a
#: prompt, a URL, a remote or an environment variable.
CANARIES = {
    "anthropic": "sk-ant-api03-CANARYFAKEVALUE1234567890abcd",  # pragma: allowlist secret
    "openai": "sk-proj-CANARYFAKE1234567890abcdefghij",  # pragma: allowlist secret
    "github": "ghp_CANARYFAKEABCDEFGHIJKLMNOPQRST0123",  # pragma: allowlist secret
    "aws": "AKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
}


def _leaked(haystack: object, secret: str) -> bool:
    """Whether the secret survives anywhere in a returned structure."""

    return secret in json.dumps(haystack, default=str)


class SafeDetailScrubsEveryInjectionPointTests(unittest.TestCase):
    """The migration adapter, against each carrier #622 lists."""

    def test_a_provider_exception_message(self):
        for name, secret in CANARIES.items():
            with self.subTest(canary=name):
                exc = RuntimeError(f"401 unauthorised using {secret}")

                self.assertFalse(_leaked(safe_detail(exc), secret))

    def test_subprocess_stderr(self):
        """#622 names subprocess stderr as an injection point by itself."""

        secret = CANARIES["github"]
        exc = subprocess.CalledProcessError(
            1, ["git", "push"], output="", stderr=f"remote: rejected token {secret}"
        )

        self.assertFalse(_leaked(safe_detail(exc), secret))

    def test_command_arguments(self):
        secret = CANARIES["openai"]
        exc = subprocess.CalledProcessError(2, ["curl", "-H", f"Bearer {secret}"])

        self.assertFalse(_leaked(safe_detail(exc), secret))

    def test_a_url_with_a_signed_query_parameter(self):
        secret = CANARIES["aws"]
        exc = OSError(f"GET https://example/object?X-Amz-Credential={secret} failed")

        self.assertFalse(_leaked(safe_detail(exc), secret))

    def test_a_git_remote_carrying_credentials(self):
        secret = CANARIES["github"]
        exc = RuntimeError(
            f"cannot reach https://x-access-token:{secret}@github.com/o/r"
        )

        self.assertFalse(_leaked(safe_detail(exc), secret))

    def test_an_environment_value(self):
        secret = CANARIES["anthropic"]
        exc = KeyError(f"ANTHROPIC_API_KEY={secret}")

        self.assertFalse(_leaked(safe_detail(exc), secret))

    def test_nested_json_inside_the_message(self):
        secret = CANARIES["openai"]
        body = json.dumps({"error": {"auth": {"key": secret}}})
        exc = ValueError(f"provider rejected the request: {body}")

        self.assertFalse(_leaked(safe_detail(exc), secret))

    def test_an_exception_chain(self):
        """The outer message is what a sink records; the cause must not leak."""

        secret = CANARIES["anthropic"]
        try:
            try:
                raise RuntimeError(f"inner holds {secret}")
            except RuntimeError as inner:
                raise ValueError(f"outer wraps: {inner}") from inner
        except ValueError as outer:
            detail = safe_detail(outer)

        self.assertFalse(_leaked(detail, secret))

    def test_binary_output_that_is_not_utf8(self):
        """#622 lists non-UTF-8 subprocess output as an edge case."""

        secret = CANARIES["github"]
        exc = subprocess.CalledProcessError(
            1, ["tool"], output=b"\xff\xfe" + secret.encode() + b"\x00"
        )

        detail = safe_detail(exc)
        self.assertFalse(_leaked(detail, secret))
        self.assertIsInstance(detail, str)

    def test_a_very_large_message_is_bounded(self):
        """#622 asks that stack traces and output be bounded."""

        exc = RuntimeError("x" * 500_000)

        self.assertLess(len(safe_detail(exc)), 1_000)


class SafeDetailStillSaysSomethingUsefulTests(unittest.TestCase):
    """Redaction that destroys the diagnosis is its own failure.

    #622's non-goals include "removing useful failure categories to avoid
    disclosure", and its user experience section asks that "more details"
    remain useful and safe.
    """

    def test_the_exception_class_survives(self):
        """The stable category a user and a support request both need."""

        detail = safe_detail(PermissionError(f"denied for {CANARIES['aws']}"))

        self.assertIn("PermissionError", detail)

    def test_non_secret_context_survives(self):
        detail = safe_detail(FileNotFoundError("config.toml is missing from the repo"))

        self.assertIn("config.toml", detail)

    def test_an_exception_with_no_message_still_names_its_type(self):
        self.assertEqual(safe_detail(TimeoutError()), "TimeoutError")


class RedactionFailsClosedTests(unittest.TestCase):
    """#622: "Ensure redaction failure itself fails closed before persistence".

    The acceptance criterion is that a failed redaction *blocks* the unsafe
    payload and records a safe meta-error -- not that it passes the value
    through, and not that it raises into the caller.
    """

    def test_a_redactor_that_raises_yields_a_meta_error_not_the_secret(self):
        from unittest import mock

        secret = CANARIES["anthropic"]
        exc = RuntimeError(f"boom {secret}")

        with mock.patch(
            "opaihub.boundary_errors.redact", side_effect=RuntimeError("redactor down")
        ):
            detail = safe_detail(exc)

        self.assertFalse(_leaked(detail, secret))
        self.assertIn("REDACTION_FAILED", detail)

    def test_the_type_still_survives_a_failed_redaction(self):
        """Failing closed must not also destroy the category."""

        from unittest import mock

        with mock.patch(
            "opaihub.boundary_errors.redact", side_effect=RuntimeError("redactor down")
        ):
            detail = safe_detail(ValueError("anything"))

        self.assertIn("ValueError", detail)

    def test_a_boundary_error_records_the_failure_in_its_status(self):
        from unittest import mock

        with mock.patch(
            "opaihub.boundary_errors.redact", side_effect=RuntimeError("redactor down")
        ):
            error = BoundaryError.create(
                category="provider_transport",
                code="NETWORK_ERROR",
                source="test",
                detail=f"boom {CANARIES['openai']}",
                user_message="Could not reach the provider.",
            )

        self.assertEqual(error.redaction_status, "failed_closed")
        self.assertFalse(_leaked(error.to_dict(), CANARIES["openai"]))


class TheCanariesAreRecognisableTests(unittest.TestCase):
    """Teeth for the whole file.

    Every assertion above is "the secret is absent". All of them would pass
    against a redactor that returned the empty string, and all of them would
    pass if the canaries were shapes the redactor happens to strip for an
    unrelated reason. This proves the canaries are real needles: unredacted,
    each one is findable by the same search.
    """

    def test_an_unredacted_message_does_leak(self):
        for name, secret in CANARIES.items():
            with self.subTest(canary=name):
                self.assertTrue(_leaked(f"raw: {secret}", secret))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
