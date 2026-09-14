# Testing Vesta

Vesta is a **Python CLI + PySide6/QWebEngine desktop GUI**. The test strategy is
unit + integration + headless-GUI + CLI end-to-end in Python, plus a JS layer
(Vitest + Playwright) for the web-rendered front-end. **No test ever launches a
real paid CLI, makes a network call, or spends money** — the model runtime is
mocked (`FakeAccountRunner`, `FakeStreamingRunner`, mocked `subprocess.Popen`,
and a mock QWebChannel bridge for the browser).

## AI activity / cancellation / streaming (three layers)

The Stop-button + live-activity + streaming work is tested at every layer:

| Layer | Files | Covers |
| --- | --- | --- |
| Python unit/integration | `tests/test_activity.py`, `tests/test_streaming.py`, `tests/test_cancellation.py`, `tests/test_copilot.py` | claude stream parser, stale-guard, slow-model thresholds, error mapper; pipeline events + streamed text (account + local); `Popen` kill on stop/timeout; Copilot connector |
| JS unit (Vitest, Node 22) | `opai/assets/web/__tests__/activity.test.js` | `shouldApply` stale-guard, thresholds, elapsed formatting, activity store |
| E2E (Playwright, Chromium) | `opai/assets/web/__tests__/e2e/activity.spec.js` (+ `mock-bridge.js`) | generation visibility, slow-model, stop-before-token (no stale overwrite), stop-during-stream, double-stop, duplicate-submit, retry-after-stop, error recovery, a11y |

Run the JS layers locally:

```sh
npm ci                         # reproducible install from package-lock.json
npm audit --audit-level=high   # security gate for test-only dependencies
npm run test:unit              # Vitest
npx playwright install chromium  # once
npm run test:e2e               # Playwright (serves the repo, injects a mock bridge)
```

These packages are development-only and are not included in the Vesta Python
runtime. GitHub CI runs the audit, Vitest suite, and Chromium E2E suite in a
separate least-privilege Node 22 job. Browser screenshots, traces, and the HTML
report are retained for seven days when that job fails.

The Playwright harness (`mock-bridge.js`) stubs `QWebChannel`/`qt` and a
scriptable bridge so the **real** `activity.js` + `app.js` run in Chromium with
no Qt; the spec drives streaming/cancellation via `window.__mock.*`.

## Comprehensive desktop-web E2E suite

The E2E suite protects the complete visible Vesta control-plane surface: shell,
chat, activity, cancellation, provider/auth states, settings, models/modes,
savings receipts, cost firewall, context waste, benchmarks, prompt library,
agents/workflows, proof bundles, inspector, brand rules, recovery, responsive
layouts, accessibility basics, CLI parity, loading states, and known
regressions.

Shared fixtures and helpers live under
`opai/assets/web/__tests__/e2e/helpers/`. `fullScenario()` creates one complete
deterministic app state, while individual tests override only the state they
need. The mock records native bridge calls so tests can prove that a UI action
delegated safely without executing a real provider, shell mutation, or paid
operation.

```sh
# List every browser contract
npx playwright test --list

# Run all browser E2E tests
npm run test:e2e

# Run one product area while developing
npx playwright test opai/assets/web/__tests__/e2e/money-saved.spec.js

# Open failure artifacts locally
npx playwright show-report
```

The current suite has **148 Playwright tests**: 25 pre-existing tests and 123
added by the comprehensive QA pass. Expected-failure contracts reproduce ten
cross-platform defects plus one clean-Linux dependency defect. These are not
hidden or skipped: Playwright executes each assertion, and unrelated failures
remain fatal. See `QA_TEST_REPORT.md` for the bug IDs, reproductions, severity,
and coverage map.

When fixing a documented defect:

1. Find its `BUG-QA-*` expected-failure annotation.
2. Fix the application in a separate product PR.
3. Remove `test.fail()` without weakening the assertion.
4. Run that spec, then `npm run test:e2e` and the Python suite.

The harness ignores one expected static-browser console message for the Qt
`qrc:` script, which Chromium cannot load outside QWebEngine. Every other
console error and every uncaught page error fails the relevant test.

## Python suite

## Run the tests

```sh
# Full suite (what CI runs)
python -m unittest discover -s tests

# One module
python -m unittest discover -s tests -p "test_savings_honesty.py"

# Lint + format (must be clean for CI)
python -m ruff check .
python -m ruff format --check .

# Security static analysis
python -m bandit -r opai opaihub opcoding -q
```

