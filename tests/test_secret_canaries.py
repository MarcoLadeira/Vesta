"""Secret canaries must not survive into anything Vesta persists (#546/#549).

The report's release gate is "secret canaries are absent from logs, events,
receipts and support bundles", and its scenario corpus calls for "canary
secrets in logs, URLs, screenshots and nested tool payloads". Before this file
the repository had **no canary test at all** — `SECRET_PATTERNS` was only ever
exercised indirectly.

A 2026-08-04 sweep found **6 of 12** realistic credential formats surviving
`command_runner.redact()` — the single shared redactor imported by 28 modules
including `ledger.py`, `checkpoints.py`, `audit.py`, `receipt.py`,
`workflow_ledger.py` and saved chat. Two of the leaks were **Google and Groq
API keys: credentials Vesta itself asks the user to configure** for its own
free-tier providers, so a leak there is a leak of a secret Vesta requested.

Two directions are tested, because a redactor is only useful if both hold:

1. **No leak** — every canary is unrecognisable in the output.
2. **No over-redaction** — git SHAs, digests, tracebacks, token counts and URLs
   survive intact. A redactor that eats diagnostics gets switched off, and then
   it protects nothing.
"""

from __future__ import annotations

import unittest

from opaihub.command_runner import redact

# Realistic shapes only. Values are synthetic but structurally identical to the
# real thing, because structure is exactly what the patterns match on.
CANARIES: dict[str, str] = {
    "github_classic_pat": "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "github_fine_grained_pat": (
        "github_pat_11ABCDEFG0aaaaaaaaaaaa_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ),
    "openai_project_key": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
    "anthropic_key": "sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "google_api_key": "AIzaSyA1234567890abcdefghijklmnopqrstuv",
    "groq_key": "gsk_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghij",
    "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "slack_bot_token": "xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
}

