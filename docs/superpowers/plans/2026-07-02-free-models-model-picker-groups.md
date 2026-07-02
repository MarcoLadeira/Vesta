# Free Models + Model Picker Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add free API models (DeepSeek, Gemini, Groq, Mistral) to OPai's model picker and fix the picker so models are displayed with descriptive provider-prefixed labels ("Claude · Sonnet 4.6") grouped by Claude | Codex | Copilot | Free models | OPai routing | Local models, replacing the broken "OPai · Balanced/Fast/Powerful mode" labels that repeat identically for every model.

**Architecture:** Python data layer adds `FREE_MODEL_SPECS` + `FreeAPIRunner`, `provider_display_name()` returns provider-prefixed labels, all model options carry a `group` field. The JS picker renders `<optgroup>` elements per group. Free API models appear always (grayed when no API key) and route through the policy confirmation gate since they hit public endpoints.

**Tech Stack:** Python 3.13, stdlib urllib (no new deps), JavaScript ES2020, Playwright (E2E), Vitest (unit), pytest (Python unit)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `opaihub/free_models.py` | **Create** | FREE_MODEL_SPECS list, list_free_models(), spec_for_model_id() |
| `opaihub/local_runner.py` | **Modify** | Add FreeAPIRunner, update _http_json, update runner_for_model |
| `opai/provider_contract.py` | **Modify** | Fix provider_display_name() to return provider-prefixed labels |
| `opaihub/accounts.py` | **Modify** | Add group field to all _account_options() entries |
| `opai/app_state.py` | **Modify** | Add free models to available_models(), add _ask_free_model() |
| `opai/assets/web/app.js` | **Modify** | renderComposerSelects uses optgroup via PICKER_GROUPS |
| `tests/test_free_models.py` | **Create** | Python unit tests for free_models + FreeAPIRunner |
| `tests/test_provider_contract.py` | **Modify** | Update label assertions to new format |
| `tests/test_provider_connections.py` | **Modify** | Update OPai-first label assertion |
| `opai/assets/web/__tests__/e2e/helpers/fixtures.js` | **Modify** | Add group fields, free model entries |
| `opai/assets/web/__tests__/e2e/model-mode.spec.js` | **Modify** | Add optgroup / group assertions |
| `opai/assets/web/__tests__/e2e/free-models.spec.js` | **Create** | E2E tests for free model behavior |

---

## Task 1: Create branch

- [ ] **Step 1.1: Create feature branch**

```bash
cd C:\Users\Frist\.opai\source
git checkout -b feat/free-models-model-picker-groups
```

Expected: `Switched to a new branch 'feat/free-models-model-picker-groups'`

---

## Task 2: Free models module (TDD)

**Files:**
- Create: `opaihub/free_models.py`
- Create: `tests/test_free_models.py`

- [ ] **Step 2.1: Write the failing tests**

Create `tests/test_free_models.py`:

```python
"""Tests for the free API model registry and picker options."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from opaihub.free_models import (
    FREE_MODEL_SPECS,
    list_free_models,
    spec_for_model_id,
)


class FreeModelSpecsTests(unittest.TestCase):
    def test_spec_ids_are_unique(self):
        ids = [s["id"] for s in FREE_MODEL_SPECS]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate spec IDs detected")

    def test_all_specs_have_required_fields(self):
        required = {"id", "label", "advanced_label", "provider", "model_id",
                    "api_base", "env_key", "group", "setup_hint"}
        for spec in FREE_MODEL_SPECS:
            missing = required - spec.keys()
            self.assertFalse(missing, f"Spec '{spec.get('id')}' missing: {missing}")

    def test_all_specs_have_free_group(self):
        for spec in FREE_MODEL_SPECS:
            self.assertEqual(spec["group"], "free", f"Spec '{spec.get('id')}' has wrong group")

    def test_five_free_models_defined(self):
        self.assertGreaterEqual(len(FREE_MODEL_SPECS), 5)

    def test_deepseek_specs_present(self):
        ids = {s["id"] for s in FREE_MODEL_SPECS}
        self.assertIn("free:deepseek:deepseek-chat", ids)
        self.assertIn("free:deepseek:deepseek-reasoner", ids)

    def test_gemini_groq_mistral_present(self):
        ids = {s["id"] for s in FREE_MODEL_SPECS}
        self.assertIn("free:gemini:gemini-2.0-flash", ids)
        self.assertIn("free:groq:llama-3.3-70b-versatile", ids)
        self.assertIn("free:mistral:mistral-small-latest", ids)

    def test_spec_for_known_id(self):
        spec = spec_for_model_id("free:deepseek:deepseek-chat")
        self.assertIsNotNone(spec)
        self.assertEqual(spec["provider"], "deepseek")
        self.assertEqual(spec["model_id"], "deepseek-chat")

    def test_spec_for_unknown_id_returns_none(self):
        self.assertIsNone(spec_for_model_id("free:unknown:model"))
        self.assertIsNone(spec_for_model_id(""))


class ListFreeModelsTests(unittest.TestCase):
    def _no_keys(self):
        """Env with all free model keys cleared."""
        return {s["env_key"]: "" for s in FREE_MODEL_SPECS}

    def test_list_returns_all_specs_as_options(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        self.assertEqual(len(models), len(FREE_MODEL_SPECS))

    def test_grayed_without_api_key(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            with self.subTest(model=model["id"]):
                self.assertFalse(model["available"])
                self.assertIsNotNone(model["disabled_reason"])
                self.assertTrue(model["disabled_reason"])

    def test_enabled_with_api_key(self):
        spec = FREE_MODEL_SPECS[0]  # deepseek-chat
        env = self._no_keys()
        env[spec["env_key"]] = "test-key-abc"
        with mock.patch.dict(os.environ, env):
            models = list_free_models()
        provider_models = [m for m in models if m["provider"] == spec["provider"]]
        self.assertTrue(
            any(m["available"] for m in provider_models),
            "At least one model for the provider should be enabled when key is set",
        )

    def test_group_is_always_free(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            self.assertEqual(model["group"], "free")

    def test_all_picker_fields_present(self):
        required = {"id", "label", "advanced_label", "provider", "model",
                    "kind", "group", "paid", "available", "disabled_reason"}
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            missing = required - model.keys()
            self.assertFalse(missing, f"'{model.get('id')}' missing picker fields: {missing}")

    def test_paid_is_false_for_all_free_models(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            self.assertFalse(model["paid"])

    def test_kind_is_free(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            self.assertEqual(model["kind"], "free")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2.2: Run to confirm it fails**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_free_models.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'opaihub.free_models'`

- [ ] **Step 2.3: Create `opaihub/free_models.py`**