> **CI parity.** CI pins `ruff==0.15.9` (see `.github/workflows/ci.yml`). Run the
> same version locally, or the format check can disagree. When you bump the pin,
> reformat the tree in the **same** PR.

> **Clean-HOME runs.** Some suites isolate `HOME`/`USERPROFILE` (see
> `isolated_home()` in `tests/_helpers.py`) so the developer's real machine state
> (connected accounts, global config) can't leak into a result. That mirrors CI,
> which has no `claude`/`codex`/Ollama installed.

## How AI providers are mocked

Real model CLIs (`claude`, `codex`) and local models are **never** invoked in
tests. Everything goes through injectable fakes in `tests/_helpers.py`:

| Fake | Stands in for | Knobs |
| --- | --- | --- |
| `FakeAccountRunner` | `opaihub.accounts.AccountRunner` (paid cloud) | `account_id`, `text`, `cost`, `timed_out`, `raises`; records every call in `.calls` |
| `FakeLocalRunner` | `opaihub.local_runner.LocalRunner` | `name`, `model`, `answer`, `available`, `raises` |
| `fake_subprocess` | `accounts._hidden_run` / `subprocess.run` | `stdout`, `returncode`, `timeout` |
| `make_repo` | a temp git repo with markers | `files`, `commit` |
| `isolated_home` | a throwaway `HOME` | context manager |

These cover the AI edge cases the product must survive: **success, empty
response, unknown/`None` cost, timeout, raised exception, and (for the proxy)
fail-open when the runner is unavailable.** They are deterministic.

Example — drive the whole GUI pipeline without a real model:

```python
from _helpers import FakeAccountRunner, make_repo
from opaihub.gui_pipeline import handle_gui_message

fake = FakeAccountRunner(text="done", cost=0.042)
res = handle_gui_message(root, "summarize", model_id="account:claude:sonnet",
                         mode="ask", account_runner=fake)
assert res["receipt"]["estimated_actual_usd"] == 0.042  # honest cost
```

## Where each concern is tested

| Concern | Test file(s) |
| --- | --- |
| GUI message flow / status contract | `test_message_contract.py`, `test_desktop_gui.py` |
| AI-output rendering safety (the "GUI XSS" surface) | `test_message_render.py` |
| Honest cost / savings accounting | `test_savings_honesty.py`, `test_cost_ledger.py` |
| Runner robustness (timeout/empty/non-JSON) | `test_runner_robustness.py` |
| Inline-capture proxy (gate / record / fail-open) | `test_proxy.py` |
| Signed savings receipt + verify/tamper | `test_receipt.py` |
| Destructive-command policy + shell-bypass hardening | `test_sandbox_policy.py` |
| Route output redaction + size budget | `test_evidence_router.py` |
| Policy / budget firewall / governance | `test_policy_routing.py`, `test_budget_firewall.py`, `test_governance.py` |

## Adversarial / abuse coverage

Because this is an AI GUI, the renderer and ledger are treated as attack
surfaces (see `RenderSecurityTests` in `test_message_render.py` and the
redaction/privacy invariants in `test_savings_honesty.py` / `test_proxy.py`):

- AI output containing `<script>`, `<img onerror=…>`, `javascript:`/`data:`
  links, or href-breakout attempts must render as **inert escaped text** — no
  active anchor, no event handler, no `<img>` auto-load.
- A prompt containing a secret (`sk-…`, `token=…`) must **never** land in the
  ledger, a receipt, or the GUI result.

## Adding a new model provider

The provider seam is `opaihub.accounts` (paid) and `opaihub.local_runner`
(local). To add one (e.g. Gemini, Mistral, a custom endpoint):

1. Add an adapter branch in `AccountRunner.build_command` / `complete`, or a new
   runner class mirroring the `available()` + `complete()` shape.
2. Extend `opaihub.proxy.SUPPORTED_AGENTS` so the inline-capture shim routes it.
3. Add a `FakeAccountRunner(account_id="…")` case to your tests — no real call.
4. Keep `complete()` returning `{"text", "cost", "timed_out"?}` so honest cost
   accounting (`record_model_call(real_cost_usd=…)`) keeps working.

## Future test space (documented, not stubbed)

To avoid shallow placeholder tests that only inflate the count, future-feature
coverage is tracked as **GitHub issues**, not skipped specs:

- Inline-capture auto-record surfacing — #92
- Bypass-resistance suite (no session escapes capture) — #94
- Portable / public-key receipt verification — #88
- License + paid distribution (Epic C) — #95, #96, #97

Add the real tests alongside each feature when it lands.
