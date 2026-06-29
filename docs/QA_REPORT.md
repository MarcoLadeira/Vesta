# OPai QA Report

_Executive quality gate. Stack: Python CLI + PySide6 desktop GUI (no web frontend)._
_Generated 2026-06-29 against `main`._

## 1. Product summary

OPai is the "AI coding cost firewall": a local-first control plane that sits in
front of paid coding agents (Claude, Codex) and routes each task to the cheapest
safe path — deterministic tools and local models first, paid cloud only with
confirmation — then **proves the savings** in a signed, shareable receipt.
Core surfaces: the `opai` CLI, the PySide6 desktop chat (`opai gui`), the routing
+ evidence engine, the savings ledger, the command-policy sandbox, and the new
inline-capture proxy.

## 2. Quality scorecard

Scored 1–10 against the current `main`, evidence-based where possible.

| Area | Score | Basis |
| --- | --- | --- |
| Product clarity | 8 | Sharp, timely positioning; category bet still unproven `[ASSUMPTION]` |
| UX quality | 7 | GUI decluttered (soft typeface, Stop, persistent history); onboarding thin |
| AI reliability | 8 | Runner timeout/empty/non-JSON paths covered; honest cost recorded (#90) |
| Test coverage | 8 | **576 tests pass**; deep on money/render/policy; GUI is headless-only |
| Security | 8 | bandit clean (16,266 LOC, 0 issues); redaction + sandbox + render hardened |
| Performance | 6 | Route output capped (#30); no profiling of GUI render at scale `[UNVERIFIED]` |
| Maintainability | 8 | Clean module seams; CI now deterministic (pinned ruff) |
| Scalability | 6 | Local-first by design; no multi-user/server surface yet (intentional) |
| Release readiness | 7 | Solid for local demo / alpha; no billing rail yet (Epic C open) |

## 3. Evidence (gates run this pass)

| Gate | Result |
| --- | --- |
| `python -m unittest discover -s tests` | **576 passed, 1 skipped** |
| `python -m ruff format --check .` | 161 files already formatted |
| `python -m ruff check .` | All checks passed |
| `python -m bandit -r opai opaihub opcoding` | **No issues identified** (16,266 LOC) |
| Secret scan (committed real keys) | None found (only test/fixture patterns) |
| GitHub Actions CI on `main` | Green (3.10 + 3.13) |

## 4. Security findings

| Severity | Finding | Status |
| --- | --- | --- |
| — | AI-output renderer (`message_render.py`) — the "GUI XSS" surface | **Safe by construction, now locked.** Escape-first; only `http(s)` anchors; no `<img>`/`<script>`/event handlers ever emitted. Added 9 adversarial regression tests. |
| — | Secret leakage into ledger / receipts | **Mitigated + tested.** `redact()` + one-way task hashes; invariants in `test_savings_honesty.py`, `test_proxy.py`, `test_receipt.py`. |
| — | Destructive shell commands / bypasses | **Mitigated.** `sandbox.py` policy + bypass hardening (#11); 75 tests. |
| — | Paid call without consent | **Mitigated.** Proxy gates destructive tasks before any paid call; panic mode forces local-only. |
| Low | Receipt verification is HMAC (shared-secret), not portable across machines | **Open — tracked in #88** (needs ed25519 or key distribution). |
| Info | No license enforcement; editions self-declared via `OPAI_EDITION` | **By design in alpha — tracked in Epic C (#95–#97).** |

No High/Critical security issues found. Static analysis is clean.

## 5. Critical risks (product, not bugs)

- **High — no way to charge.** Paid editions are self-declared; there is no
  license/checkout. Tracked: Epic C (#86 → #95/#96/#97).
- **Medium — capture is opt-in until the shim ships.** The proxy engine exists
  (#91); auto-record surfacing (#92) and bypass-resistance (#94) are next.
- **Medium — GUI tested headlessly only.** Logic paths are covered via
  `run_once`/pipeline, but live PySide rendering at scale is `[UNVERIFIED]`.
- **Low — onboarding thinness.** First-run guidance exists (`quickstart`) but the
  desktop empty state could do more to convey value.

## 6. Backlog (single source of truth = GitHub issues)

To avoid a parallel doc that rots, the prioritized backlog lives as labelled
GitHub issues under the Capture → Prove → Charge epics:

| Priority | Item | Issue |
| --- | --- | --- |
| P0 | Auto-record every routed session (capture continuity) | #92 |
| P0 | License + paid distribution (revenue rail) | #95, #96, #97 |
| P1 | Bypass-resistance suite (firewall is literally true) | #94 |
| P1 | Portable / public-key receipt verification | #88 |
| P1 | Capture-rate in Mission Control | #93 |
| P2 | Share badge / growth loop | #89 |

## 7. Release verdict

**READY FOR LOCAL DEMO / ALPHA.** Tests, lint, and static security are green;
the money-handling, rendering, and command-policy paths are hardened and tested.
**Not yet ready to charge** — there is no license/billing rail (Epic C). Treat
"production for paying users" as gated on Epic C plus a live-GUI smoke pass.
