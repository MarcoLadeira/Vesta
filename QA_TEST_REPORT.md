# OPai E2E QA Report

## 1. Summary

This testing-only change expands OPai's browser E2E suite from 25 to 148 tests.
It exercises the real desktop web assets in Chromium through a deterministic
mock QWebChannel bridge. No test contacts Claude, Codex, Copilot, Ollama, or any
paid/network provider.

- Playwright: 148 passed locally, including 11 expected failures. Clean-Linux
  CI adds one environment-conditional expected failure. Together they preserve
  11 verified product bugs.
- Vitest: 10 passed.
- Python: 784 passed, 1 skipped.
- Quality gates: Ruff, formatting, Bandit, registry validation, npm audit, and
  the OPai benchmark gate passed.
- Product logic changed: no.

## 2. OPai Testing Baseline Report

| Area | Finding | Risk |
| --- | --- | --- |
| Git state | Work started from clean `origin/main` in an isolated `codex/comprehensive-e2e-qa` worktree. | Low |
| Test framework | Python `unittest`, Vitest, and Playwright/Chromium are established. | Low |
| Existing E2E coverage | 25 tests covered core activity, branding, capture, and folder selection. | High gaps before this PR |
| Mock provider availability | A QWebChannel mock existed; this PR extends it with deterministic scenarios and state tracking. | Low |
| App start command | Browser assets are served locally by Playwright; native app is `python -m opai gui`. | Low |
| CLI testability | CLI can run in isolated temporary HOME/project directories without provider calls. | Low |
| CI readiness | Node 22, Chromium, npm audit, Vitest, and Playwright already run in a separate CI job. | Low |
| Test blockers | Real provider, billing, OS-native Qt dialog, and destructive-operation tests are intentionally excluded. | Controlled |

## 3. OPai Feature Test Map

| Feature | Current surface | Critical behaviours | Priority |
| --- | --- | --- | --- |
| App shell / navigation | Sidebar, workspace, status footer | Load, active view, no fatal errors, stable project identity | P0 |
| Chat / new chat / composer | Main chat | Send, follow-up, no duplication, hostile/long text, reset | P0 |
| Activity / stop / retry | Chat timeline and composer | Truthful states, pre-token and streaming cancel, stale-reply guard | P0 |
| Provider auth | Chat errors, Connections, Settings | Missing/invalid auth, timeout, retry, secret-safe details | P0 |
| Models and modes | Composer and Settings | Availability, defaults, Safe Auto posture, Full Auto gate | P0 |
| Money Saved / receipts | Money Saved and chat receipt | Spend/savings split, paid-call truth, unknown/zero states | P1 |
| Cost Firewall | Cost Firewall and warning flow | Budget warning, cheaper route, block, panic state | P1 |
| Context Waste | Context Waste | Waste ranking, honest estimates, profile and empty states | P1 |
| Benchmark | Benchmark | Exact local claim, caveat, gate/failure/empty states | P1 |
| Proof Bundle | Proof Bundle | Signature, cost/change evidence, privacy, export command | P1 |
| Agents / workflows / tools | Agents, Workflows, tool dialogs | Safe delegation, permission/risk context, confirmation | P1 |
| Inspector | Right-side inspector | Live activity, request/provider/cost detail, open/close | P1 |
| Prompt Library | Prompt Library | Search, category filter, composer insertion, empty state | P2 |
| Settings / connections | Settings and Connections | Accounts, privacy, defaults, persistence, sparse data | P1 |
| Branding and copy | All user surfaces | OPai identity, human errors, no raw route IDs or broken sentinels | P1 |
| Responsiveness | Desktop web surface | Desktop/tablet/mobile usability and overflow | P1 |
| Accessibility | Core controls and overlays | Names, keyboard operation, focus, status semantics | P1 |
| CLI parity | Local CLI | Help, GUI contract, Safe Auto, wording and secret isolation | P1 |

## 4. Feature Coverage

