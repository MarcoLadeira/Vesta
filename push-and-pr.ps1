# OPai - Push feature branch and create PR
# Run this in a regular PowerShell terminal (not autopilot)
#
# 1. Push the feature branch
git push origin main:refs/heads/feat/free-models-model-picker-groups

# 2. Create the PR via gh CLI
gh pr create `
  --repo MarcoLadeira/OPai `
  --title "feat: free API models and grouped model picker" `
  --base main `
  --head feat/free-models-model-picker-groups `
  --body @"
## Summary

Adds 5 free-tier AI models to the OPai model picker and fixes the grouped picker UI.

### Problem
The model picker showed repeated ``OPai · Balanced/Fast/Powerful mode`` labels for all models regardless of provider, making it impossible to distinguish Claude vs Codex vs Copilot models.

### Changes

#### 🆓 Free API model registry (``opaihub/free_models.py``)
Five free-tier models now appear in the picker — grayed when no API key is set, enabled when the key is found in the environment:

| Label | Env Var | Endpoint |
|---|---|---|
| DeepSeek · V3 Chat (free) | DEEPSEEK_API_KEY | api.deepseek.com/v1 |
| DeepSeek · R1 Reasoner (free) | DEEPSEEK_API_KEY | api.deepseek.com/v1 |
| Gemini · 2.0 Flash (free) | GOOGLE_API_KEY | generativelanguage.googleapis.com/v1beta/openai |
| Groq · Llama 3.3 (free) | GROQ_API_KEY | api.groq.com/openai/v1 |
| Mistral · Small (free) | MISTRAL_API_KEY | api.mistral.ai/v1 |

#### 🔌 FreeAPIRunner (``opaihub/local_runner.py``)
OpenAI-compatible HTTP runner for free API endpoints. Keys read from environment. Calls always require ``allow_cloud=True`` confirmation (public endpoints leave the device).

#### 🏷️ Descriptive model labels (``opai/provider_contract.py``)
``provider_display_name()`` now returns "Claude · Sonnet 4.6" / "Codex · GPT-5.5" / "Copilot · Claude Sonnet" instead of the generic "OPai · Balanced mode" for all models.

#### 📂 Grouped model picker (``opai/assets/web/app.js``)
``<select id="modelSel">`` now uses ``<optgroup>`` sections in this order:
Claude | Codex | Copilot | **Free models** | OPai routing | Local models

#### 🧪 Tests
- ``tests/test_free_models.py`` — 20 unit tests (specs, list, FreeAPIRunner, ask dispatch)
- ``tests/test_provider_connections.py`` — 6 new tests (group fields, provider labels, available_models)
- ``opai/assets/web/__tests__/e2e/free-models.spec.js`` — 7 E2E tests (optgroups, disabled state, label suffix, ordering)
- ``opai/assets/web/__tests__/e2e/model-mode.spec.js`` — updated to assert optgroup headings

### Backward Compatibility
- Models without a ``group`` field render ungrouped (fallback path in JS)
- ``auto`` and local models get ``group='routing'`` / ``group='local'``
- Free models always appear; grayed when no API key (matches existing unavailable-account pattern)
"@