# Credentials that only appear safely with assignment context. A bare 40-char
# base64 blob (an AWS secret access key, a Mistral key) is deliberately NOT
# pattern-matched on its own: it is indistinguishable from a hash or digest,
# and matching it would redact legitimate output. Honest limitation, recorded
# here rather than papered over.
ASSIGNED_CANARIES: dict[str, str] = {
    "aws_secret_access_key": (
        "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    ),
    "mistral_api_key": "MISTRAL_API_KEY=abcdefghij0123456789klmnopqrstuv",
    "generic_api_key": "api_key=abcdefghijklmnopqrstuvwxyz123456",
    "bearer_header": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
}

# Output that MUST survive: over-redaction destroys the diagnostics these
# records exist to provide.
BENIGN: tuple[str, ...] = (
    "commit a1b2c3d4e5f6789012345678901234567890abcd",
    "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934c",
    "Traceback (most recent call last): File app.py line 42",
    "tokens=15234 input_tokens=9000 output_tokens=6234",
    "AKIA is the AWS access-key prefix",
    "https://github.com/MarcoLadeira/OPai/pull/660",
    "docs/lifecycle-schema.md updated with 8 new mappings",
)


class CanaryRedactionTests(unittest.TestCase):
    def test_no_bare_canary_survives_redaction(self) -> None:
        for name, secret in CANARIES.items():
            with self.subTest(canary=name):
                self.assertNotIn(secret, redact(f"log line: {secret} end"))

    def test_no_assigned_canary_survives_redaction(self) -> None:
        for name, line in ASSIGNED_CANARIES.items():
            with self.subTest(canary=name):
                value = line.split("=", 1)[-1].split("Bearer ")[-1]
                self.assertNotIn(value, redact(f"env: {line}"))

    def test_a_canary_is_caught_anywhere_in_the_line(self) -> None:
        # Tool output and stack traces bury credentials mid-line and inside
        # nested payloads; anchoring to line starts would miss every real case.
        secret = CANARIES["google_api_key"]
        for template in (
            "{s}",
            "prefix {s}",
            '{{"headers": {{"x-goog-api-key": "{s}"}}}}',
            "curl 'https://api.example/v1?key={s}'",
            "  nested:\n    value: {s}\n",
        ):
            with self.subTest(template=template):
                self.assertNotIn(secret, redact(template.format(s=secret)))

    def test_providers_opai_asks_the_user_to_configure_are_covered(self) -> None:
        # A leaked credential Vesta itself requested is the worst case: the user
        # gave it to Vesta, so Vesta owns not spilling it into its own records.
        from opaihub.free_models import FREE_MODEL_SPECS

        configured = {spec["env_key"] for spec in FREE_MODEL_SPECS}
        self.assertIn("GOOGLE_API_KEY", configured)
        self.assertIn("GROQ_API_KEY", configured)
        self.assertNotIn(CANARIES["google_api_key"], redact(CANARIES["google_api_key"]))
        self.assertNotIn(CANARIES["groq_key"], redact(CANARIES["groq_key"]))

    def test_redaction_does_not_eat_legitimate_output(self) -> None:
        for text in BENIGN:
            with self.subTest(text=text[:40]):
                self.assertEqual(redact(text), text)

    def test_redaction_is_idempotent(self) -> None:
        # Records get redacted at more than one boundary; a second pass must not
        # corrupt an already-redacted string.
        once = redact(f"key: {CANARIES['github_classic_pat']}")
        self.assertEqual(redact(once), once)

    def test_multiple_distinct_canaries_on_one_line_are_all_removed(self) -> None:
        line = " ".join(CANARIES.values())
        cleaned = redact(line)
        for name, secret in CANARIES.items():
            with self.subTest(canary=name):
                self.assertNotIn(secret, cleaned)


class PersistenceBoundaryCanaryTests(unittest.TestCase):
    """The gate names records, not a function: check the real write paths."""

    def test_the_shared_redactor_is_what_the_persistence_modules_import(self) -> None:
        # These are the modules that write durable records. If one of them ever
        # stops routing through the shared redactor, the canary coverage above
        # silently stops protecting it — so assert the wiring, not just the
        # regexes.
        import importlib

        for module_name in (
            "opaihub.ledger",
            "opaihub.checkpoints",
            "opaihub.audit",
            "opaihub.receipt",
            "opaihub.workflow_ledger",
        ):
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(
                    hasattr(module, "redact"),
                    f"{module_name} no longer imports the shared redactor",
                )

    def test_a_canary_does_not_survive_a_ledger_summary_round_trip(self) -> None:
        # store_summary=True is the ledger path that actually persists task
        # *text* (as `task_summary_redacted`). Testing the default path instead
        # would pass vacuously — see the hash-only test below for why.
        import tempfile
        from pathlib import Path

        from opaihub.ledger import read_events, record_event

        secret = CANARIES["google_api_key"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_event(
                root, "gui_receipt", task=f"debug with {secret}", store_summary=True
            )
            events = read_events(root)
        written = str(events)
        self.assertNotIn(secret, written)
        # Proof the text really travelled this path and was scrubbed, rather
        # than the field simply being absent.
        self.assertIn("task_summary_redacted", events[0])
        self.assertIn("REDACT", str(events[0]["task_summary_redacted"]).upper())

    def test_the_default_ledger_path_stores_a_hash_not_the_prompt(self) -> None:
        # The strongest protection is not redacting the prompt but never
        # storing it. Pinning this keeps a future change from "helpfully"
        # adding raw task text to every event and relying on redaction alone.
        import tempfile
        from pathlib import Path

        from opaihub.ledger import read_events, record_event

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_event(root, "gui_receipt", task="refactor the billing module")
            event = read_events(root)[0]
        self.assertIn("task_hash", event)
        self.assertNotIn("task", event)
        self.assertNotIn("billing", str(event))


if __name__ == "__main__":
    unittest.main()