```python
"""Free API model registry for OPai.

Defines FREE_MODEL_SPECS for providers that offer free API tiers (DeepSeek,
Google Gemini, Groq, Mistral). Models appear in the picker even without a key
— grayed with a setup hint — so users can discover free options.

Execution: free models hit public endpoints and always go through OPai's
confirmation gate (same as other cloud routes). No network calls happen here;
availability is determined solely by env var presence.
"""

from __future__ import annotations

import os
from typing import Any

FREE_MODEL_SPECS: list[dict[str, Any]] = [
    {
        "id": "free:deepseek:deepseek-chat",
        "label": "DeepSeek · V3 Chat (free)",
        "advanced_label": "DeepSeek V3 Chat via DeepSeek API (free tier)",
        "provider": "deepseek",
        "model_id": "deepseek-chat",
        "api_base": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set DEEPSEEK_API_KEY env var. "
            "Get a free key at platform.deepseek.com"
        ),
    },
    {
        "id": "free:deepseek:deepseek-reasoner",
        "label": "DeepSeek · R1 Reasoner (free)",
        "advanced_label": "DeepSeek R1 Reasoning Model via DeepSeek API (free tier)",
        "provider": "deepseek",
        "model_id": "deepseek-reasoner",
        "api_base": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set DEEPSEEK_API_KEY env var. "
            "Get a free key at platform.deepseek.com"
        ),
    },
    {
        "id": "free:gemini:gemini-2.0-flash",
        "label": "Gemini · 2.0 Flash (free)",
        "advanced_label": "Google Gemini 2.0 Flash via Google AI API (free tier)",
        "provider": "gemini",
        "model_id": "gemini-2.0-flash",
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GOOGLE_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set GOOGLE_API_KEY env var. "
            "Get a free key at aistudio.google.com"
        ),
    },
    {
        "id": "free:groq:llama-3.3-70b-versatile",
        "label": "Groq · Llama 3.3 (free)",
        "advanced_label": "Meta Llama 3.3 70B via Groq API (free tier, very fast inference)",
        "provider": "groq",
        "model_id": "llama-3.3-70b-versatile",
        "api_base": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set GROQ_API_KEY env var. "
            "Get a free key at console.groq.com"
        ),
    },
    {
        "id": "free:mistral:mistral-small-latest",
        "label": "Mistral · Small (free)",
        "advanced_label": "Mistral Small via Mistral AI API (free/low-cost tier)",
        "provider": "mistral",
        "model_id": "mistral-small-latest",
        "api_base": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set MISTRAL_API_KEY env var. "
            "Get a free key at console.mistral.ai"
        ),
    },
]

_SPEC_BY_ID: dict[str, dict[str, Any]] = {s["id"]: s for s in FREE_MODEL_SPECS}


def list_free_models() -> list[dict[str, Any]]:
    """Return picker entries for all free API models.

    Every model appears regardless of key presence. Models without a configured
    API key are returned with ``available=False`` and a ``disabled_reason``
    containing the setup hint, matching the pattern used for unavailable
    account models.
    """
    options: list[dict[str, Any]] = []
    for spec in FREE_MODEL_SPECS:
        api_key = os.environ.get(spec["env_key"], "").strip()
        available = bool(api_key)
        options.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "advanced_label": spec["advanced_label"],
                "provider": spec["provider"],
                "model": spec["model_id"],
                "kind": spec["kind"],
                "group": spec["group"],
                "paid": spec["paid"],
                "available": available,
                "disabled_reason": None if available else spec["setup_hint"],
                "api_base": spec["api_base"],
                "env_key": spec["env_key"],
            }
        )
    return options


def spec_for_model_id(model_id: str) -> dict[str, Any] | None:
    """Return the spec dict for a picker id like ``'free:deepseek:deepseek-chat'``."""
    return _SPEC_BY_ID.get(str(model_id or ""))
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_free_models.py -v
```

Expected: All tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add opaihub/free_models.py tests/test_free_models.py
git commit -m "feat(models): add free API model registry (DeepSeek, Gemini, Groq, Mistral)

FREE_MODEL_SPECS defines 5 free-tier models always visible in the picker.
list_free_models() returns grayed entries when no API key is set.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: FreeAPIRunner (TDD)

**Files:**
- Modify: `opaihub/local_runner.py`
- Modify: `tests/test_free_models.py` (add runner tests)

- [ ] **Step 3.1: Add runner tests to `tests/test_free_models.py`**

Append this class at the end of the file (before `if __name__ == "__main__"`):

```python
class FreeAPIRunnerTests(unittest.TestCase):
    def test_runner_available_with_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.deepseek.com/v1", "deepseek-chat", "test-key")
        self.assertTrue(runner.available())

    def test_runner_unavailable_without_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.deepseek.com/v1", "deepseek-chat", "")
        self.assertFalse(runner.available())

    def test_runner_unavailable_with_whitespace_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.deepseek.com/v1", "deepseek-chat", "   ")
        self.assertFalse(runner.available())

    def test_runner_for_free_model_id_with_key(self):
        from opaihub.local_runner import FreeAPIRunner, runner_for_model

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key-abc"}):
            runner = runner_for_model("free:deepseek:deepseek-chat")

        self.assertIsInstance(runner, FreeAPIRunner)
        self.assertTrue(runner.available())

    def test_runner_for_free_model_id_without_key(self):
        from opaihub.local_runner import FreeAPIRunner, runner_for_model

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            runner = runner_for_model("free:deepseek:deepseek-chat")

        self.assertIsInstance(runner, FreeAPIRunner)
        self.assertFalse(runner.available())

    def test_runner_for_unknown_free_model_returns_none(self):
        from opaihub.local_runner import runner_for_model

        runner = runner_for_model("free:unknown:nonexistent")
        self.assertIsNone(runner)

    def test_runner_complete_sends_auth_header(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.deepseek.com/v1", "deepseek-chat", "sk-test-123")
        mock_response = {"choices": [{"message": {"content": "Test response"}}]}

        captured_headers: dict = {}

        def fake_http(url, *, method="GET", payload=None, timeout=60.0, extra_headers=None):
            captured_headers.update(extra_headers or {})
            return mock_response

        with mock.patch("opaihub.local_runner._http_json", side_effect=fake_http):
            result = runner.complete("What is 2+2?")

        self.assertEqual(result, "Test response")
        self.assertIn("Authorization", captured_headers)
        self.assertEqual(captured_headers["Authorization"], "Bearer sk-test-123")

    def test_runner_complete_with_system_prompt(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "key")
        mock_response = {"choices": [{"message": {"content": "OK"}}]}
        captured_payload: dict = {}

        def fake_http(url, *, method="GET", payload=None, timeout=60.0, extra_headers=None):
            captured_payload.update(payload or {})
            return mock_response

        with mock.patch("opaihub.local_runner._http_json", side_effect=fake_http):
            runner.complete("Hello", system="You are a coding assistant.")

        messages = captured_payload.get("messages", [])
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "You are a coding assistant.")
```