| Feature | Coverage | Test file |
| --- | --- | --- |
| Shell, chat, recents, workspace | Full deterministic E2E | `app-shell.spec.js`, `chat-core.spec.js` |
| Activity, streaming, cancellation | Full deterministic E2E | `activity-truth.spec.js`, `stop-cancel.spec.js` |
| Auth and recovery states | Success/error matrix | `provider-auth.spec.js`, `errors-recovery.spec.js` |
| Savings, firewall, context, benchmark | Product-value contracts | `money-saved.spec.js`, `cost-firewall.spec.js`, `context-waste.spec.js`, `benchmark.spec.js` |
| Prompts, agents, workflows, proof | Major secondary surfaces | `prompt-library.spec.js`, `agents-workflows.spec.js`, `proof-bundle.spec.js` |
| Inspector, branding, models, modes | User and advanced-state contracts | `inspector.spec.js`, `opai-branding.spec.js`, `model-mode.spec.js` |
| Settings and confirmation | State persistence and mutation gates | `settings-connections.spec.js`, `tool-confirmation.spec.js` |
| Loading, viewport, accessibility | Cross-cutting quality | `loading-states.spec.js`, `responsiveness.spec.js`, `accessibility.spec.js` |
| CLI and known regressions | Cross-surface contracts | `cli-parity.spec.js`, `regression.spec.js` |

## 5. Bugs Found

Expected-failure tests are executable defect specifications. They must be
converted to ordinary passing assertions when the product bug is fixed.

| Severity | ID and bug | Reproduction | Expected | Actual | Test |
| --- | --- | --- | --- | --- | --- |
| High | BUG-QA-002: technical error details expose secret-like text | Return a provider failure containing a credential-shaped value and expand details. | UI defense redacts it even if upstream misses it. | Value renders verbatim. | `provider-auth.spec.js` |
| High | BUG-QA-009: Full Auto persists without explicit confirmation | Select Full Auto in the web mode selector. | Plain risk acknowledgement before persistence. | Preference is saved immediately. | `model-mode.spec.js` |
| Medium | BUG-QA-001: final failure discards activity evidence | Emit activity events, then finish with failure. | Failed timeline remains visible for diagnosis. | Error card replaces the timeline. | `activity-truth.spec.js` |
| Medium | BUG-QA-003: malformed answer renders object coercion | Return an object instead of answer text. | Safe malformed-response error. | Chat shows `[object Object]`. | `errors-recovery.spec.js` |
| Medium | BUG-QA-004: mobile layouts overflow | Open at 390x844 or 360x640. | Usable nav/composer with no horizontal overflow. | Fixed desktop grid exceeds viewport. | `responsiveness.spec.js` (2 cases) |
| Medium | BUG-QA-005: command palette has no dialog semantics | Open the command palette with keyboard. | Named `dialog` available to assistive tech. | Generic overlay only. | `accessibility.spec.js` |
| Medium | BUG-QA-006: model/mode selectors have no accessible names | Inspect composer controls by role. | Both selects have stable accessible names. | Unnamed comboboxes. | `accessibility.spec.js` |
| Medium | BUG-QA-008: unavailable models remain selectable | Supply an unavailable model with a disabled reason. | Disabled option explains why. | Option is enabled; reason ignored. | `model-mode.spec.js` |
| Medium | BUG-QA-011: clean Python crashes while loading GUI workflow data | Run `opai gui --once` without PyYAML installed. | Dependency-free headless state or an actionable dependency error. | YAML registry text is passed to `json.loads` and raises `JSONDecodeError`. | `cli-parity.spec.js` |
| Low | BUG-QA-007: workspace tooltip loses brand tagline | Load app and inspect workspace tooltip. | OPai tagline remains. | Workspace render overwrites it. | `opai-branding.spec.js` |
| Low | BUG-QA-010: form controls use an inconsistent fallback font | Compare composer/select/button computed fonts. | Bundled soft UI font is shared. | Controls fall back to Arial. | `opai-branding.spec.js` |

## 6. Untested Areas

| Area | Reason | Recommended next test |
| --- | --- | --- |
| Real provider auth/billing | Paid calls and real credentials are forbidden in automated QA. | Provider-owned sandbox/contract test with capped non-production account. |
| Native Qt OS dialogs | Browser harness replaces QWebChannel; CI has no interactive desktop. | Offscreen PySide smoke plus a Windows VM UI job. |
| Real deploy/push/destructive operations | Safety policy forbids executing them. | Assert command construction and confirmation using a fake process boundary. |
| Provider-backed external benchmarks | Local suite only; external runs cost money and are non-deterministic. | Explicit opt-in promptfoo/SWE-bench validation workflow. |

## 7. No-Logic-Change Confirmation

Changed runtime source files: **none**.

The changed files are Playwright specs, E2E helpers/fixtures, the existing
test-only QWebChannel bridge, this report, and `docs/TESTING.md`. No selectors
were added to production because roles, labels, stable IDs, and product copy
were sufficient.

```text
Production logic changed: NO
Business logic changed: NO
Provider logic changed: NO
Cost logic changed: NO
Message logic changed: NO
```
