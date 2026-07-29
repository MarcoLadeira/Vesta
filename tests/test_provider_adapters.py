import unittest

from opaihub.agent_policy import resolve_agent_policy
from opaihub.provider_adapters import (
    ExecutionRequest,
    adapter_for,
    gemini_approval_mode,
    opai_mode_for_gemini_approval,
    resolve_execution_plan,
)
from opaihub.provider_protocol import (
    AdapterRequest,
    EventKind,
    ProtocolViolation,
    ProviderEvent,
    validate_event_stream,
)


class ProviderCapabilityTests(unittest.TestCase):
    def test_editable_free_provider_gets_bounded_repository_tools(self):
        plan = resolve_execution_plan(
            adapter_for("gemini"),
            resolve_agent_policy("Fix the bug and run tests."),
            effective_mode="full-auto",
        )

        self.assertTrue(plan.allow_edits)
        self.assertEqual(plan.mode, "full-auto")
        self.assertEqual(
            plan.tools,
            ("find_files", "search_code", "read_file", "apply_patch", "run_tests"),
        )

    def test_read_only_intent_removes_mutating_tools_in_full_auto(self):
        plan = resolve_execution_plan(
            adapter_for("gemini"),
            resolve_agent_policy("Explain the code. Do not edit files."),
            effective_mode="full-auto",
        )

        self.assertFalse(plan.allow_edits)
        self.assertEqual(plan.mode, "ask")
        self.assertEqual(plan.tools, ("find_files", "search_code", "read_file"))

    def test_gemini_cli_approval_mode_matches_opai_mode(self):
        self.assertEqual(gemini_approval_mode("ask"), "plan")
        self.assertEqual(gemini_approval_mode("plan"), "plan")
        self.assertEqual(gemini_approval_mode("safe-auto"), "auto_edit")
        self.assertEqual(gemini_approval_mode("approve-edits"), "auto_edit")
        self.assertEqual(gemini_approval_mode("full-auto"), "yolo")
        self.assertEqual(gemini_approval_mode("unknown"), "plan")
        self.assertEqual(opai_mode_for_gemini_approval("plan"), "plan")
        self.assertEqual(opai_mode_for_gemini_approval("default"), "ask")
        self.assertEqual(opai_mode_for_gemini_approval("auto_edit"), "safe-auto")
        self.assertEqual(opai_mode_for_gemini_approval("yolo"), "full-auto")
        self.assertIsNone(opai_mode_for_gemini_approval("unknown"))


class AdapterProtocolBoundaryTests(unittest.TestCase):
    def _execution_request(self) -> ExecutionRequest:
        return ExecutionRequest(
            prompt="fix it",
            cwd="C:/repo",
            mode="safe-auto",
            sandbox="workspace-write",
            permission="on-request",
        )

    def test_adapter_request_is_negotiated_before_account_execution(self):
        prepared = adapter_for("codex").prepare_execution(
            self._execution_request(),
            AdapterRequest("codex", "request-1", ("chat", "repo_editing")),
        )

        self.assertEqual(prepared["provider"], "codex")
        self.assertIn("--json", prepared["command"])

    def test_public_request_validation_rejects_catalog_partial_capabilities(self):
        with self.assertRaises(ProtocolViolation):
            adapter_for("codex").validate_request(
                AdapterRequest("codex", "request-1", ("structured_output",))
            )

    def test_partial_and_unsupported_capabilities_fail_before_execution(self):
        execution = self._execution_request()
        cases = (
            ("codex", AdapterRequest("codex", "request-1", ("structured_output",))),
            ("gemini", AdapterRequest("gemini", "request-2", ("streaming",))),
        )

        for provider, adapter_request in cases:
            with self.subTest(
                provider=provider, capability=adapter_request.requested_capabilities
            ):
                with self.assertRaises(ProtocolViolation):
                    adapter_for(provider).prepare_execution(execution, adapter_request)

    def test_unknown_provider_cannot_create_an_adapter_or_protocol_request(self):
        with self.assertRaises(ValueError):
            adapter_for("not-a-provider")
        with self.assertRaises(ProtocolViolation):
            AdapterRequest("not-a-provider", "request-1", ())

    def test_legacy_execution_request_retains_its_existing_payload(self):
        prepared = adapter_for("codex").prepare_execution(self._execution_request())

        self.assertEqual(prepared["cwd"], "C:/repo")
        self.assertEqual(prepared["sandbox"], "workspace-write")
        self.assertEqual(prepared["permission"], "on-request")
        self.assertNotIn("protocolRequest", prepared)

    def test_parsed_provider_output_is_exposed_as_safe_caller_managed_observations(
        self,
    ):
        normalized = adapter_for("claude").normalize_event(
            {
                "type": "result",
                "result": "done",
                "total_cost_usd": 1.25,
            }
        )

        self.assertEqual(normalized["text"], "done")
        self.assertEqual(normalized["cost"], None)
        self.assertFalse(normalized["done"])
        self.assertNotIn("protocolEvents", normalized)
        follow_up = adapter_for("claude").normalize_event(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "next"}]},
            }
        )
        self.assertEqual(
            [
                *normalized["protocolObservations"],
                *follow_up["protocolObservations"],
            ],
            [
                {
                    "kind": "text_delta",
                    "payload": {"text": "done"},
                },
                {
                    "kind": "text_delta",
                    "payload": {"text": "next"},
                },
            ],
        )
        events = [ProviderEvent(1, 0.0, EventKind.STARTED)]
        events.extend(
            ProviderEvent(sequence, 0.1, observation["kind"], observation["payload"])
            for sequence, observation in enumerate(
                [
                    *normalized["protocolObservations"],
                    *follow_up["protocolObservations"],
                ],
                start=2,
            )
        )
        events.append(
            ProviderEvent(len(events) + 1, 0.2, EventKind.TERMINAL, {"state": "failed"})
        )

        self.assertEqual(validate_event_stream(events), tuple(events))


if __name__ == "__main__":
    unittest.main()