- [ ] **Step 3.2: Run to confirm new tests fail**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_free_models.py::FreeAPIRunnerTests -v
```

Expected: `ImportError: cannot import name 'FreeAPIRunner' from 'opaihub.local_runner'`

- [ ] **Step 3.3: Update `opaihub/local_runner.py`**

**3.3a: Update `_http_json` to accept `extra_headers`** — find the existing `_http_json` function and replace it:

```python
def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(  # nosec B310 - scheme validated by caller (loopback only)
        url, data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))
```

**3.3b: Add `FreeAPIRunner` class** — insert after the `OpenAICompatibleRunner` class:

```python
class FreeAPIRunner(OpenAICompatibleRunner):
    """OpenAI-compatible runner for free API tiers (DeepSeek, Groq, Gemini, Mistral).

    Unlike local runners, these reach public endpoints and require an API key
    stored in an env var.  ``available()`` checks key presence only — no network
    ping — to avoid latency in the model picker.  All calls go through OPai's
    policy confirmation gate (``requires_confirmation=True``) because they hit
    a public host.
    """

    name = "free-api"

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        super().__init__(base_url, model)
        self._api_key = api_key.strip()

    def available(self) -> bool:
        return bool(self._api_key)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def complete(
        self, prompt: str, *, system: str | None = None, timeout: float = 60.0
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        result = _http_json(
            f"{self.base_url}/chat/completions",
            method="POST",
            payload={"model": self.model, "messages": messages, "stream": False},
            timeout=timeout,
            extra_headers=self._auth_headers(),
        )
        choices = result.get("choices") or [{}]
        return str((choices[0].get("message") or {}).get("content", "")).strip()
```

**3.3c: Update `runner_for_model`** — add `free:` prefix handling at the top of the function body, before the existing `provider, name = model_id.split(":", 1)` line:

```python
def runner_for_model(
    model_id: str, project_root: Path | None = None
) -> LocalRunner | None:
    """Build a runner bound to a specific ``provider:model`` id from the picker."""
    if not model_id or ":" not in model_id:
        return None

    # Free API models: "free:<provider>:<model_id>"
    if model_id.startswith("free:"):
        from .free_models import spec_for_model_id

        spec = spec_for_model_id(model_id)
        if spec is None:
            return None
        api_key = os.environ.get(spec["env_key"], "").strip()
        return FreeAPIRunner(spec["api_base"], spec["model_id"], api_key)

    provider, name = model_id.split(":", 1)
    for _url, runner in _candidate_runners():
        if provider == "ollama" and isinstance(runner, OllamaRunner):
            return OllamaRunner(runner.base_url, name)
        if provider in {"openai", "openai-compatible"} and isinstance(
            runner, OpenAICompatibleRunner
        ):
            return OpenAICompatibleRunner(runner.base_url, name)
    if provider == "ollama":
        return OllamaRunner(DEFAULT_OLLAMA_URL, name)
    return None
```

Also add `import os` at the top of the file if not already present (check — it is not currently imported in local_runner.py). Add it after the existing imports.

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_free_models.py -v
```

Expected: All tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add opaihub/local_runner.py tests/test_free_models.py
git commit -m "feat(runner): add FreeAPIRunner for free-tier API models

- _http_json gains optional extra_headers for Authorization support
- FreeAPIRunner checks key presence for available(), injects Bearer header
- runner_for_model handles 'free:<provider>:<model>' prefix

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 4: Fix `provider_display_name()` (TDD)

**Files:**
- Modify: `opai/provider_contract.py`
- Modify: `tests/test_provider_contract.py`

- [ ] **Step 4.1: Update the failing test in `tests/test_provider_contract.py`**

Replace the existing `test_provider_details_are_advanced_only` method and add new tests:

```python
def test_provider_details_are_advanced_only(self):
    # Simple label now uses provider prefix, not OPai generic label
    self.assertEqual(provider_display_name("claude", "haiku"), "Claude · Haiku 4.5")
    # Advanced label is unchanged (full diagnostic string)
    self.assertEqual(
        provider_display_name("claude", "haiku", advanced=True),
        "Claude Haiku 4.5 via Anthropic account connector",
    )

def test_claude_model_labels(self):
    self.assertEqual(provider_display_name("claude", "sonnet"), "Claude · Sonnet 4.6")
    self.assertEqual(provider_display_name("claude", "opus"), "Claude · Opus 4.8")
    self.assertEqual(provider_display_name("claude", "haiku"), "Claude · Haiku 4.5")

def test_codex_model_labels(self):
    self.assertEqual(provider_display_name("codex", "gpt-5.5"), "Codex · GPT-5.5")
    self.assertEqual(provider_display_name("codex", "gpt-5.4"), "Codex · GPT-5.4")
    self.assertEqual(provider_display_name("codex", "gpt-5.4-mini"), "Codex · GPT-5.4 Mini")
    self.assertEqual(provider_display_name("codex", "gpt-5.3-codex-spark"), "Codex · Spark")

def test_copilot_model_labels(self):
    self.assertEqual(
        provider_display_name("copilot", "claude-sonnet-4.6"), "Copilot · Claude Sonnet"
    )
    self.assertEqual(provider_display_name("copilot", "gpt-5.2"), "Copilot · GPT-5.2")
    self.assertEqual(
        provider_display_name("copilot", "claude-haiku-4.5"), "Copilot · Claude Haiku"
    )

def test_auto_and_local_labels_unchanged(self):
    self.assertEqual(provider_display_name("auto"), "OPai · Auto mode")
    self.assertEqual(provider_display_name(""), "OPai · Auto mode")
    self.assertEqual(provider_display_name("local"), "OPai · Local mode")
```

- [ ] **Step 4.2: Run to confirm updated tests fail**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_provider_contract.py -v
```

Expected: `test_provider_details_are_advanced_only` FAILS (expected "Claude · Haiku 4.5", got "OPai · Fast mode").

- [ ] **Step 4.3: Rewrite `provider_display_name()` in `opai/provider_contract.py`**

Replace the existing `provider_display_name` function (lines ~276-302) with:

```python
# Lookup tables for the simple (non-advanced) picker label.
_CLAUDE_DISPLAY: dict[str, str] = {
    "haiku": "Haiku 4.5",
    "sonnet": "Sonnet 4.6",
    "opus": "Opus 4.8",
}
_CODEX_DISPLAY: dict[str, str] = {
    "gpt-5.5": "GPT-5.5",
    "gpt-5.4": "GPT-5.4",
    "gpt-5.4-mini": "GPT-5.4 Mini",
    "gpt-5.3-codex-spark": "Spark",
}
_COPILOT_DISPLAY: dict[str, str] = {
    "claude-sonnet-4.6": "Claude Sonnet",
    "claude-haiku-4.5": "Claude Haiku",
    "gpt-5.2": "GPT-5.2",
}


def provider_display_name(
    provider: str, model: str | None = None, *, advanced: bool = False
) -> str:
    """Return a picker label for a provider/model pair.

    Simple (``advanced=False``): provider-prefixed model name used directly in
    the model picker — e.g. ``"Claude · Sonnet 4.6"``.

    Advanced (``advanced=True``): full diagnostic string for the inspector and
    hover tooltip — e.g. ``"Claude Sonnet 4.6 via Anthropic account connector"``.
    The advanced format is unchanged from the previous implementation.
    """
    provider_id = str(provider or "").lower()
    model_id = str(model or "").lower()

    if advanced:
        if provider_id == "claude":
            models = {"haiku": "Haiku 4.5", "sonnet": "Sonnet 4.6", "opus": "Opus 4.8"}
            return f"Claude {models.get(model_id, model or 'account')} via Anthropic account connector"
        if provider_id == "codex":
            return f"Codex {model or 'account'} via OpenAI account connector"
        if provider_id == "copilot":
            return f"GitHub Copilot {model or 'account'} via account connector"
        if provider_id == "local":
            return f"{model or 'Local model'} on this device"
        return f"{provider or 'Automatic'} {model or ''}".strip()

    # Simple picker label: provider-prefixed model name.
    if provider_id == "claude":
        display = _CLAUDE_DISPLAY.get(model_id, model or "model")
        return f"Claude · {display}"
    if provider_id == "codex":
        display = _CODEX_DISPLAY.get(model_id, model or "model")
        return f"Codex · {display}"
    if provider_id == "copilot":
        display = _COPILOT_DISPLAY.get(model_id, model or "model")
        return f"Copilot · {display}"
    if provider_id == "local":
        return "OPai · Local mode"
    if provider_id in {"auto", ""}:
        return "OPai · Auto mode"
    # Generic fallback: keeps OPai branding for any unrecognized provider.
    return f"OPai · {(model or provider or 'model').strip()}"
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_provider_contract.py -v
```

Expected: All tests PASS.

- [ ] **Step 4.5: Commit**

```bash
git add opai/provider_contract.py tests/test_provider_contract.py
git commit -m "feat(ui): fix provider_display_name to return descriptive labels

Before: 'OPai · Balanced mode' for every connected model (broken)
After:  'Claude · Sonnet 4.6', 'Codex · GPT-5.5', 'Copilot · Claude Sonnet'

Lookup tables CLAUDE_DISPLAY / CODEX_DISPLAY / COPILOT_DISPLAY map model
aliases to readable names. advanced=True keeps diagnostic format unchanged.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 5: Add `group` field to account models (TDD)

**Files:**
- Modify: `opaihub/accounts.py`
- Modify: `tests/test_provider_connections.py`

- [ ] **Step 5.1: Update `tests/test_provider_connections.py`**

**Replace** `test_account_picker_is_opai_first_with_advanced_provider_detail` with:

```python
def test_account_picker_has_provider_prefixed_labels(self):
    account = {
        "id": "claude",
        "label": "Claude",
        "vendor": "Anthropic Claude Code",
        "cli": "claude",
        "cli_path": "/bin/claude",
        "cli_present": True,
        "authenticated": True,
        "connected": True,
        "login_hint": "Sign in",
    }
    with mock.patch(
        "opaihub.accounts.list_connected_accounts", return_value=[account]
    ):
        options = account_models()

    # Labels now use provider prefix, not OPai generic labels
    self.assertTrue(
        all(option["label"].startswith("Claude ·") for option in options),
        f"Expected all labels to start with 'Claude ·', got: {[o['label'] for o in options]}",
    )
    self.assertIn("Claude", options[0]["advanced_label"])

def test_account_options_include_group_field(self):
    account = {
        "id": "claude",
        "label": "Claude",
        "vendor": "Anthropic Claude Code",
        "cli": "claude",
        "cli_path": "/bin/claude",
        "cli_present": True,
        "authenticated": True,
        "connected": True,
        "login_hint": "Sign in",
    }
    with mock.patch(
        "opaihub.accounts.list_connected_accounts", return_value=[account]
    ):
        options = account_models()

    for opt in options:
        self.assertEqual(opt.get("group"), "claude", f"Option {opt['id']} missing group='claude'")

def test_codex_options_have_codex_group(self):
    account = {
        "id": "codex",
        "label": "Codex",
        "vendor": "OpenAI Codex CLI",
        "cli": "codex",
        "cli_path": "/bin/codex",
        "cli_present": True,
        "authenticated": True,
        "connected": True,
        "login_hint": "Sign in",
    }
    with mock.patch(
        "opaihub.accounts.list_connected_accounts", return_value=[account]
    ):
        options = account_models()

    for opt in options:
        self.assertEqual(opt.get("group"), "codex")

def test_copilot_options_have_copilot_group(self):
    account = {
        "id": "copilot",
        "label": "Copilot",
        "vendor": "GitHub Copilot CLI",
        "cli": "copilot",
        "cli_path": "/bin/copilot",
        "cli_present": True,
        "authenticated": True,
        "connected": True,
        "login_hint": "Sign in",
    }
    with mock.patch(
        "opaihub.accounts.list_connected_accounts", return_value=[account]
    ):
        options = account_models()

    for opt in options:
        self.assertEqual(opt.get("group"), "copilot")
```

- [ ] **Step 5.2: Run to confirm new/updated tests fail**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_provider_connections.py -v
```

Expected: `test_account_picker_has_provider_prefixed_labels` and group tests FAIL.

- [ ] **Step 5.3: Update `opaihub/accounts.py` — add `group` to `_account_options()`**

In the `_account_options()` function, add `"group": account["id"]` to every returned dict. The account id is `"claude"`, `"codex"`, or `"copilot"`, which are exactly the group keys we need.

Find each return block in `_account_options()` and add the `group` key:

**Claude block** (inside the `if account["id"] == "claude":` return):
```python
return [
    {
        "id": f"account:claude:{alias}",
        "label": provider_display_name("claude", alias),
        "advanced_label": provider_display_name("claude", alias, advanced=True),
        "provider": "claude",
        "model": alias,
        "kind": "account",
        "group": "claude",          # ← add this
        "paid": True,
        "vendor": account["vendor"],
        "connected": connected,
        "available": connected,
        "disabled_reason": disabled_reason,
    }
    for alias, label in CLAUDE_MODELS
]
```

**Codex block** (inside `if account["id"] == "codex":`):
```python
return [
    {
        "id": f"account:codex:{model_id}",
        "label": provider_display_name("codex", model_id),
        "advanced_label": provider_display_name("codex", label, advanced=True),
        "provider": "codex",
        "model": model_id,
        "kind": "account",
        "group": "codex",           # ← add this
        "paid": True,
        "vendor": account["vendor"],
        "speed": speed,
        "connected": connected,
        "available": connected,
        "disabled_reason": disabled_reason,
    }
    for model_id, label, speed in CODEX_MODELS
]
```

**Copilot block** (inside `if account["id"] == "copilot":`):
```python
return [
    {
        "id": f"account:copilot:{model_id}",
        "label": provider_display_name("copilot", model_id),
        "advanced_label": provider_display_name("copilot", label, advanced=True),
        "provider": "copilot",
        "model": model_id,
        "kind": "account",
        "group": "copilot",         # ← add this
        "paid": True,
        "vendor": account["vendor"],
        "speed": speed,
        "connected": connected,
        "available": connected,
        "disabled_reason": disabled_reason,
    }
    for model_id, label, speed in COPILOT_MODELS
]
```

**Generic fallback block** (the final `return [...]` in `_account_options()`):
```python
return [
    {
        "id": f"account:{account['id']}",
        "label": provider_display_name(account["id"]),
        "advanced_label": provider_display_name(account["id"], advanced=True),
        "provider": account["id"],
        "model": "",
        "kind": "account",
        "group": account["id"],     # ← add this
        "paid": True,
        "vendor": account["vendor"],
        "connected": connected,
        "available": connected,
        "disabled_reason": disabled_reason,
    }
]
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_provider_connections.py -v
```

Expected: All tests PASS.

- [ ] **Step 5.5: Run the full Python suite to catch regressions**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: All existing tests pass. If `test_gui_web.py` or `test_desktop_gui.py` fail because they check model labels, update them (see note below Step 5.5).

> **Note on regressions:** If any test asserts the old `"OPai · Balanced mode"` / `"OPai · Fast mode"` / `"OPai · Powerful mode"` label format, update that test to expect the new provider-prefixed format. These are intentional breaking changes to the broken behavior.

- [ ] **Step 5.6: Commit**

```bash
git add opaihub/accounts.py tests/test_provider_connections.py
git commit -m "feat(models): add group field to all account model options

Each model option now carries group='claude'/'codex'/'copilot' so the
JS picker can render <optgroup> sections. Tests updated to match new
provider-prefixed label format (dropping OPai-generic labels).

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 6: Wire free models into `available_models()` and `ask()` (TDD)

**Files:**
- Modify: `opai/app_state.py`
- Modify: `tests/test_provider_connections.py` (add available_models group test)

- [ ] **Step 6.1: Add `available_models` group tests to `tests/test_provider_connections.py`**

Append after the existing tests:

```python
def test_available_models_includes_free_models(self):
    with tempfile.TemporaryDirectory() as tmp:
        with (
            mock.patch("opaihub.accounts.list_connected_accounts", return_value=[]),
            mock.patch("opaihub.local_runner.list_local_models", return_value=[]),
        ):
            payload = available_models(Path(tmp))

    groups = {m.get("group") for m in payload["models"]}
    self.assertIn("free", groups, "Free models should always appear in available_models()")
    self.assertIn("routing", groups, "Auto routing model should be present")

def test_available_models_auto_has_routing_group(self):
    with tempfile.TemporaryDirectory() as tmp:
        with (
            mock.patch("opaihub.accounts.list_connected_accounts", return_value=[]),
            mock.patch("opaihub.local_runner.list_local_models", return_value=[]),
        ):
            payload = available_models(Path(tmp))

    auto_model = next((m for m in payload["models"] if m["id"] == "auto"), None)
    self.assertIsNotNone(auto_model)
    self.assertEqual(auto_model.get("group"), "routing")

def test_available_models_local_models_have_local_group(self):
    local_model = {
        "id": "ollama:qwen2.5-coder",
        "provider": "ollama",
        "model": "qwen2.5-coder:7b",
        "endpoint": "http://127.0.0.1:11434",
    }
    with tempfile.TemporaryDirectory() as tmp:
        with (
            mock.patch("opaihub.accounts.list_connected_accounts", return_value=[]),
            mock.patch("opaihub.local_runner.list_local_models", return_value=[local_model]),
        ):
            payload = available_models(Path(tmp))

    local_options = [m for m in payload["models"] if m.get("group") == "local"]
    self.assertTrue(len(local_options) > 0, "Local model should appear with group='local'")
    self.assertEqual(local_options[0]["id"], "ollama:qwen2.5-coder")
```

- [ ] **Step 6.2: Run to confirm tests fail**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_provider_connections.py::ProviderConnectionTests::test_available_models_includes_free_models -v
```

Expected: FAIL — `"free" not in groups`.

- [ ] **Step 6.3: Update `opai/app_state.py` — `available_models()`**

Find the `available_models()` function. Replace the section that builds `options` (from the `accounts = account_models()` line through the `options.append({...auto...})` and local model loop) with:

```python
def available_models(project_root: Path) -> dict[str, Any]:
    """Pickable models: connected accounts first, then free API, then Auto, then local.

    Accounts (Claude/Codex/Copilot via logged-in CLIs) are the headline path.
    Free API models (DeepSeek, Gemini, Groq, Mistral) appear always — grayed
    when no key is set. Auto routes cheapest safe option. Local models are the
    free, advanced fallback. Read-only — never installs, downloads, or signs in.
    """
    from opaihub.accounts import (
        account_connections,
        account_models,
        list_connected_accounts,
    )
    from opaihub.free_models import list_free_models
    from opaihub.local_runner import list_local_models

    accounts = account_models()
    account_catalog = account_models(include_unavailable=True)
    local = list_local_models(project_root)
    options: list[dict[str, Any]] = []

    # 1. Connected account models (Claude | Codex | Copilot) — group already set
    for account in accounts:
        options.append(
            {
                "id": account["id"],
                "label": account["label"],
                "advanced_label": account.get("advanced_label", account["label"]),
                "kind": "account",
                "group": account.get("group", "account"),
                "paid": True,
                "provider": account["provider"],
                "model": account.get("model", ""),
                "available": account.get("available", True),
                "disabled_reason": account.get("disabled_reason"),
            }
        )

    # 2. Free API models (always shown, grayed when no key)
    options.extend(list_free_models())

    # 3. OPai Auto routing
    options.append(
        {
            "id": "auto",
            "label": "OPai · Auto mode",
            "advanced_label": "Automatic local-first routing",
            "kind": "auto",
            "group": "routing",
        }
    )

    # 4. Local models (ollama, lmstudio, etc.)
    for model in local:
        options.append(
            {
                "id": model["id"],
                "label": "OPai · Local mode",
                "advanced_label": f"{model['model']} via {model['provider']} on this device",
                "kind": "local",
                "group": "local",
                "endpoint": model["endpoint"],
            }
        )

    if accounts or local:
        hint = None
    else:
        hint = (
            "No AI account connected. Sign in to Claude, Codex, or Copilot (run "
            "`claude`, `codex`, or `copilot` once), or add a local model under Advanced."
        )
    return {
        "models": options,
        "available_models": options,
        "account_models": account_catalog,
        "accounts": list_connected_accounts(),
        "connections": account_connections(),
        "account_count": len(accounts),
        "account_model_count": len(accounts),
        "local_count": len(local),
        "setup": model_setup(project_root),
        "hint": hint,
    }
```

- [ ] **Step 6.4: Add `_ask_free_model()` helper to `opai/app_state.py`**

Add this function after `_ask_account()`:

```python
def _ask_free_model(
    project_root: Path,
    task: str,
    model_choice: str,
    *,
    allow_edits: bool = False,
    on_event: Any = None,
    on_text: Any = None,
    cancel: Any = None,
) -> dict[str, Any]:
    """Run a task via a free API model (DeepSeek, Gemini, Groq, Mistral).

    Requires the model's env var API key to be set. Always recorded as a
    cloud call and subject to the policy gate (public endpoint).
    """
    from opaihub.free_models import spec_for_model_id
    from opaihub.local_runner import FreeAPIRunner

    spec = spec_for_model_id(model_choice)
    if spec is None:
        return {
            "status": "error",
            "answer": f"Unknown free model: {model_choice}",
            "requestId": None,
        }

    import os

    api_key = os.environ.get(spec["env_key"], "").strip()
    if not api_key:
        return {
            "status": "error",
            "answer": (
                f"No API key configured for {spec['provider']}. "
                f"{spec['setup_hint']}"
            ),
            "requestId": None,
        }

    from opaihub.ask import run_ask

    runner = FreeAPIRunner(spec["api_base"], spec["model_id"], api_key)
    return run_ask(
        project_root, task, runner=runner, record=True, allow_cloud=True
    )
```

- [ ] **Step 6.5: Update `ask()` to dispatch `free:` model choices**

In the `ask()` function, add the `free:` handler after the `account:` check:

```python
def ask(
    project_root: Path,
    task: str,
    model_choice: str = "auto",
    *,
    allow_cloud: bool = False,
    allow_edits: bool = False,
    account_runner: Any = None,
    mode: str | None = None,
    on_event: Any = None,
    on_text: Any = None,
    cancel: Any = None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    if model_choice and model_choice.startswith("account:"):
        spec = model_choice.split(":", 1)[1]
        account_id, _, model = spec.partition(":")
        return _ask_account(
            root, task, account_id, model=model or None,
            allow_edits=allow_edits, runner=account_runner, mode=mode,
            on_event=on_event, on_text=on_text, cancel=cancel,
        )

    if model_choice and model_choice.startswith("free:"):    # ← NEW
        return _ask_free_model(
            root, task, model_choice,
            allow_edits=allow_edits, on_event=on_event,
            on_text=on_text, cancel=cancel,
        )

    from opaihub.ask import run_ask
    from opaihub.local_runner import runner_for_model

    runner = None
    if model_choice and model_choice != "auto":
        runner = runner_for_model(model_choice, project_root)
    return run_ask(root, task, runner=runner, record=True, allow_cloud=allow_cloud)
```

- [ ] **Step 6.6: Run tests to verify**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/test_provider_connections.py -v
```

Expected: All tests PASS.

- [ ] **Step 6.7: Run full Python suite**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: All pass.

- [ ] **Step 6.8: Commit**

```bash
git add opai/app_state.py tests/test_provider_connections.py
git commit -m "feat(models): wire free models into available_models() and ask()

- available_models() inserts free models (group='free') between accounts and auto
- auto model gains group='routing', local models gain group='local'
- ask() dispatches 'free:' model prefix to _ask_free_model()
- _ask_free_model() validates key, builds FreeAPIRunner, calls run_ask

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 7: Update frontend model picker to `<optgroup>` (E2E TDD)

**Files:**
- Modify: `opai/assets/web/app.js`
- Modify: `opai/assets/web/__tests__/e2e/helpers/fixtures.js`
- Modify: `opai/assets/web/__tests__/e2e/model-mode.spec.js`
- Create: `opai/assets/web/__tests__/e2e/free-models.spec.js`

- [ ] **Step 7.1: Update fixtures to include group fields and free models**

In `opai/assets/web/__tests__/e2e/helpers/fixtures.js`, replace the `MODELS` export with:

```js
export const MODELS = [
  {
    id: "account:claude:sonnet",
    label: "Claude · Sonnet 4.6",
    advanced_label: "Claude Sonnet 4.6 via Anthropic account connector",
    kind: "account",
    provider: "claude",
    group: "claude",
    paid: true,
    badge: "balanced · high · $$",
    available: true,
  },
  {
    id: "account:claude:opus",
    label: "Claude · Opus 4.8",
    advanced_label: "Claude Opus 4.8 via Anthropic account connector",
    kind: "account",
    provider: "claude",
    group: "claude",
    paid: true,
    badge: "slower · highest · $$$",
    available: true,
  },
  {
    id: "account:claude:haiku",
    label: "Claude · Haiku 4.5",
    advanced_label: "Claude Haiku 4.5 via Anthropic account connector",
    kind: "account",
    provider: "claude",
    group: "claude",
    paid: true,
    badge: "fast · good · $",
    available: true,
  },
  {
    id: "account:codex:gpt-5.5",
    label: "Codex · GPT-5.5",
    advanced_label: "Codex gpt-5.5 via OpenAI account connector",
    kind: "account",
    provider: "codex",
    group: "codex",
    paid: true,
    badge: "fast · high · $$",
    available: true,
  },
  {
    id: "account:copilot:gpt-5.2",
    label: "Copilot · GPT-5.2",
    advanced_label: "GitHub Copilot gpt-5.2 via account connector",
    kind: "account",
    provider: "copilot",
    group: "copilot",
    paid: true,
    badge: "fast · high · $$",
    available: true,
  },
  {
    id: "free:deepseek:deepseek-chat",
    label: "DeepSeek · V3 Chat (free)",
    advanced_label: "DeepSeek V3 Chat via DeepSeek API (free tier)",
    kind: "free",
    provider: "deepseek",
    group: "free",
    paid: false,
    available: true,
    badge: "free tier",
  },
  {
    id: "free:groq:llama-3.3-70b-versatile",
    label: "Groq · Llama 3.3 (free)",
    advanced_label: "Meta Llama 3.3 70B via Groq API (free tier, very fast inference)",
    kind: "free",
    provider: "groq",
    group: "free",
    paid: false,
    available: false,
    disabled_reason: "Set GROQ_API_KEY env var. Get a free key at console.groq.com",
  },
  {
    id: "ollama:qwen2.5-coder",
    label: "OPai · Local mode",
    advanced_label: "qwen2.5-coder:7b via ollama on this device",
    kind: "local",
    provider: "local",
    group: "local",
    paid: false,
    badge: "private · free",
    available: true,
  },
  {
    id: "auto",
    label: "OPai · Auto mode",
    advanced_label: "Automatic local-first routing",
    kind: "auto",
    provider: "auto",
    group: "routing",
    paid: false,
    badge: "routes cheapest · $0 when local",
    available: true,
  },
];
```

Also update `fullScenario()` to use the new MODELS and add the free model to default boot:

In `fullScenario()`, the `models` key in `base.boot` already uses `MODELS` — no change needed there.

Update `defaultBoot` in `mock-bridge.js` is NOT a fixtures.js concern — leave that for Step 7.3.

- [ ] **Step 7.2: Create `free-models.spec.js`**

Create `opai/assets/web/__tests__/e2e/free-models.spec.js`:

```js
import { test, expect } from "@playwright/test";
import { MODELS } from "./helpers/fixtures.js";
import { openApp } from "./helpers/app.js";

test("free models appear under Free models optgroup", async ({ page }) => {
  await openApp(page, {
    boot: { models: MODELS, selectedModel: "auto" },
  });
  const optgroup = page.locator('#modelSel optgroup[label="Free models"]');
  await expect(optgroup).toBeVisible();
  const freeOptions = page.locator('#modelSel optgroup[label="Free models"] option');
  await expect(freeOptions).toHaveCount(2); // DeepSeek and Groq in MODELS
});

test("free model without API key is disabled with setup hint", async ({ page }) => {
  await openApp(page, {
    boot: { models: MODELS, selectedModel: "auto" },
  });
  const disabledOption = page.locator(
    '#modelSel option[value="free:groq:llama-3.3-70b-versatile"]'
  );
  await expect(disabledOption).toBeDisabled();
  const title = await disabledOption.getAttribute("title");
  expect(title).toContain("GROQ_API_KEY");
});

test("free model with API key is enabled and selectable", async ({ page }) => {
  await openApp(page, {
    boot: { models: MODELS, selectedModel: "auto" },
  });
  const enabledOption = page.locator(
    '#modelSel option[value="free:deepseek:deepseek-chat"]'
  );
  await expect(enabledOption).not.toBeDisabled();
});

test("Claude optgroup contains only Claude models", async ({ page }) => {
  await openApp(page, {
    boot: { models: MODELS, selectedModel: "auto" },
  });
  const claudeOptions = page.locator('#modelSel optgroup[label="Claude"] option');
  const count = await claudeOptions.count();
  expect(count).toBe(3); // sonnet, opus, haiku
  for (let i = 0; i < count; i++) {
    const text = await claudeOptions.nth(i).textContent();
    expect(text).toMatch(/^Claude ·/);
  }
});

test("Codex optgroup contains only Codex models", async ({ page }) => {
  await openApp(page, {
    boot: { models: MODELS, selectedModel: "auto" },
  });
  const codexOptions = page.locator('#modelSel optgroup[label="Codex"] option');
  const count = await codexOptions.count();
  expect(count).toBeGreaterThanOrEqual(1);
  for (let i = 0; i < count; i++) {
    const text = await codexOptions.nth(i).textContent();
    expect(text).toMatch(/^Codex ·/);
  }
});

test("OPai routing optgroup contains Auto mode", async ({ page }) => {
  await openApp(page, {
    boot: { models: MODELS, selectedModel: "auto" },
  });
  const autoOption = page.locator('#modelSel optgroup[label="OPai routing"] option[value="auto"]');
  await expect(autoOption).toBeVisible();
  const text = await autoOption.textContent();
  expect(text).toContain("Auto");
});

test("selecting enabled free model updates provider signal", async ({ page }) => {
  await openApp(page, {
    boot: { models: MODELS, selectedModel: "auto" },
  });
  await page.selectOption("#modelSel", "free:deepseek:deepseek-chat");
  // providerDot should update (color change from auto/grey)
  const color = await page.locator("#providerDot").evaluate(
    (el) => getComputedStyle(el).backgroundColor
  );
  expect(color).not.toBe("rgba(0, 0, 0, 0)");
});
```

- [ ] **Step 7.3: Update `model-mode.spec.js` to match new model labels and groups**

In `opai/assets/web/__tests__/e2e/model-mode.spec.js`, **replace** the first test:

```js
test("model selector shows optgroup headings for Claude, Codex, Copilot, Free models", async ({ page }) => {
  await openApp(page, {
    boot: { models: MODELS, selectedModel: "auto" },
  });
  const groupLabels = await page.locator("#modelSel optgroup").evaluateAll(
    (groups) => groups.map((g) => g.label)
  );
  expect(groupLabels).toContain("Claude");
  expect(groupLabels).toContain("Codex");
  expect(groupLabels).toContain("Copilot");
  expect(groupLabels).toContain("Free models");
  expect(groupLabels).toContain("OPai routing");
});
```

Also add the MODELS import at the top if not already present:
```js
import { MODELS } from "./helpers/fixtures.js";
```

And update the `selectOption` calls if they reference old model IDs — check that `"account:codex:gpt-5.5"` still exists in MODELS (it does, as `"account:codex:gpt-5.5"`).

- [ ] **Step 7.4: Run E2E tests to confirm they fail before JS change**

```bash
cd C:\Users\Frist\.opai\source
npx playwright test opai/assets/web/__tests__/e2e/free-models.spec.js --reporter=list 2>&1 | tail -30
```

Expected: Tests fail — `optgroup` elements not found because the picker still uses flat `<option>`.

- [ ] **Step 7.5: Update `renderComposerSelects()` in `opai/assets/web/app.js`**

Find the section starting `const modelSel = $("#modelSel"); modelSel.innerHTML = "";` through `setProviderDot();` and replace it with:

```js
const modelSel = $("#modelSel"); modelSel.innerHTML = "";
const PICKER_GROUPS = [
  { key: "claude", label: "Claude" },
  { key: "codex", label: "Codex" },
  { key: "copilot", label: "Copilot" },
  { key: "free", label: "Free models" },
  { key: "routing", label: "OPai routing" },
  { key: "local", label: "Local models" },
];
const allModels = state.boot.models || [];
function _makeOption(m) {
  const o = document.createElement("option"); o.value = m.id; o.textContent = m.label;
  o.title = m.advanced_label || m.badge || "";
  if (m.available === false) { o.disabled = true; o.title = m.disabled_reason || "Not available"; }
  if (m.id === state.model.id) o.selected = true;
  return o;
}
PICKER_GROUPS.forEach(({ key, label }) => {
  const groupModels = allModels.filter((m) => m.group === key);
  if (!groupModels.length) return;
  const grp = document.createElement("optgroup"); grp.label = label;
  groupModels.forEach((m) => grp.appendChild(_makeOption(m)));
  modelSel.appendChild(grp);
});
// Ungrouped fallback: models without group field (backward-compat with legacy fixtures)
allModels.filter((m) => !m.group).forEach((m) => modelSel.appendChild(_makeOption(m)));
modelSel.onchange = () => {
  const m = state.boot.models.find((x) => x.id === modelSel.value);
  if (m) state.model = { id: m.id, label: m.label, advancedLabel: m.advanced_label, kind: m.kind, provider: m.provider };
  setProviderDot(); bridge.savePref("default_model", state.model.id); refreshInspector(); refreshStatus();
};
setProviderDot();
```

- [ ] **Step 7.6: Run E2E tests to verify they pass**

```bash
cd C:\Users\Frist\.opai\source
npx playwright test opai/assets/web/__tests__/e2e/free-models.spec.js --reporter=list
```

Expected: All tests PASS.

- [ ] **Step 7.7: Run the full E2E suite**

```bash
cd C:\Users\Frist\.opai\source
npx playwright test --reporter=list 2>&1 | tail -40
```

Expected: All tests pass. If any fail due to the flat-option assumption being broken, fix as follows:
- Tests checking for `option` text containing "Claude/Codex/Copilot/local" — update to check for `optgroup[label="Claude"]` or use fixture MODELS that already have the expected values.

- [ ] **Step 7.8: Run unit (Vitest) tests**

```bash
cd C:\Users\Frist\.opai\source
npx vitest run 2>&1 | tail -20
```

Expected: All pass.

- [ ] **Step 7.9: Commit**

```bash
git add opai/assets/web/app.js \
        opai/assets/web/__tests__/e2e/helpers/fixtures.js \
        opai/assets/web/__tests__/e2e/model-mode.spec.js \
        opai/assets/web/__tests__/e2e/free-models.spec.js
git commit -m "feat(ui): grouped model picker with optgroup + free model entries

Model picker now renders <optgroup> sections: Claude | Codex | Copilot |
Free models | OPai routing | Local models. Labels use provider-prefixed
format ('Claude · Sonnet 4.6' not 'OPai · Balanced mode'). Free models
show grayed with setup hint when no API key is configured.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 8: Full suite verification + PR

- [ ] **Step 8.1: Run full Python test suite**

```bash
cd C:\Users\Frist\.opai\source
python -m pytest tests/ -v --tb=short 2>&1 | tail -50
```

Expected: All pass. Fix any remaining regressions before continuing.

- [ ] **Step 8.2: Run full E2E suite one more time**

```bash
cd C:\Users\Frist\.opai\source
npx playwright test --reporter=list
```

Expected: All pass.

- [ ] **Step 8.3: Run npm audit**

```bash
cd C:\Users\Frist\.opai\source
npm audit --audit-level=high
```

Expected: `found 0 vulnerabilities` (or only low/moderate if pre-existing).

- [ ] **Step 8.4: Push branch and open PR**

```bash
cd C:\Users\Frist\.opai\source
git push origin feat/free-models-model-picker-groups
gh pr create \
  --title "feat: free API models + grouped model picker (DeepSeek, Gemini, Groq, Mistral)" \
  --body "## Summary

Fixes the model picker showing \`OPai · Balanced mode\` repeated for every connected model. Adds support for 5 free API models.

### Changes

- **Model picker labels**: Provider-prefixed format (\`Claude · Sonnet 4.6\` instead of \`OPai · Balanced mode\`)
- **Grouped picker**: \`<optgroup>\` sections — Claude | Codex | Copilot | Free models | OPai routing | Local models
- **Free models**: DeepSeek V3 Chat, DeepSeek R1 Reasoner, Gemini 2.0 Flash, Groq Llama 3.3, Mistral Small — always visible, grayed when no API key
- **Execution**: \`FreeAPIRunner\` handles free API calls with Bearer auth; \`ask()\` dispatches \`free:\` model prefix
- **Tests**: Python unit (13 new + 5 updated), Playwright E2E (7 new + 3 updated)

### Before / After

| Before | After |
|--------|-------|
| \`OPai · Balanced mode\` ×8 | \`Claude · Sonnet 4.6\`, \`Codex · GPT-5.5\`, … |
| Flat \`<select>\` list | Grouped \`<optgroup>\` sections |
| No free models | 5 free-tier models always visible |

### Setup for free models (user action required)

Set any of these env vars to enable the corresponding models:
- \`DEEPSEEK_API_KEY\` — platform.deepseek.com (free tier)
- \`GOOGLE_API_KEY\` — aistudio.google.com (free tier)
- \`GROQ_API_KEY\` — console.groq.com (free tier)
- \`MISTRAL_API_KEY\` — console.mistral.ai (free/low-cost)

### Testing

- Python: \`python -m pytest tests/ -v\`
- E2E: \`npx playwright test\`
- Unit: \`npx vitest run\`" \
  --base main
```

---

## Spec Coverage Checklist

| Spec Requirement | Task |
|-----------------|------|
| Fix labels: "Claude · Sonnet 4.6" format | Task 4 |
| Grouped picker: Claude \| Codex \| Copilot \| Free \| Routing \| Local | Task 7 |
| Add DeepSeek V3 + R1 | Task 2 |
| Add Gemini 2.0 Flash | Task 2 |
| Add Groq Llama 3.3 | Task 2 |
| Add Mistral Small | Task 2 |
| Free models grayed when no key | Tasks 2, 7 |
| FreeAPIRunner with Bearer auth | Task 3 |
| ask() dispatches free: prefix | Task 6 |
| available_models() includes free + groups | Task 6 |
| group field on all model options | Tasks 5, 6 |
| Python unit tests for free_models | Task 2 |
| Python tests for FreeAPIRunner | Task 3 |
| Updated provider_contract tests | Task 4 |
| Updated provider_connections tests | Task 5 |
| Playwright E2E for groups | Task 7 |
| Playwright E2E for free models | Task 7 |
| Full suite must pass | Task 8 |
