# Vesta - Push feature branch and create PR
# Optional publishing helper for this feature branch.
#
# 1. Push the feature branch
git push --set-upstream origin HEAD

# 2. Create the PR via gh CLI
gh pr create `
  --repo MarcoLadeira/OPai `
  --title "feat: free API models and grouped model picker" `
  --base main `
  --head feat/free-models-model-picker-groups `
  --body @"
## Summary

Adds verified free-tier AI models to the Vesta model picker and fixes the grouped picker UI.

### Problem
The model picker showed repeated ``Vesta · Balanced/Fast/Powerful mode`` labels for all models regardless of provider, making it impossible to distinguish Claude vs Codex vs Copilot models.

### Changes

#### 🆓 Free API model registry (``opaihub/free_models.py``)
Free-tier-eligible models now appear in the picker — grayed when no API key is set, enabled when the key is found in the environment. Vesta still confirms before sending data because provider quotas or billing may apply.

| Label | Env Var | Endpoint |
|---|---|---|
| Gemini · 3.1 Flash-Lite (free tier) | GOOGLE_API_KEY | generativelanguage.googleapis.com/v1beta/openai |
| Groq · GPT-OSS 120B (free tier) | GROQ_API_KEY | api.groq.com/openai/v1 |
| Mistral · Small (free tier) | MISTRAL_API_KEY | api.mistral.ai/v1 |

#### 🔌 FreeAPIRunner (``opaihub/local_runner.py``)
OpenAI-compatible HTTP runner for free-tier API endpoints. Keys are read from the environment. Calls always require explicit confirmation because public endpoints receive project context and account billing may apply.

#### 🏷️ Descriptive model labels (``opai/provider_contract.py``)
``provider_display_name()`` now returns "Claude · Sonnet 4.6" / "Codex · GPT-5.5" / "Copilot · Claude Sonnet" instead of the generic "Vesta · Balanced mode" for all models.

#### 📂 Grouped model picker (``opai/assets/web/app.js``)
``<select id="modelSel">`` now uses ``<optgroup>`` sections in this order:
Claude | Codex | Copilot | **Free models** | Vesta routing | Local models

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
