# Design: Free Models + Model Picker Groups

**Date:** 2026-07-02
**Branch:** `feat/free-models-model-picker-groups`
**Status:** Approved

---

## Problem

The OPai model picker renders every model as a generic "OPai · Balanced/Fast/Powerful mode" label because `provider_display_name()` in `opai/provider_contract.py` maps all models to just three speed-tier strings. When Claude (3 models) + Codex (4 models) + Copilot (3 models) are all connected, the picker shows 10 nearly identical entries with no way to distinguish them — a usability failure visible in the screenshot provided.

Additionally, OPai has no free-tier cloud model options. Users either need a paid subscription (Claude/Codex/Copilot) or a local GPU (ollama/lmstudio). Free API models like DeepSeek and Groq fill a critical gap.

---

## Goals

1. Fix the model picker: distinct, provider-prefixed labels + `<optgroup>` grouping
2. Add free API model support: DeepSeek, Gemini, Groq, Mistral
3. Full test coverage: Python unit + JS/Vitest unit + Playwright E2E

---

## Architecture

### Layer 1 — Python data layer

**`opai/provider_contract.py` — Updated `provider_display_name()`**

Old: maps all models to `"OPai · Balanced/Fast/Powerful mode"`
New: returns provider-prefixed labels.

```python
# Old output (broken)
provider_display_name("claude", "sonnet") → "OPai · Balanced mode"
provider_display_name("codex", "gpt-5.5") → "OPai · Powerful mode"

# New output (correct)
provider_display_name("claude", "sonnet") → "Claude · Sonnet 4.6"
provider_display_name("codex", "gpt-5.5") → "Codex · GPT-5.5"
provider_display_name("local", None) → "OPai · Local mode"  # unchanged
provider_display_name("auto", None) → "OPai · Auto mode"   # unchanged
```

Add `model_group(provider: str) -> str` helper returning `"claude"`, `"codex"`, `"copilot"`, `"free"`, `"local"`, or `"routing"`.

**`opaihub/accounts.py` — Add `group` field to all model options**

All entries from `_account_options()` gain `"group": "claude"/"codex"/"copilot"`. The auto entry gets `"group": "routing"`. Local models get `"group": "local"`.

**`opaihub/free_models.py` (new)**

Defines `FREE_MODEL_SPECS` and `list_free_models()`:

```python
FREE_MODEL_SPECS = [
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
        "free_tier_note": "Generous free tier at platform.deepseek.com",
        "setup_hint": "Set DEEPSEEK_API_KEY env var (get a key at platform.deepseek.com)",
    },
    {
        "id": "free:deepseek:deepseek-reasoner",
        "label": "DeepSeek · R1 Reasoner (free)",
        ...
    },
    {
        "id": "free:gemini:gemini-2.0-flash",
        "label": "Gemini · 2.0 Flash (free)",
        "env_key": "GOOGLE_API_KEY",
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        ...
    },
    {
        "id": "free:groq:llama-3.3-70b-versatile",
        "label": "Groq · Llama 3.3 70B (free)",
        "env_key": "GROQ_API_KEY",
        "api_base": "https://api.groq.com/openai/v1",
        ...
    },
    {
        "id": "free:mistral:mistral-small-latest",
        "label": "Mistral · Small (free)",
        "env_key": "MISTRAL_API_KEY",
        "api_base": "https://api.mistral.ai/v1",
        ...
    },
]
```

`list_free_models()` iterates `FREE_MODEL_SPECS`, checks `os.environ.get(spec["env_key"])`, and returns picker entries with `available: bool` and `disabled_reason` when not configured.

**`opai/app_state.py` — `available_models()` updated**

Order: connected account models (Claude | Codex | Copilot) → free models → auto → local models.

**`app_state.ask()` — Handle `free:` prefix**

```python
if model_choice.startswith("free:"):
    return _ask_free_model(root, task, model_choice, ...)
```

### Layer 2 — Execution (`opaihub/local_runner.py`)

Add `FreeAPIRunner(OpenAICompatibleRunner)`:
- `__init__`: takes `api_key` (from env var), passes to `Authorization: Bearer` header
- `available()`: returns `bool(self.api_key)` — no network ping (key presence is enough to show as enabled)
- `complete()`: injects Authorization header via `_http_json_auth()`
- `runner_for_model("free:deepseek:deepseek-chat", ...)`: builds the right runner from the spec

`_http_json` gains an optional `headers` parameter to support the Authorization header.

`runner_for_model()` handles `free:` prefix by looking up the spec and building a `FreeAPIRunner`.

Free model calls always go through the policy gate (`requires_confirmation=True` since they're public endpoints), consistent with how OPai treats all cloud calls.

### Layer 3 — Frontend (`opai/assets/web/app.js`)

`renderComposerSelects()` replaces the flat `forEach` with group-aware rendering:

```js
const GROUPS = [
  { key: "claude", label: "Claude" },
  { key: "codex", label: "Codex" },
  { key: "copilot", label: "Copilot" },
  { key: "free", label: "Free models" },
  { key: "routing", label: "OPai routing" },
  { key: "local", label: "Local models" },
];

GROUPS.forEach(({ key, label }) => {
  const groupModels = (state.boot.models || []).filter((m) => m.group === key);
  if (!groupModels.length) return;
  const grp = document.createElement("optgroup");
  grp.label = label;
  groupModels.forEach((m) => {
    const o = document.createElement("option");
    o.value = m.id;
    o.textContent = m.label;
    o.title = m.advanced_label || m.badge || "";
    if (m.available === false) { o.disabled = true; o.title = m.disabled_reason || "Not available"; }
    if (m.id === state.model.id) o.selected = true;
    grp.appendChild(o);
  });
  modelSel.appendChild(grp);
});
```

Models without a `group` field fall into a catch-all render pass after the groups: they are appended ungrouped (backward-compatible with any legacy mock data that omits `group`). In production all models from `available_models()` will carry a `group` field, so this path is only for tests using simplified fixtures.

---

## Label Format

| Provider | Model | Label |
|----------|-------|-------|
| Claude account | sonnet | `Claude · Sonnet 4.6` |
| Claude account | opus | `Claude · Opus 4.8` |
| Claude account | haiku | `Claude · Haiku 4.5` |
| Codex account | gpt-5.5 | `Codex · GPT-5.5` |
| Codex account | gpt-5.4 | `Codex · GPT-5.4` |
| Codex account | gpt-5.4-mini | `Codex · GPT-5.4 Mini` |
| Codex account | gpt-5.3-codex-spark | `Codex · Spark` |
| Copilot account | claude-sonnet-4.6 | `Copilot · Claude Sonnet` |
| Copilot account | gpt-5.2 | `Copilot · GPT-5.2` |
| Copilot account | claude-haiku-4.5 | `Copilot · Claude Haiku` |
| DeepSeek (free) | deepseek-chat | `DeepSeek · V3 Chat (free)` |
| DeepSeek (free) | deepseek-reasoner | `DeepSeek · R1 Reasoner (free)` |
| Gemini (free) | gemini-2.0-flash | `Gemini · 2.0 Flash (free)` |
| Groq (free) | llama-3.3-70b | `Groq · Llama 3.3 (free)` |
| Mistral (free) | mistral-small | `Mistral · Small (free)` |
| Auto | — | `OPai · Auto mode` (unchanged) |
| Local | — | `OPai · Local mode` (unchanged) |

---

## Test Plan

### Python unit tests

**`tests/test_free_models.py` (new)**
- `test_spec_ids_are_unique` — no duplicate ids
- `test_list_free_models_grayed_without_key` — env var absent → `available=False`, `disabled_reason` set
- `test_list_free_models_enabled_with_key` — env var present → `available=True`
- `test_all_free_models_have_required_fields` — id, label, group, env_key, api_base
- `test_group_is_always_free` — all specs have `group=="free"`
- `test_free_runner_available_with_key` — `FreeAPIRunner(api_key="x").available()` is True
- `test_free_runner_unavailable_without_key` — `FreeAPIRunner(api_key="").available()` is False
- `test_runner_for_free_model_id` — `runner_for_model("free:deepseek:deepseek-chat")` returns `FreeAPIRunner`

**`tests/test_provider_contract.py` (updated)**
- `test_provider_details_are_advanced_only` → assert `provider_display_name("claude", "haiku") == "Claude · Haiku 4.5"`
- Add: `test_codex_label_format`, `test_free_label_format`, `test_auto_and_local_unchanged`

**`tests/test_provider_connections.py` (updated)**
- `test_account_picker_is_opai_first_with_advanced_provider_detail` → verify labels start with `"Claude ·"` not `"OPai ·"`

### JS / Vitest unit tests

New file or additions to existing test files:
- model options with `group` field render into correct `<optgroup>`
- models without `group` fall back to ungrouped (backward compat)

### Playwright E2E tests

**`model-mode.spec.js` (updated)**
- `test("model selector shows optgroup headings for Claude, Codex, Copilot, Free models")`
- `test("free model without key is disabled with setup hint")` — check `option[disabled][title*="DEEPSEEK_API_KEY"]`

**`free-models.spec.js` (new)**
- Free model entries appear under "Free models" group
- Disabled free model has `title` containing the setup hint
- Selecting a free model with key present updates provider signal
- Free model id follows `free:<provider>:<model>` format

**`helpers/fixtures.js` (updated)**
- Add group field to all MODELS entries
- Add free model entries (2 enabled, 1 disabled) to MODELS
- Update `fullScenario()` to include free models

---

## Breaking Changes

| Test | Change |
|------|--------|
| `test_provider_details_are_advanced_only` | `"OPai · Fast mode"` → `"Claude · Haiku 4.5"` |
| `test_account_picker_is_opai_first_with_advanced_provider_detail` | `startswith("OPai ·")` → `startswith("Claude ·")` |
| E2E: `"model selector exposes Auto, Claude, Codex, Copilot, and local choices"` | Add assertion for optgroup headings |

These are intentional — the old assertions enforced the broken behavior we are fixing.

---

## Files Changed

| File | Change |
|------|--------|
| `opai/provider_contract.py` | Rewrite `provider_display_name()`, add `model_group()` |
| `opaihub/accounts.py` | Add `group` to all model options |
| `opaihub/free_models.py` | **New** — `FREE_MODEL_SPECS`, `list_free_models()` |
| `opaihub/local_runner.py` | Add `FreeAPIRunner`, update `_http_json`, `runner_for_model()` |
| `opai/app_state.py` | Add free models to `available_models()`, `_ask_free_model()` |
| `opai/assets/web/app.js` | Grouped `<optgroup>` picker rendering |
| `tests/test_free_models.py` | **New** — unit tests |
| `tests/test_provider_contract.py` | Update label assertions |
| `tests/test_provider_connections.py` | Update OPai-first label assertion |
| `opai/assets/web/__tests__/e2e/model-mode.spec.js` | Add group/free model assertions |
| `opai/assets/web/__tests__/e2e/free-models.spec.js` | **New** — free model E2E |
| `opai/assets/web/__tests__/e2e/helpers/fixtures.js` | Add group fields and free model entries |
